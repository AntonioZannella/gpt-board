"""Bound the record model before it reaches Canvas or native decoders."""

import base64
import binascii
import math
import struct

from board_storage import ClientError, finite

MAX_RECORDS = 50000
MAX_DEPTH = 24
MAX_POINTS = 32000
MAX_IMAGE_PIXELS = 24_000_000
SHAPE_TYPES = {
    "frame",
    "image",
    "draw",
    "highlight",
    "text",
    "note",
    "geo",
    "arrow",
    "group",
}


def bounded_tree(value):
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH or count > 2_000_000:
            raise ClientError(
                "Record nesting or item count exceeds the supported limit."
            )
        if isinstance(item, float) and not math.isfinite(item):
            raise ClientError("Record numbers must be finite.")
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)


def stroke(segment):
    if not isinstance(segment, dict):
        raise ClientError("Invalid stroke segment.")
    points = segment.get("points")
    if points is not None:
        if not isinstance(points, list) or len(points) > MAX_POINTS:
            raise ClientError("Stroke exceeds the point limit.")
        for point in points:
            if not isinstance(point, dict) or not all(
                finite(point.get(k)) for k in ("x", "y")
            ):
                raise ClientError("Invalid stroke point.")
            if "z" in point and not finite(point["z"]):
                raise ClientError("Invalid stroke pressure.")
        return len(points)
    value = segment.get("path", "")
    dim = segment.get("dim", 3)
    if (
        dim not in (2, 3)
        or not isinstance(value, str)
        or len(value) > MAX_POINTS * 8 + 24
    ):
        raise ClientError("Invalid packed stroke.")
    if not value:
        return 0
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) < dim * 4 or (len(raw) - dim * 4) % (dim * 2):
            raise ValueError()
        values = list(struct.unpack("<" + "f" * dim, raw[: dim * 4]))
        if not all(math.isfinite(n) for n in values):
            raise ValueError()
        for delta in struct.iter_unpack("<" + "e" * dim, raw[dim * 4 :]):
            values = [a + b for a, b in zip(values, delta)]
            if not all(math.isfinite(n) and abs(n) <= 10_000_000 for n in values):
                raise ValueError()
        return 1 + (len(raw) - dim * 4) // (dim * 2)
    except (ValueError, binascii.Error, struct.error):
        raise ClientError("Malformed or non-finite packed stroke.") from None


def shape_details(shape):
    bounded_tree(shape)
    if shape["type"] not in SHAPE_TYPES:
        raise ClientError("Unsupported shape type.")
    props = shape["props"]
    if "meta" in shape and not isinstance(shape["meta"], dict):
        raise ClientError("Shape metadata must be an object.")
    for key in ("scale", "scaleX", "scaleY"):
        if key in props and (not finite(props[key]) or abs(props[key]) > 10000):
            raise ClientError("Shape scale must be a finite supported number.")
    for key in ("color", "size", "geo", "text", "name", "altText"):
        if key in props and (
            not isinstance(props[key], str) or len(props[key]) > 12000
        ):
            raise ClientError("Shape text/style fields must be bounded strings.")
    nodes = [props["richText"]] if "richText" in props else []
    while nodes:
        node = nodes.pop()
        if isinstance(node, str):
            continue
        if not isinstance(node, dict) or (
            "text" in node and not isinstance(node["text"], str)
        ):
            raise ClientError("Invalid rich text node.")
        if "content" in node:
            if not isinstance(node["content"], list):
                raise ClientError("Rich text content must be an array.")
            nodes.extend(node["content"])
    for key in ("w", "h"):
        if key in props and (not finite(props[key]) or not 0 < props[key] <= 100000):
            raise ClientError("Shape dimensions must be positive and at most 100000.")
    for key in ("x", "y"):
        if key in shape and abs(shape[key]) > 10_000_000:
            raise ClientError("Shape position exceeds the supported range.")
    if "opacity" in shape and not 0 <= shape["opacity"] <= 1:
        raise ClientError("Opacity must be between zero and one.")
    if shape["type"] in ("draw", "highlight"):
        segments = props.get("segments", [])
        if not isinstance(segments, list) or len(segments) > 2000:
            raise ClientError("Invalid stroke segments.")
        if sum(stroke(segment) for segment in segments) > MAX_POINTS:
            raise ClientError(
                "A stroke exceeds the 32000 point limit. Split long strokes."
            )
    if shape["type"] == "arrow":
        for key in ("start", "end"):
            point = props.get(key)
            if not isinstance(point, dict) or not all(
                finite(point.get(k)) for k in ("x", "y")
            ):
                raise ClientError("Arrows need finite start and end points.")
    if shape["type"] == "frame" and not isinstance(props.get("name", ""), str):
        raise ClientError("Page names must be text.")


def snapshot(value):
    from board_storage import check_parents, check_shape, decode_png

    if (
        not isinstance(value, dict)
        or not isinstance(value.get("store"), dict)
        or not isinstance(value.get("schema"), dict)
    ):
        raise ClientError("Notebook must contain a record store and schema.")
    for name in ("_operations", "_imports"):
        if name in value and (
            not isinstance(value[name], dict) or len(value[name]) > 5000
        ):
            raise ClientError("Invalid operation or import history.")
    for entry in value.get("_operations", {}).values():
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("result"), dict)
            or not isinstance(entry.get("status"), str)
        ):
            raise ClientError("Invalid AI receipt history.")
    records = value["store"]
    if len(records) > MAX_RECORDS:
        raise ClientError("Notebook exceeds the 50000 record limit.")
    shapes = []
    pages = 0
    for key, record in records.items():
        if not isinstance(record, dict) or record.get("id") != key:
            raise ClientError("Record key and ID must agree.")
        kind = record.get("typeName")
        if kind not in ("shape", "page", "document", "asset"):
            raise ClientError("Unsupported record type.")
        if not key.startswith(kind + ":") or len(key) > 120:
            raise ClientError("Invalid record ID.")
        if kind == "shape":
            check_shape(record)
            shapes.append(record)
            if record["type"] == "image":
                asset = records.get(record["props"].get("assetId"))
                if not asset or asset.get("typeName") != "asset":
                    raise ClientError("Image asset does not exist.")
        elif kind == "asset":
            props = record.get("props", {})
            if record.get("type") != "image" or not isinstance(props, dict):
                raise ClientError("Only embedded PNG image assets are supported.")
            try:
                decode_png(props.get("src"))
            except ValueError:
                raise ClientError(
                    "Asset must contain a valid, bounded PNG image."
                ) from None
        elif kind == "page":
            pages += 1
    if not pages:
        raise ClientError("Notebook has no page record.")
    check_parents(shapes, records)
