"""Bounded, cancellable import staging and raster PDF export."""

import base64
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from board_storage import (
    LOCK,
    ROOT,
    ClientError,
    atomic_write,
    decode_png,
    read_json,
    write_json,
)
from validation import snapshot as validate_snapshot

WORKERS = threading.BoundedSemaphore(2)


def worker(action, source, output, page=0):
    if not WORKERS.acquire(blocking=False):
        raise ClientError(
            "Two media jobs are already running. Wait or cancel one before retrying."
        )
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "media_worker.py"),
                action,
                str(source),
                str(output),
                str(page),
            ],
            capture_output=True,
            timeout=120 if action == "pdf-files" else 30,
            check=False,
        )
        data = json.loads(result.stdout) if len(result.stdout) <= 1_000_000 else {}
        if not data:
            raise ClientError("Media metadata exceeds the supported limit.")
        if result.returncode or data.get("error"):
            raise ClientError(data.get("error", "Media conversion failed."))
        return data
    except (subprocess.TimeoutExpired, ValueError):
        raise ClientError(
            "Media conversion timed out or failed. Try a smaller file."
        ) from None
    finally:
        WORKERS.release()


class Media:
    def __init__(self, storage):
        self.storage = storage
        self.folder = storage.data / "import-staging"
        self.lock = threading.RLock()

    def job(self, job_id):
        if (
            not isinstance(job_id, str)
            or len(job_id) != 32
            or any(c not in "0123456789abcdef" for c in job_id)
        ):
            raise ClientError("Invalid import job.")
        folder = self.folder / job_id
        info = read_json(folder / "job.json")
        if not info or info.get("expires", 0) < time.time():
            raise ClientError("Import expired or was cancelled. Choose the file again.")
        return folder, info

    def prepare(self, name, encoded):
        suffix = Path(str(name)).suffix.lower()
        if (
            suffix not in (".pdf", ".png", ".jpg", ".jpeg")
            or not isinstance(encoded, str)
            or len(encoded) > 23_000_000
        ):
            raise ClientError("Choose a PDF, PNG or JPEG file up to 16 MiB.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ClientError("Invalid uploaded file encoding.") from None
        if not raw or len(raw) > 16 * 1024**2:
            raise ClientError("Choose a file up to 16 MiB.")
        with self.lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            for folder in self.folder.iterdir():
                info = read_json(folder / "job.json", {}) if folder.is_dir() else {}
                if folder.is_dir() and info.get("expires", 0) < time.time():
                    shutil.rmtree(folder)
            if len(list(self.folder.iterdir())) >= 2:
                raise ClientError(
                    "Two imports are already staged. Cancel them or wait one hour for expiry."
                )
            job_id = secrets.token_hex(16)
            folder = self.folder / job_id
            folder.mkdir()
            info = {
                "id": job_id,
                "expires": time.time() + 3600,
                "suffix": suffix,
                "name": Path(name).stem[:80],
            }
            write_json(folder / "job.json", info)
            atomic_write(folder / ("source" + suffix), raw)
        try:
            info.update(
                worker("inspect", folder / ("source" + suffix), folder / "unused.png")
            )
            write_json(folder / "job.json", info)
            return {"id": job_id, "pages": info["pages"]}
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def render(self, job_id, page):
        folder, info = self.job(job_id)
        if (
            not isinstance(page, int)
            or isinstance(page, bool)
            or not 0 <= page < info["pages"]
        ):
            raise ClientError("Invalid import page.")
        output = folder / f"{page}.png"
        cached = read_json(folder / f"{page}.json")
        if output.is_file() and cached:
            return cached
        result = worker("render", folder / ("source" + info["suffix"]), output, page)
        # Cancellation can remove the job while a decoder runs. It never
        # touches notebook files; commit requires the still-live job again.
        self.job(job_id)
        if sum(p.stat().st_size for p in folder.glob("*.png")) > 32 * 1024**2:
            raise ClientError(
                "Rendered import exceeds 32 MiB. Split the document into smaller imports."
            )
        write_json(folder / f"{page}.json", result)
        return result

    def cancel(self, job_id):
        with self.lock:
            folder, _ = self.job(job_id)
            shutil.rmtree(folder)
        return {"cancelled": True}

    def commit(self, job_id, notebook, base, targets=None):
        with self.lock, LOCK:
            self.storage.require(notebook)
            snapshot = self.storage.read(notebook)
            if job_id in snapshot.get("_imports", {}):
                return snapshot["_imports"][job_id]
            folder, info = self.job(job_id)
            if base != self.storage.revision(notebook):
                raise ClientError(
                    "Notebook changed during import. Reopen it and retry; staged pages are retained."
                )
            records = snapshot["store"]
            replaced_assets = set()
            if targets is not None:
                if (not isinstance(targets, list) or len(targets) != info["pages"]
                        or any(not isinstance(t, str) for t in targets)
                        or len(set(targets)) != len(targets)):
                    raise ClientError("Map every imported page to one distinct existing page.")
                for target in targets:
                    frame = records.get(target, {})
                    pictures = [r for r in records.values() if r.get("parentId") == target
                                and r.get("type") == "image" and r.get("isLocked")]
                    if frame.get("type") != "frame" or frame.get("meta", {}).get("trashed") or len(pictures) != 1:
                        raise ClientError("Replacement requires a live page with exactly one locked background.")
                    replaced_assets.add(pictures[0]["props"].get("assetId"))
            page_id = next(
                r["id"] for r in records.values() if r.get("typeName") == "page"
            )
            y = (
                max(
                    (
                        r.get("y", 0) + r.get("props", {}).get("h", 0)
                        for r in records.values()
                        if r.get("type") == "frame"
                    ),
                    default=-80,
                )
                + 80
            )
            added = []
            for page in range(info["pages"]):
                dims = read_json(folder / f"{page}.json")
                image = folder / f"{page}.png"
                if not dims or not image.is_file():
                    raise ClientError("Render every page before committing the import.")
                asset, frame, picture = [
                    prefix + ":" + job_id + "-" + str(page)
                    for prefix in ("asset", "shape", "shape:image")
                ]
                if targets is not None:
                    frame = targets[page]
                    old_picture = next(r for r in records.values() if r.get("parentId") == frame
                                       and r.get("type") == "image" and r.get("isLocked"))
                    picture = old_picture["id"]
                    fw, fh = records[frame]["props"]["w"], records[frame]["props"]["h"]
                    if abs((dims["width"] / dims["height"]) / (fw / fh) - 1) > 0.02:
                        raise ClientError("Replacement page proportions differ. Import as a new notebook to avoid misaligning handwriting.")
                w = records[frame]["props"]["w"] if targets is not None else 1000
                h = round(w * dims["height"] / dims["width"])
                records[asset] = {
                    "id": asset,
                    "typeName": "asset",
                    "type": "image",
                    "props": {
                        "src": "data:image/png;base64,"
                        + base64.b64encode(image.read_bytes()).decode(),
                        "w": dims["width"],
                        "h": dims["height"],
                    },
                }
                new_frame = {
                    "id": frame,
                    "typeName": "shape",
                    "type": "frame",
                    "parentId": page_id,
                    "x": 0,
                    "y": y,
                    "props": {
                        "w": w,
                        "h": h,
                        "name": info["name"] + " " + str(page + 1),
                    },
                    "meta": {},
                }
                if targets is None:
                    records[frame] = new_frame
                else:
                    h = records[frame]["props"]["h"]
                records[picture] = {
                    "id": picture,
                    "typeName": "shape",
                    "type": "image",
                    "parentId": frame,
                    "x": 0,
                    "y": 0,
                    "isLocked": True,
                    "props": {"w": w, "h": h, "assetId": asset},
                    "meta": {"pdfLinks": [
                        {"rect": link["rect"], **(
                            {"frame": targets[link["page"]] if targets is not None else "shape:" + job_id + "-" + str(link["page"])}
                            if "page" in link else {"url": link["url"]}
                        )} for link in dims.get("links", [])
                    ]},
                }
                y += h + 80
                added.append(frame)
            # Only replaced, unreferenced background assets can be removed; ink/IDs/history stay.
            if targets is not None:
                used = {r.get("props", {}).get("assetId") for r in records.values()}
                for key in replaced_assets:
                    if key in records and records[key].get("typeName") == "asset" and key not in used:
                        del records[key]
            validate_snapshot(snapshot)
            result = {"ok": True, "pages": added}
            snapshot.setdefault("_imports", {})[job_id] = result
            self.storage.save(notebook, snapshot, {"shapeCount": len(records)})
            return result

    def export_pdf(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "pages.json", Path(tmp) / "export.pdf"
            write_json(source, body)
            worker("pdf", source, output)
            if output.stat().st_size > 48 * 1024**2:
                raise ClientError(
                    "PDF exceeds the 48 MiB export limit. Export fewer pages."
                )
            return output.read_bytes()

    def export_stage(self, body):
        action = body.get("action")
        with self.lock:
            if action == "begin":
                self.folder.mkdir(parents=True, exist_ok=True)
                for old in self.folder.glob("*/job.json"):
                    if read_json(old, {}).get("expires", 0) < time.time():
                        shutil.rmtree(old.parent)
                if len(list(self.folder.iterdir())) >= 2:
                    raise ClientError("Two media jobs are active. Cancel one before starting another.")
                job = secrets.token_hex(16)
                folder = self.folder / job
                folder.mkdir()
                write_json(folder / "job.json", {"kind": "export", "expires": time.time() + 3600})
                return {"id": job}
            folder, info = self.job(body.get("job"))
            if info.get("kind") != "export":
                raise ClientError("Not an export job.")
            if action == "cancel":
                shutil.rmtree(folder)
                return {"cancelled": True}
            if action == "page":
                page = body.get("page")
                if type(page) is not int or not 0 <= page < 500:
                    raise ClientError("Export supports up to 500 pages.")
                raw = decode_png(body.get("png"))
                if sum(p.stat().st_size for p in folder.glob("*.png")) + len(raw) > 192 * 1024**2:
                    raise ClientError("Export staging exceeds 192 MiB. Select a smaller page range.")
                atomic_write(folder / f"{page}.png", raw)
                write_json(folder / f"{page}.json", {"file": str(folder / f"{page}.png"), "w": body.get("w"), "h": body.get("h")})
                return {"ok": True}
            if action == "finish":
                count = body.get("count")
                if type(count) is not int or not 1 <= count <= 500:
                    raise ClientError("Export supports 1–500 pages.")
                pages = [read_json(folder / f"{i}.json") for i in range(count)]
                if not all(pages):
                    raise ClientError("An export page is missing. Retry the export.")
                source, output = folder / "export.json", folder / "export.pdf"
                write_json(source, {"pages": pages})
                worker("pdf-files", source, output)
                if output.stat().st_size > 48 * 1024**2:
                    raise ClientError("PDF exceeds 48 MiB. Export a smaller page range.")
                return output.read_bytes()
            raise ClientError("Unsupported export action.")
