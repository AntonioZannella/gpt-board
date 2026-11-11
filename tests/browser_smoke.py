"""Disposable Chromium/WebKit workflows. Never connects to a personal server."""

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

SOURCE = Path(__file__).resolve().parents[1]
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII="


def copy_source(destination, source):
    for path in source.glob("*.py"):
        shutil.copy2(path, destination / path.name)
    shutil.copy2(source / "mcp_schema.json", destination / "mcp_schema.json")
    shutil.copytree(source / "src", destination / "src")


def run(engine, artifacts, source=SOURCE):
    with tempfile.TemporaryDirectory(prefix="board-browser-") as tmp:
        root = Path(tmp)
        copy_source(root, source)
        subprocess.run(
            [sys.executable, "build.py"], cwd=root, check=True, capture_output=True
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            **os.environ,
            "MLS_CONTROLLER": "",
            "MLS_COURSES_JSON": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        log = (artifacts / f"{engine}-server.log").open("w")
        child = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(port)],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
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
                raise AssertionError("Isolated server did not start")
            with sync_playwright() as playwright:
                browser = getattr(playwright, engine).launch()
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    accept_downloads=True,
                    record_video_dir=str(artifacts / "video"),
                )
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                page = context.new_page()
                errors = []
                tearing_down = set()
                teardown_diagnostics = []

                def watch_errors(tab):
                    def report(error):
                        detail = str(error) + "\n" + (error.stack or "")
                        # WebKit reports a caught same-origin fetch cancellation
                        # as pageerror during document replacement (Playwright #43120).
                        if (engine == "webkit" and tab in tearing_down
                                and "Fetch API cannot load " + base + "/api/board?shapes=1&" in detail
                                and "due to access control checks." in detail):
                            teardown_diagnostics.append(detail)
                        else:
                            errors.append(detail)
                    tab.on("pageerror", report)

                def reload_page(tab):
                    tearing_down.add(tab)
                    try:
                        tab.reload()
                    finally:
                        tearing_down.discard(tab)

                watch_errors(page)

                def api(path, body=None):
                    response = (
                        context.request.post(base + path, data=body)
                        if body is not None
                        else context.request.get(base + path)
                    )
                    assert response.ok, (path, response.status, response.text()[:200])
                    return response.json()

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

                def saved(tab):
                    expect(tab.locator("#status")).to_have_text(
                        __import__("re").compile(r"^Saved"), timeout=15000
                    )

                def more_action(tab, selector):
                    tab.get_by_role("button", name="More", exact=True).click()
                    tab.locator(selector).click()

                def draw(tab, offset=0):
                    box = tab.locator("#canvas").bounding_box()
                    x = box["x"] + box["width"] * 0.4
                    y = box["y"] + box["height"] * 0.6 + offset
                    tab.mouse.move(x, y)
                    tab.mouse.down()
                    tab.mouse.move(x + 70, y + 25, steps=12)
                    tab.mouse.up()

                def ink_count():
                    return sum(
                        r.get("type") == "draw"
                        for r in api("/api/board")["store"].values()
                    )

                started = time.perf_counter()
                page.goto(base)
                saved(page)
                open_ms = (time.perf_counter() - started) * 1000
                draw(page)
                saved(page)
                assert ink_count() == 1
                reload_page(page)
                saved(page)
                assert ink_count() == 1
                page.locator("#undo").click() if page.locator(
                    "#undo"
                ).is_enabled() else None
                draw(page, 40)
                saved(page)
                assert ink_count() == 2
                page.locator("#undo").click()
                saved(page)
                assert ink_count() == 1
                page.locator("#redo").click()
                saved(page)
                assert ink_count() == 2
                more_action(page, "#addpage")
                saved(page)
                assert page.locator("#pages option").count() == 2
                page.locator("#prev").click()
                more_action(page, "#manage-pages")
                page.get_by_role("button", name="Duplicate", exact=True).first.click()
                expect(page.locator("#pages option")).to_have_count(3)
                page.get_by_role("button", name="Trash", exact=True).last.click()
                expect(page.locator("#pages option")).to_have_count(2)
                page.get_by_role("button", name="Restore", exact=True).click()
                expect(page.locator("#pages option")).to_have_count(3)
                page.locator("#close-manage").click()
                saved(page)
                # One journal per live document; active tabs never consume each other's queues.
                other = context.new_page()
                other.goto(base)
                saved(other)
                before = ink_count()

                def refuse_save(route):
                    if route.request.method == "POST":
                        route.abort("internetdisconnected")
                    else:
                        route.continue_()

                page.route("**/api/board", refuse_save)
                draw(page, 75)
                expect(page.locator("#status")).to_contain_text("kept on this device", timeout=10000)
                assert ink_count() == before
                reload_page(other)
                saved(other)
                assert ink_count() == before, "live journal must not be stolen"
                tearing_down.add(page)
                page.close()
                other.close()
                page = context.new_page()
                watch_errors(page)
                page.goto(base)
                saved(page)
                assert ink_count() == before + 1, (
                    "closed-tab journal is replayed after reconnect"
                )
                # Independent device adds a stroke; missed notifications reconcile on reconnect.
                device = browser.new_context(viewport={"width": 1280, "height": 900})
                second = device.new_page()
                second.goto(base)
                saved(second)
                draw(second, 20)
                saved(second)
                reload_page(page)
                saved(page)
                assert ink_count() == before + 2
                # Explicit page targeting, durable operation receipt, protected material proposal.
                more_action(page, "#ai-panel")
                page.get_by_role(
                    "button", name="Use this tab and current page as AI target"
                ).click()
                args = {
                    "operation_id": "browser-safe-retry",
                    "shapes": [
                        {
                            "type": "note",
                            "text": "Original synthetic AI annotation: explain each step in your own words.",
                            "w": 280,
                            "h": 70,
                        }
                    ],
                }
                result = tool("add_shapes", args)
                assert not result["isError"], result
                receipt = json.loads(result["content"][0]["text"])
                assert receipt == json.loads(
                    tool("add_shapes", args)["content"][0]["text"]
                )
                page.locator("#close-manage").click()
                reload_page(page)
                saved(page)
                more_action(page, "#ai-panel")
                expect(page.get_by_label("Annotation text").filter()).not_to_have_count(
                    0
                )
                page.once("dialog", lambda dialog: dialog.accept("Student typed note"))
                page.get_by_role("button", name="Add a typed note on this page").click()
                saved(page)
                page.get_by_label("Typed note text", exact=True).last.fill(
                    "Edited student typed note"
                )
                page.get_by_role(
                    "button", name="Save typed note", exact=True
                ).last.click()
                saved(page)
                snapshot = api("/api/board")
                student = next(
                    r
                    for r in snapshot["store"].values()
                    if r.get("type") == "note" and not r.get("meta", {}).get("ai")
                )
                assert "Edited student typed note" in json.dumps(student["props"])
                protected = {
                    "operation_id": "browser-approval",
                    "notebook": "default",
                    "frame": student["parentId"],
                    "ids": [student["id"].removeprefix("shape:")],
                    "user_authorized": True,
                }
                answer = tool("delete_shapes", protected)
                assert answer["isError"]
                assert student["id"] in api("/api/board")["store"]
                page.locator("#close-manage").click()
                more_action(page, "#ai-panel")
                operation = page.locator(".manage-row").filter(
                    has=page.get_by_text("browser-approval", exact=True)
                )
                operation.get_by_role("button", name="Approve these exact changes").click()
                expect(operation.get_by_role("heading", name="committed", exact=True)).to_be_visible()
                assert student["id"] not in api("/api/board")["store"]
                operation.get_by_role("button", name="Undo this AI edit", exact=True).click()
                expect(operation.get_by_role("heading", name="undone", exact=True)).to_be_visible()
                assert student["id"] in api("/api/board")["store"]
                page.locator("#close-manage").click()
                # Hiding the annotation layer changes rendering without deleting records.
                visible = page.locator("#canvas").screenshot()
                page.locator("#show-annotations").uncheck()
                page.wait_for_timeout(100)
                hidden = page.locator("#canvas").screenshot()
                assert visible != hidden
                page.locator("#show-annotations").check()
                # Notebook JSON preview and media import/export are user workflows.
                more_action(page, "#files")
                with page.expect_download() as download:
                    page.get_by_role("button", name="Download notebook JSON").click()
                exported = json.loads(download.value.path().read_text())
                assert exported["format"] == "gpt-board-notebook"
                with page.expect_download() as download:
                    page.get_by_role(
                        "button", name="Export current page PNG (print resolution)"
                    ).click()
                png_path = artifacts / f"{engine}-page.png"
                download.value.save_as(png_path)
                from PIL import Image

                with Image.open(png_path) as image:
                    assert max(image.size) >= 2500
                page.get_by_label(
                    "Choose notebook, PDF or image to import"
                ).set_input_files(
                    {
                        "name": "original.png",
                        "mimeType": "image/png",
                        "buffer": base64.b64decode(PNG),
                    }
                )
                page.get_by_label("Import destination").select_option("append")
                page.get_by_role("button", name="Import with these settings").click()
                page.wait_for_load_state()
                saved(page)
                expect(page.locator("#pages option")).to_have_count(4)
                more_action(page, "#files")
                with page.expect_download(timeout=60000) as download:
                    page.get_by_role("button", name="Export selected pages PDF").click()
                pdf_path = artifacts / f"{engine}-notebook.pdf"
                download.value.save_as(pdf_path)
                import pypdfium2

                with pypdfium2.PdfDocument(str(pdf_path)) as pdf:
                    assert len(pdf) == 4
                page.locator("#close-manage").click()
                # Storage denied/quota errors must not break input or claim retention.
                for mode in ("denied", "quota"):
                    restricted = browser.new_context(
                        viewport={"width": 1280, "height": 900}
                    )
                    script = (
                        "Object.defineProperty(window,'localStorage',{get(){throw new DOMException('denied','SecurityError')}})"
                        if mode == "denied"
                        else "Storage.prototype.setItem=function(){throw new DOMException('full','QuotaExceededError')}"
                    )
                    restricted.add_init_script(script + ";Object.defineProperty(window,'indexedDB',{value:undefined})")
                    tab = restricted.new_page()
                    watch_errors(tab)
                    tab.goto(base)
                    saved(tab)
                    tab.route("**/api/board", refuse_save)
                    draw(tab, 55)
                    expect(tab.locator("#status")).to_contain_text(
                        "Keep this tab open", timeout=10000
                    )
                    assert (
                        "kept on this device" not in tab.locator("#status").inner_text()
                    )
                    tab.unroute("**/api/board", refuse_save)
                    saved(tab)
                    tearing_down.add(tab)
                    restricted.close()
                # A restored edit conflicting with newer server state is retained,
                # and the user can export the local version to a separate notebook.
                latest = api("/api/board")
                old = latest["store"][student["id"]]
                local = {**old, "x": old["x"] + 20}
                remote = {**old, "x": old["x"] + 40}
                rev = context.request.get(base + "/api/board").headers["x-board-rev"]
                api(
                    "/api/board",
                    {
                        "tab": "test-other-device",
                        "notebook": "default",
                        "base": rev,
                        "changes": {"put": [remote], "remove": []},
                        "summary": {"shapeCount": 1},
                    },
                )
                page.evaluate(
                    "j=>localStorage.setItem('gpt-board:journal:v2:default:crashed-conflict',JSON.stringify(j))",
                    {
                        "version": 2,
                        "notebook": "default",
                        "device": "fixture",
                        "tab": "crashed-conflict",
                        "revision": rev,
                        "operation": "crashed-conflict:1",
                        "updated": 0,
                        "entries": [
                            [
                                student["id"],
                                json.dumps(old, separators=(",", ":")),
                                json.dumps(local, separators=(",", ":")),
                            ]
                        ],
                    },
                )
                reload_page(page)
                saved(page)
                assert api("/api/board")["store"][student["id"]]["x"] == remote["x"]
                more_action(page, "#recovery")
                with page.expect_download() as recovered:
                    page.get_by_role(
                        "button",
                        name="Download a separate notebook with recovered local versions",
                    ).click()
                recovery = json.loads(recovered.value.path().read_text())
                assert recovery["snapshot"]["store"][student["id"]]["x"] == local["x"]
                page.locator("#close-manage").click()
                page.screenshot(
                    path=str(artifacts / f"{engine}-final.png"), full_page=True
                )
                assert not errors, errors
                (artifacts / f"{engine}-result.json").write_text(
                    json.dumps(
                        {
                            "engine": engine,
                            "status": "passed",
                            "first_open_ms": round(open_ms, 1),
                            "teardown_diagnostics": teardown_diagnostics,
                            "workflows": [
                                "draw/save/reload",
                                "undo/redo",
                                "pages duplicate/trash/restore",
                                "isolated journals",
                                "closed-tab recovery",
                                "second device",
                                "AI target/retry/approval/undo",
                                "JSON/PNG/PDF/image import and export",
                            ],
                        },
                        indent=2,
                    )
                )
                context.tracing.stop(path=str(artifacts / f"{engine}-trace.zip"))
                device.close()
                context.close()
                browser.close()
                print(f"{engine}: all browser smoke workflows passed")
        except BaseException:
            try:
                page.screenshot(
                    path=str(artifacts / f"{engine}-failure.png"), full_page=True
                )
                context.tracing.stop(path=str(artifacts / f"{engine}-trace.zip"))
            except Exception as exc:  # noqa: BLE001 - preserve the original browser failure
                print("Failure artifact capture unavailable:", type(exc).__name__)
            raise
        finally:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    parser.add_argument("--artifacts", type=Path, default=SOURCE / "browser-artifacts")
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    run(args.engine, args.artifacts, args.source)
