# Reproducible local measurements

Local candidate 1.3.0rc1; macOS 26.5.1 arm64, Python 3.12.0, Chromium 158.0.8127.18 through Playwright 1.65.0. All material was original and synthetic in temporary installations. These numbers describe this workload on this host; they do not establish a speedup, Pencil latency or total process memory.

## Storage: five samples per operation

Each starting stroke has 20 points. Timings include validation and normal persistence work. The one-stroke upload is 619 bytes, but the server still serializes the complete snapshot. Backup spacing means these repeated samples do not all include a newly created historical backup.

| Strokes | Snapshot bytes | Whole save median / max ms | Load/validate median / max ms | One-stroke save median / max ms |
| --- | --- | --- | --- | --- |
| 100 | 62123 | 5.16 / 8.75 | 2.24 / 2.26 | 3.34 / 3.47 |
| 1000 | 621824 | 46.89 / 47.22 | 22.08 / 22.25 | 29.28 / 30.24 |
| 10000 | 6245834 | 470.32 / 473.82 | 233.04 / 233.71 | 297.06 / 300.3 |

## Browser: one run per workload

First open includes navigation, Saved, and two animation frames. Idle rAF p95 is the frame interval while not writing, not input-to-pixel latency. Heap values are Chromium CDP JavaScript heap snapshots, not total browser/OS memory. The 30-stroke duration includes automation pacing and final saving; it is not a per-stroke latency estimate.

| Starting strokes | Open + two frames ms | Idle frame p95 ms | JS heap bytes | 30 strokes and save ms |
| --- | --- | --- | --- | --- |
| 100 | 402.0 | 16.8 | 3628392 | 7839.6 |
| 1000 | 329.4 | 16.8 | 10113820 | 7813.8 |
| 10000 | 829.9 | 16.8 | 43777696 | 8788.8 |

## Sustained synthetic writing and recovery

Starting with 10,030 strokes, 600 additional automated strokes and final saving took 141.4 seconds. JavaScript heap samples were 34,690,664 bytes before and 46,466,668 afterward; garbage collection was not forced, so this is not evidence of a leak or a memory plateau.

Ten more strokes were drawn while POST saves were deliberately blocked. After requests were restored, Saved appeared after 1795.5 ms. The server snapshot contained exactly 10,640 strokes. This measures one simulated interruption, not real Wi-Fi/sleep behavior.

This roughly two-minute synthetic session is reproducible evidence, not certification of a long physical study session. Real Pencil latency, heat, battery, longer sessions, image-heavy notebooks, multiple hardware models and total process memory still require the physical checks.

## Reproduction

```sh
.venv/bin/python scripts/benchmark.py --output storage-results.json
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/browser_evidence.py --mode benchmark --output browser-results
```

Raw data: [storage](measurements/storage.json), [browser](measurements/browser.json), [installed dependency versions](measurements/environment.txt). Direct dependencies are pinned; the environment record captures the transitive versions actually installed.

The current measurements do not justify an immediate storage-format migration. Large snapshots still cost hundreds of milliseconds to save; gather image-heavy and hardware evidence before changing asset storage/indexing, and preserve the current format/IDs in any later migration. No performance improvement is claimed without a comparable baseline.
