"""Reproducible synthetic storage workloads; never loads personal notebooks."""

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from board_storage import BoardStorage, dumps, empty_snapshot


def workload(count):
    snapshot = empty_snapshot("Original synthetic benchmark")
    for i in range(count):
        snapshot["store"][f"shape:stroke-{i}"] = {
            "id": f"shape:stroke-{i}",
            "typeName": "shape",
            "type": "draw",
            "parentId": "page:page",
            "x": i % 100 * 10,
            "y": i // 100 * 10,
            "props": {
                "segments": [
                    {
                        "points": [
                            {"x": j * 2, "y": (j % 5) * 3, "z": 0.5} for j in range(20)
                        ]
                    }
                ],
                "size": "s",
            },
        }
    return snapshot


def measure(callback, repeats=5):
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        callback()
        times.append((time.perf_counter() - start) * 1000)
    return {
        "median_ms": round(statistics.median(times), 2),
        "max_ms": round(max(times), 2),
        "samples": repeats,
    }


def run():
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "workloads": [],
        "scope": "Synthetic local storage: 20 points/stroke. Five samples per operation. No baseline comparison, physical Pencil latency or performance-gain claim.",
    }
    for count in (100, 1000, 10000):
        with tempfile.TemporaryDirectory() as tmp:
            storage = BoardStorage(tmp)
            storage.metadata()
            value = workload(count)
            save = measure(
                lambda storage=storage, value=value, count=count: storage.save_snapshot(
                    "default",
                    value,
                    {"shapeCount": count},
                    base=storage.revision("default"),
                )
            )
            read = measure(lambda storage=storage: storage.read("default"))
            stroke = value["store"]["shape:stroke-0"]

            def incremental(stroke=stroke, storage=storage, count=count):
                stroke["x"] += 1
                storage.save_changes(
                    "default",
                    {"put": [stroke], "remove": []},
                    {"shapeCount": count},
                    storage.revision("default"),
                )

            result["workloads"].append(
                {
                    "strokes": count,
                    "snapshot_bytes": len(dumps(value).encode()),
                    "whole_save": save,
                    "load_and_validate": read,
                    "one_stroke_save": measure(incremental),
                    "one_stroke_payload_bytes": len(
                        dumps({"put": [stroke], "remove": []}).encode()
                    ),
                }
            )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.json"))
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(), indent=2))
    print("Synthetic measurements written to", args.output)
