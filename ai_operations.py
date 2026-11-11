"""AI edits commit with their receipt in one authoritative notebook snapshot.

Retries with the same operation ID and request return the recorded result, even
when the HTTP response was lost. Human approval happens only through the board
UI route; no model argument grants approval. Receipt history is never pruned.
"""

import copy
import hashlib
import re
import time

from board_storage import LOCK, ClientError, check_shape, dumps, finite

MAX_OPERATIONS = 5000


def rich(text):
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}] if line else [],
            }
            for line in str(text).split("\n")
        ],
    }


def frame_for(records, value):
    frames = [
        s
        for s in records.values()
        if s.get("type") == "frame" and not s.get("meta", {}).get("trashed")
    ]
    matches = [s for s in frames if s["id"] in (value, "shape:" + str(value))]
    if not matches:
        matches = [
            s
            for s in frames
            if s["props"].get("name", "").lower() == str(value).lower()
        ]
    if len(matches) != 1:
        raise ClientError(
            "Choose one stable page ID; this page is missing or its name is ambiguous."
        )
    return matches[0]


def on_page(shape, frame_id, records):
    seen = set()
    while shape and shape.get("typeName") == "shape" and shape["id"] not in seen:
        if shape["id"] == frame_id:
            return True
        seen.add(shape["id"])
        shape = records.get(shape.get("parentId"))
    return False


def may_edit(shape):
    return (
        shape.get("meta", {}).get("ai") is True
        and shape["type"] not in ("frame", "image")
        and not shape.get("isLocked")
    )


def simple_box(shape, records, frame):
    """Conservative collision box in page coordinates, including packed ink."""
    import base64
    import struct

    points = []
    if shape["type"] in ("draw", "highlight"):
        for segment in shape["props"].get("segments", []):
            if "points" in segment:
                points.extend((p["x"], p["y"]) for p in segment["points"])
            elif segment.get("path"):
                raw = base64.b64decode(segment["path"])
                dim = segment.get("dim", 3)
                values = list(struct.unpack("<" + "f" * dim, raw[: dim * 4]))
                points.append(tuple(values[:2]))
                for delta in struct.iter_unpack("<" + "e" * dim, raw[dim * 4 :]):
                    values = [a + b for a, b in zip(values, delta)]
                    points.append(tuple(values[:2]))
    if shape["type"] == "arrow":
        points = [
            (shape["props"][key]["x"], shape["props"][key]["y"])
            for key in ("start", "end")
        ]
    x, y = shape.get("x", 0), shape.get("y", 0)
    parent = records.get(shape.get("parentId"))
    for _ in range(24):
        if (
            not parent
            or parent["id"] == frame["id"]
            or parent.get("typeName") != "shape"
        ):
            break
        x += parent.get("x", 0)
        y += parent.get("y", 0)
        parent = records.get(parent.get("parentId"))
    if parent is None or parent.get("typeName") == "page":
        x -= frame.get("x", 0)
        y -= frame.get("y", 0)
    if points:
        left, top = min(p[0] for p in points), min(p[1] for p in points)
        return (
            x + left - 4,
            y + top - 4,
            max(p[0] for p in points) - left + 8,
            max(p[1] for p in points) - top + 8,
        )
    return (x, y, shape["props"].get("w", 200), shape["props"].get("h", 100))


def overlaps(a, b):
    x, y, w, h = a
    bx, by, bw, bh = b
    return x < bx + bw + 8 and bx < x + w + 8 and y < by + bh + 8 and by < y + h + 8


class AIOperations:
    def __init__(self, storage):
        self.storage = storage

    def execute(self, notebook, action, args, *, human=False):
        with LOCK:
            notebook = self.storage.require(notebook)
            snapshot = self.storage.read(notebook)
            receipts = snapshot.setdefault("_operations", {})
            op_id = args.get("operation_id")
            if not isinstance(op_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{8,100}", op_id
            ):
                raise ClientError(
                    "Provide a stable operation_id (8–100 letters, digits, hyphens or underscores); reuse it on retries."
                )
            request = {
                k: v
                for k, v in args.items()
                if k not in ("user_authorized", "notebook", "tab")
            }
            identity = hashlib.sha256(
                dumps([notebook, action, request]).encode()
            ).hexdigest()
            old = receipts.get(op_id)
            if old:
                if old["identity"] != identity:
                    raise ClientError(
                        "This operation_id belongs to a different request. Reuse the original payload or choose a new ID."
                    )
                if old["status"] != "approval_required" or not human:
                    return old["result"]
                if time.time() > old["expires"]:
                    raise ClientError(
                        "Approval expired. Ask for a new proposal and inspect it again."
                    )
                # The proposal is bound to exactly the writing the user saw.
                current = hashlib.sha256(dumps(snapshot["store"]).encode()).hexdigest()
                if current != old["store_hash"]:
                    raise ClientError(
                        "The page changed after this proposal. Request a fresh proposal before approving."
                    )
            elif len(receipts) >= MAX_OPERATIONS:
                raise ClientError(
                    "This notebook has 5000 AI receipts. Export it and start a new notebook; history was preserved."
                )
            records = snapshot["store"]
            frame = (
                frame_for(records, args.get("frame"))
                if args.get("frame") not in (None, "whiteboard")
                else None
            )
            if not frame:
                if any(s.get("type") == "frame" for s in records.values()):
                    raise ClientError("Choose a stable frame ID for this AI edit.")
                if args.get("frame") != "whiteboard":
                    raise ClientError("For a whiteboard, use frame: whiteboard.")
            if (
                args.get("base_revision")
                and not old
                and args["base_revision"] != self.storage.revision(notebook)
            ):
                raise ClientError(
                    "The target revision is stale. Read the page again before editing."
                )
            items = args.get("ids" if action == "delete" else "shapes")
            if not isinstance(items, list) or not 1 <= len(items) <= 100:
                raise ClientError("Provide between 1 and 100 shapes or IDs.")
            targets = []
            if action != "add":
                for item in items:
                    short = (
                        item
                        if action == "delete"
                        else item.get("id")
                        if isinstance(item, dict)
                        else None
                    )
                    key = (
                        short
                        if isinstance(short, str) and short.startswith("shape:")
                        else "shape:" + str(short)
                    )
                    shape = records.get(key)
                    if not shape or shape.get("typeName") != "shape":
                        raise ClientError("An edit target no longer exists.")
                    if shape["type"] in ("frame", "image") or shape.get("isLocked"):
                        raise ClientError(
                            "AI cannot change pages, imported images or locked shapes."
                        )
                    if frame and not on_page(shape, frame["id"], records):
                        raise ClientError(
                            "An edit target is outside the selected page."
                        )
                    targets.append(shape)
                if len({s["id"] for s in targets}) != len(targets):
                    raise ClientError("Duplicate edit targets.")
            where = {
                "notebook": notebook,
                "frame": frame["id"] if frame else "whiteboard",
            }
            result = {
                "ok": True,
                "status": "committed",
                "operation_id": op_id,
                "target": where,
                "done": [],
            }
            if (args.get("preview") is True or any(not may_edit(s) for s in targets)) and not human:
                result.update(
                    ok=False,
                    status="approval_required",
                    error="Inspect and approve this exact proposal in the board AI panel.",
                )
                receipts[op_id] = {
                    "identity": identity,
                    "status": "approval_required",
                    "action": action,
                    "request": request,
                    "store_hash": hashlib.sha256(dumps(records).encode()).hexdigest(),
                    "expires": time.time() + 600,
                    "result": result,
                }
                self.storage.save(notebook, snapshot, {"shapeCount": len(records)})
                return result
            before, after = {}, {}
            for index, item in enumerate(items):
                if action == "delete":
                    shape = targets[index]
                    key = shape["id"]
                    before[key] = copy.deepcopy(shape)
                    after[key] = None
                    records.pop(key)
                else:
                    if not isinstance(item, dict):
                        raise ClientError("Each shape must be an object.")
                    if action == "update":
                        shape = targets[index]
                        key = shape["id"]
                        before[key] = copy.deepcopy(shape)
                    else:
                        short = (
                            item.get("id")
                            or "ai-"
                            + hashlib.sha256(f"{op_id}:{index}".encode()).hexdigest()[
                                :24
                            ]
                        )
                        if not isinstance(short, str) or not re.fullmatch(
                            r"[\w-]{1,100}", short
                        ):
                            raise ClientError(
                                "Shape IDs must use letters, digits, underscores or hyphens."
                            )
                        key = "shape:" + short
                        if key in records:
                            raise ClientError("An added shape ID already exists.")
                        kind = item.get("type", "text")
                        if kind not in (
                            "draw",
                            "highlight",
                            "text",
                            "note",
                            "arrow",
                            "rectangle",
                            "ellipse",
                            "triangle",
                            "diamond",
                        ):
                            raise ClientError("Unsupported AI shape type.")
                        text = item.get("text", "")
                        if not isinstance(text, str) or len(text) > 12000:
                            raise ClientError(
                                "Annotation text must be at most 12000 characters."
                            )
                        width = item.get("w", 280)
                        height = (
                            item.get(
                                "h",
                                max(
                                    70,
                                    28
                                    * (
                                        len(text) // max(1, int(width / 12))
                                        + len(text.split("\n"))
                                    ),
                                ),
                            )
                            if finite(width) and width > 0
                            else 100
                        )
                        shape = {
                            "id": key,
                            "typeName": "shape",
                            "type": kind
                            if kind in ("text", "note", "arrow", "draw", "highlight")
                            else "geo",
                            "x": 24,
                            "y": 24,
                            "rotation": 0,
                            "opacity": 1,
                            "index": "z" + str(time.time_ns()),
                            "parentId": frame["id"]
                            if frame
                            else next(
                                r["id"]
                                for r in records.values()
                                if r.get("typeName") == "page"
                            ),
                            "meta": {"ai": True, "operation_id": op_id},
                            "props": {
                                "w": width,
                                "h": height,
                                "geo": kind,
                                "color": "blue",
                                "size": "m",
                                "richText": rich(text),
                                "fill": "none",
                            },
                        }
                        before[key] = None
                    for coord in ("x", "y"):
                        if coord in item:
                            if not finite(item[coord]):
                                raise ClientError(
                                    "Annotation coordinates must be finite."
                                )
                            shape[coord] = item[coord]
                    for prop in ("w", "h", "color", "size"):
                        if prop in item:
                            shape["props"][prop] = item[prop]
                    if "text" in item:
                        if (
                            not isinstance(item["text"], str)
                            or len(item["text"]) > 12000
                        ):
                            raise ClientError(
                                "Annotation text must be at most 12000 characters."
                            )
                        shape["props"]["richText"] = rich(item["text"])
                    if shape["type"] == "arrow":
                        shape["props"].update(
                            start={"x": 0, "y": 0},
                            end={"x": shape["props"]["w"], "y": shape["props"]["h"]},
                        )
                    if shape["type"] == "arrow" and ("from" in item or "to" in item):
                        if not frame or not item.get("from") or not item.get("to"):
                            raise ClientError(
                                "Arrow connections need a page and both from/to shape IDs."
                            )
                        ends = []
                        for ref in (item["from"], item["to"]):
                            target = records.get(
                                "shape:" + str(ref).removeprefix("shape:")
                            )
                            if not target or not on_page(target, frame["id"], records):
                                raise ClientError(
                                    "Arrow target is missing or outside the selected page."
                                )
                            bx, by, bw, bh = simple_box(target, records, frame)
                            ends.append((bx + bw / 2, by + bh / 2))
                        shape["x"], shape["y"] = ends[0]
                        dx, dy = ends[1][0] - ends[0][0], ends[1][1] - ends[0][1]
                        shape["props"].update(
                            w=max(1, abs(dx)),
                            h=max(1, abs(dy)),
                            start={"x": 0, "y": 0},
                            end={"x": dx, "y": dy},
                        )
                    if shape["type"] in ("draw", "highlight"):
                        if "segments" in item:
                            shape["props"]["segments"] = copy.deepcopy(item["segments"])
                        if not shape["props"].get("segments"):
                            raise ClientError("Pen paths require nonempty segments with points.")
                        shape["props"]["isPen"] = True
                        shape["props"]["isComplete"] = True
                    check_shape(shape)
                    if frame:
                        fw, fh = frame["props"]["w"], frame["props"]["h"]
                        w, h = (
                            shape["props"].get("w", 200),
                            shape["props"].get("h", 100),
                        )
                        occupied = [
                            simple_box(r, records, frame)
                            for r in records.values()
                            if r.get("typeName") == "shape"
                            and r["id"] not in (key, frame["id"])
                            and r["type"] != "image"
                            and on_page(r, frame["id"], records)
                        ]
                        if (
                            action == "add"
                            and "x" not in item
                            and "y" not in item
                            and "from" not in item
                        ):
                            spot = next(
                                (
                                    (x, y)
                                    for y in range(24, int(fh - h), 32)
                                    for x in range(24, int(fw - w), 40)
                                    if not any(
                                        overlaps((x, y, w, h), box) for box in occupied
                                    )
                                ),
                                None,
                            )
                            if not spot:
                                raise ClientError(
                                    "No clear annotation space on this page. Add a page or specify a position after inspecting the page."
                                )
                            shape["x"], shape["y"] = spot
                        box = simple_box(shape, records, frame)
                        if (
                            box[0] < 0
                            or box[1] < 0
                            or box[0] + box[2] > fw
                            or box[1] + box[3] > fh
                        ):
                            raise ClientError(
                                "Annotation extends beyond the selected page. Reduce its size or move it inside."
                            )
                        if any(overlaps(box, other) for other in occupied):
                            result.setdefault("warnings", []).append(
                                "Explicit placement overlaps existing material; inspect the annotation."
                            )
                    records[key] = shape
                    after[key] = copy.deepcopy(shape)
                result["done"].append(key.removeprefix("shape:"))
            receipts[op_id] = {
                "identity": identity,
                "status": "committed",
                "result": result,
                "before": before,
                "after": after,
                "human_approved": human,
                "created": time.time(),
            }
            self.storage.save(notebook, snapshot, {"shapeCount": len(records)})
            return result

    def list(self, notebook):
        operations = self.storage.read(self.storage.require(notebook)).get(
            "_operations", {}
        )
        return [{"operation_id": key, **value} for key, value in operations.items()]

    def approve(self, notebook, op_id):
        with LOCK:
            entry = (
                self.storage.read(self.storage.require(notebook))
                .get("_operations", {})
                .get(op_id)
            )
            if not entry or entry["status"] != "approval_required":
                raise ClientError("This proposal is no longer awaiting approval.")
            return self.execute(notebook, entry["action"], entry["request"], human=True)

    def undo(self, notebook, op_id):
        with LOCK:
            snapshot = self.storage.read(self.storage.require(notebook))
            entry = snapshot.get("_operations", {}).get(op_id)
            if not entry or entry["status"] != "committed":
                raise ClientError("No committed operation to undo.")
            records = snapshot["store"]
            if any(records.get(key) != after for key, after in entry["after"].items()):
                raise ClientError(
                    "These shapes changed after the AI edit. Undo refused to preserve newer work."
                )
            for key, before in entry["before"].items():
                if before is None:
                    records.pop(key, None)
                else:
                    records[key] = before
            entry["status"] = "undone"
            entry["result"]["status"] = "undone"
            self.storage.save(notebook, snapshot, {"shapeCount": len(records)})
            return entry["result"]
