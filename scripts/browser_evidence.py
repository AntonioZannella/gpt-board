"""Synthetic browser demo and repeatable measurements, always in temporary installs."""

import argparse
import contextlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from benchmark import workload


@contextlib.contextmanager
def installation():
    with tempfile.TemporaryDirectory(prefix="board-evidence-") as tmp:
        root = Path(tmp)
        for source in ROOT.glob("*.py"):
            shutil.copy2(source, root / source.name)
        shutil.copy2(ROOT / "mcp_schema.json", root / "mcp_schema.json")
        shutil.copytree(ROOT / "src", root / "src")
        subprocess.run(
            [sys.executable, "build.py"], cwd=root, check=True, capture_output=True
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "MLS_CONTROLLER": "", "MLS_COURSES_JSON": ""}
        controller = os.environ.get("MLS_TEST_CONTROLLER")
        if controller:
            course = root / "synthetic-course"
            log = root / "synthetic-error-log.md"
            log.write_text("# Disposable synthetic demo error log\n")
            subprocess.run(
                [
                    sys.executable,
                    controller,
                    "--dir",
                    str(course),
                    "init",
                    "--course",
                    "Synthetic demo",
                    "--error-log",
                    str(log),
                ],
                check=True,
                capture_output=True,
            )
            env.update(
                MLS_CONTROLLER=controller,
                MLS_PYTHON=sys.executable,
                MLS_COURSES_JSON=json.dumps({"Synthetic demo": str(course)}),
            )
        child = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(port)],
            cwd=root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + "/api/health", timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("Disposable server unavailable")
            yield root, base
        finally:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main(mode, output):
    output.mkdir(parents=True, exist_ok=True)
    with installation() as (root, base), sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            record_video_dir=str(output / "raw-video") if mode == "demo" else None,
        )
        page = context.new_page()

        def api(path, body=None):
            response = (
                context.request.get(base + path)
                if body is None
                else context.request.post(base + path, data=body)
            )
            assert response.ok, (path, response.status)
            return response.json()

        def saved():
            expect(page.locator("#status")).to_have_text(
                __import__("re").compile("^Saved"), timeout=30000
            )

        def draw(offset=0):
            b = page.locator("#canvas").bounding_box()
            x = b["x"] + b["width"] * 0.45
            y = b["y"] + b["height"] * 0.55 + offset
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 65, y + 20, steps=12)
            page.mouse.up()

        if mode == "benchmark":
            cdp = context.new_cdp_session(page)
            cdp.send("Performance.enable")
            rows = []
            for count in (100, 1000, 10000):
                book = api(
                    "/api/notebooks",
                    {
                        "action": "create",
                        "name": f"Synthetic {count} strokes",
                        "kind": "whiteboard",
                    },
                )["notebook"]["id"]
                value = workload(count)
                rev = context.request.get(base + "/api/board?notebook=" + book).headers[
                    "x-board-rev"
                ]
                api(
                    "/api/board",
                    {
                        "notebook": book,
                        "tab": "fixture",
                        "base": rev,
                        "snapshot": value,
                        "summary": {"shapeCount": count},
                    },
                )
                start = time.perf_counter()
                page.goto(base + "/?notebook=" + book + "&section=whiteboards")
                saved()
                page.evaluate(
                    "()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))"
                )
                opened = (time.perf_counter() - start) * 1000
                intervals = page.evaluate(
                    """()=>new Promise(resolve=>{const times=[];let last=performance.now();function sample(now){times.push(now-last);last=now;if(times.length<120)requestAnimationFrame(sample);else resolve(times.slice(1));}requestAnimationFrame(sample);})"""
                )
                heap = next(
                    (
                        m["value"]
                        for m in cdp.send("Performance.getMetrics")["metrics"]
                        if m["name"] == "JSHeapUsedSize"
                    ),
                    None,
                )
                start = time.perf_counter()
                for i in range(30):
                    draw(i % 6 * 12)
                saved()
                write_ms = (time.perf_counter() - start) * 1000
                payload = len(json.dumps(value, separators=(",", ":")).encode())
                rows.append(
                    {
                        "strokes_at_open": count,
                        "payload_bytes": payload,
                        "first_open_and_two_frames_ms": round(opened, 1),
                        "idle_frame_p95_ms": round(
                            sorted(intervals)[int(len(intervals) * 0.95)], 2
                        ),
                        "js_heap_bytes": heap,
                        "30_synthetic_strokes_and_save_ms": round(write_ms, 1),
                    }
                )
            # Sustained automation and a measured recovery on the largest workload.
            # This is a synthetic session, not a physical Pencil endurance claim.
            start_heap = next(
                m["value"]
                for m in cdp.send("Performance.getMetrics")["metrics"]
                if m["name"] == "JSHeapUsedSize"
            )
            start = time.perf_counter()
            for i in range(600):
                draw(i % 6 * 12)
            saved()
            sustained_ms = (time.perf_counter() - start) * 1000
            end_heap = next(
                m["value"]
                for m in cdp.send("Performance.getMetrics")["metrics"]
                if m["name"] == "JSHeapUsedSize"
            )

            def block_saves(route):
                if route.request.method == "POST":
                    route.abort("internetdisconnected")
                else:
                    route.continue_()

            page.route("**/api/board", block_saves)
            for i in range(10):
                draw(i * 4)
            page.wait_for_function(
                "Object.keys(localStorage).some(k=>k.startsWith('gpt-board:journal:v2:'))"
            )
            start = time.perf_counter()
            page.unroute("**/api/board", block_saves)
            page.evaluate("window.dispatchEvent(new Event('online'))")
            saved()
            recovery_ms = (time.perf_counter() - start) * 1000
            final_count = sum(
                r.get("type") == "draw"
                for r in api("/api/board?notebook=" + book)["store"].values()
            )
            assert final_count == 10640, final_count
            (output / "browser-benchmark.json").write_text(
                json.dumps(
                    {
                        "platform": platform.platform(),
                        "python": platform.python_version(),
                        "browser": browser.version,
                        "workloads": rows,
                        "sustained_session": {
                            "starting_strokes": 10030,
                            "added_strokes": 600,
                            "elapsed_ms": round(sustained_ms, 1),
                            "js_heap_start_bytes": start_heap,
                            "js_heap_end_bytes": end_heap,
                            "blocked_save_strokes": 10,
                            "reconnect_until_saved_ms": round(recovery_ms, 1),
                            "verified_final_strokes": final_count,
                        },
                        "limits": "One run per workload; Chromium CDP JS heap and idle rAF intervals, not input latency, physical Pencil or long-session certification. No baseline speedup comparison.",
                    },
                    indent=2,
                )
            )
        else:
            began = time.perf_counter()
            captions = []

            def caption(text):
                captions.append((time.perf_counter() - began, text))

            page.goto(base)
            saved()
            caption(
                "GPT Board 1.3.0rc1 — original synthetic sample, disposable installation."
            )
            page.wait_for_timeout(1400)
            draw()
            saved()
            caption("Write locally, wait for Saved, then reopen the page.")
            page.screenshot(path=str(output / "01-writing.png"))
            page.wait_for_timeout(1400)
            page.reload()
            saved()
            page.locator("#addpage").click()
            saved()
            draw()
            saved()
            frame = page.locator("#pages").input_value()
            caption(
                "A new page retains stable identity. The next annotation uses the MCP API."
            )
            page.wait_for_timeout(1200)
            token = (root / "data/token").read_text().strip()

            def tool(name, args):
                return api(
                    "/mcp/" + token,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": args},
                    },
                )["result"]

            read = tool("read_board", {"notebook": "default", "frame": frame})
            summary = json.loads(read["content"][-1]["text"])
            result = tool(
                "add_shapes",
                {
                    "notebook": "default",
                    "frame": frame,
                    "operation_id": "synthetic-demo-annotation",
                    "shapes": [
                        {
                            "text": "Synthetic annotation: explain each step in your own words.",
                            "type": "note",
                        }
                    ],
                },
            )
            assert not result["isError"]
            page.wait_for_timeout(1800)
            caption(
                "The synthetic MCP client adds a page-relative note; its receipt makes retries safe."
            )
            page.screenshot(path=str(output / "02-annotation.png"))
            page.reload()
            saved()
            page.locator("#ai-panel").click()
            page.wait_for_timeout(1200)
            caption(
                "Inspect the saved before/after records and use Undo for a reversible AI edit."
            )
            page.screenshot(path=str(output / "03-receipt.png"))
            if os.environ.get("MLS_TEST_CONTROLLER"):
                check = summary["review_context"]["check_id"]
                reviewed = tool(
                    "record_review",
                    {
                        "check_id": check,
                        "course": "Synthetic demo",
                        "section": "demo",
                        "page": "2",
                        "problem": "fixture",
                        "original_response": "Synthetic drawing only; no mathematical answer to assess.",
                        "result": "unresolved",
                        "read_confidence": "low",
                        "assistance": "unknown",
                        "errors": [],
                    },
                )
                assert not reviewed["isError"], reviewed["content"][0]["text"]
                page.get_by_role(
                    "button", name="View canonical learning review history"
                ).click()
                page.get_by_role("button", name="Synthetic demo", exact=True).click()
                page.wait_for_timeout(1000)
                caption(
                    "A real receipt in a temporary course: unresolved, help unknown. No personal learning record or mastery claim."
                )
                page.screenshot(path=str(output / "04-review.png"))
            page.wait_for_timeout(2800)
            page.locator("#close-manage").click()
            page.reload()
            saved()
            caption(
                "Reload preserves writing and annotation. Physical iPad/Pencil validation is still required."
            )
            page.wait_for_timeout(2000)
            ended = time.perf_counter() - began
            video = page.video
            page.close()
            video.save_as(str(output / "demo.webm"))

            def stamp(seconds):
                ms = int(seconds * 1000)
                return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}"

            vtt = "WEBVTT\n\n"
            for i, (start, text) in enumerate(captions):
                vtt += f"{stamp(start)} --> {stamp(captions[i + 1][0] if i + 1 < len(captions) else ended)}\n{text}\n\n"
            (output / "demo.vtt").write_text(vtt)
            (output / "index.html").write_text(
                """<!doctype html><html lang="en"><meta charset="utf-8"><title>GPT Board local candidate demo</title><style>body{max-width:1000px;margin:40px auto;padding:0 20px;font:18px system-ui;color:#21304b}video,img{width:100%}figure{margin:40px 0}figcaption{padding:12px 0}</style><h1>GPT Board 1.3.0rc1</h1><p>Original synthetic material in an isolated installation. The annotation client is simulated; the app and recorded controller receipt are real. No personal notebook or learning record was used.</p><video controls><source src="demo.webm" type="video/webm"><track default kind="captions" src="demo.vtt" srclang="en" label="English"></video><p>Download <a href="demo.webm">video</a> or <a href="demo.vtt">captions</a>. If local caption loading is restricted, use the captioned figures below.</p><figure><img src="01-writing.png" alt="Original synthetic writing saved in the Welcome notebook"><figcaption>Write, wait for Saved, and reload.</figcaption></figure><figure><img src="02-annotation.png" alt="A synthetic AI note within the selected page"><figcaption>Page-relative annotation with a durable retry receipt.</figcaption></figure><figure><img src="03-receipt.png" alt="Inspectable AI change receipt and undo control"><figcaption>Inspect the saved change and undo it without overwriting later edits.</figcaption></figure><figure><img src="04-review.png" alt="Unresolved review from the temporary synthetic course"><figcaption>Canonical review receipt: unresolved, assistance unknown. No mastery is inferred.</figcaption></figure><p>Physical device checks and Antonio's personal contribution confirmation remain open.</p></html>"""
            )
        context.close()
        browser.close()
    print(mode, "evidence saved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "benchmark"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.mode, args.output)
