"""Usage and speed report for Codex tutor sessions.

Reads ~/.codex/sessions logs and prints, per session: model, recorded speed tier,
an ESTIMATE of model-token credits (logged tokens x OpenAI's published Codex rate card),
voice minutes, the weekly-meter readings, and how long each model call and reply took.

    python3 usage.py            # sessions from the last 2 days
    python3 usage.py 7          # last 7 days

Compare similar sessions on reply delay, tier and meter movement. The credit figure is an
estimate: included plan usage has no verified credit conversion. Voice cost is not in the
logs, so voice is shown only as minutes. Meter ticks mix usage with rounding and cannot
pin down a per-minute voice rate.
"""
import json, sys, glob, os, statistics, time
from datetime import datetime

# Credits per 1M tokens: (input, cached input, output). Standard speed.
RATES = {
    'gpt-6-astra': (250, 25, 1250), 'gpt-6.1-sol': (50, 2.5, 250), 'gpt-6-sol': (50, 5, 250),
    'gpt-6.1-terra': (25, 1.25, 125), 'gpt-6-luna': (2.5, 0.25, 12.5),
    'gpt-5.6-sol': (100, 10, 500), 'gpt-5.6-terra': (50, 5, 300), 'gpt-5.6-luna': (5, 0.5, 30),
}
FAST_MULT = 2.5  # included-usage multiplier for the Fast (priority) tier, per the pricing page


def ts(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def report(path):
    model = tier = None
    credits = 0.0
    calls, replies, meter, voice, contexts = [], [], [], [], []
    last = turn_start = None
    for line in open(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        p, t = d.get('payload') or {}, ts(d['timestamp'])
        kind = p.get('type')
        if d.get('type') == 'turn_context':
            model = p.get('model', model)
        elif kind == 'thread_settings_applied':
            tier = (p.get('thread_settings') or {}).get('service_tier', tier)
        elif kind in ('realtime_session_started', 'realtime_session_closed'):
            voice.append((kind, t))
        elif kind == 'task_started':
            last = turn_start = t
        elif kind == 'custom_tool_call_output':
            last = t
        elif kind == 'task_complete' and turn_start:
            replies.append(t - turn_start)
            turn_start = None
        elif kind == 'token_count' and p.get('info'):
            u = p['info']['last_token_usage']
            contexts.append(u['input_tokens'])
            r = RATES.get(model)
            if r:
                c = ((u['input_tokens'] - u['cached_input_tokens']) * r[0]
                     + u['cached_input_tokens'] * r[1] + u['output_tokens'] * r[2]) / 1e6
                credits += c * (FAST_MULT if tier in ('priority', 'fast') else 1)
            if last and t - last > 0.05:  # older logs repeat token_count events
                calls.append(t - last)
            last = t
            used = ((p.get('rate_limits') or {}).get('primary') or {}).get('used_percent')
            if used is not None and (not meter or meter[-1][1] != used):
                meter.append((t, used))
    if not calls:
        return
    vmin = 0.0
    start = None
    for kind, t in voice:
        if kind == 'realtime_session_started':
            start = t
        elif start:
            vmin += (t - start) / 60
            start = None
    if start:  # voice still on at the end of the log
        vmin += (os.path.getmtime(path) - start) / 60
    hm = lambda t: time.strftime('%H:%M', time.localtime(t))
    print(f"\n{os.path.basename(path)[8:27]}  model={model} tier={tier or 'standard'}")
    print(f"  model tokens: ~{credits:.1f} credits (estimate) over {len(calls)} model calls")
    print(f"  voice on: {vmin:.1f} min (cost not logged)")
    print(f"  context: {contexts[0]:,} tokens at the first call, {contexts[-1]:,} at the last")
    print(f"  model call time: median {statistics.median(calls):.1f}s, slowest {max(calls):.1f}s")
    print(f"  full reply time (before voice relay): median {statistics.median(replies):.1f}s" if replies else '')
    print('  weekly meter: ' + ', '.join(f"{hm(t)} {u:g}%" for t, u in meter))


if __name__ == '__main__':
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 2
    cutoff = time.strftime('%Y-%m-%dT%H-%M-%S', time.localtime(time.time() - days * 86400))
    for f in sorted(glob.glob(os.path.expanduser('~/.codex/sessions/*/*/*/*.jsonl'))):
        if os.path.basename(f)[8:27] >= cutoff:  # rollout-<start time>-<id>.jsonl
            report(f)
