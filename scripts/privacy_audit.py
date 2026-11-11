"""Scan working source and every reachable Git blob; never print matched values."""

import argparse
import json
import re
import subprocess
from pathlib import Path

PRIVATE = {"data", "sessions", "code-backups", ".env", ".venv", "node_modules", ".git"}
PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "service_secret": re.compile(
        rb"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})"
    ),
    "personal_absolute_path": re.compile(rb"/Users/[A-Za-z0-9_.-]+/"),
}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def audit(repo, source):
    known = []
    for name in ("token", "ipad-token"):
        file = repo / "data" / name
        if file.is_file():
            value = file.read_bytes().strip()
            if len(value) >= 12:
                known.append(value)
    findings = []

    def scan(raw, identity, scope):
        for category, pattern in PATTERNS.items():
            if pattern.search(raw):
                findings.append(
                    {"scope": scope, "object": identity, "category": category}
                )
        if any(secret in raw for secret in known):
            findings.append(
                {
                    "scope": scope,
                    "object": identity,
                    "category": "matches_active_local_secret",
                }
            )

    current = 0
    for file in source.rglob("*"):
        relative = file.relative_to(source)
        if (
            any(
                p in PRIVATE
                or p in ("dist", "__pycache__", ".ruff_cache", "browser-artifacts")
                for p in relative.parts
            )
            or not file.is_file()
        ):
            continue
        current += 1
        scan(file.read_bytes(), str(relative), "working_source")
    tracked = git(repo, "ls-files", "-z").decode().split("\0")
    sensitive_tracked = sum(
        bool(name) and any(part in PRIVATE for part in Path(name).parts)
        for name in tracked
    )
    lines = git(repo, "rev-list", "--objects", "--all").decode().splitlines()
    blobs = 0
    private_history = 0
    for line in lines:
        oid, _, name = line.partition(" ")
        if git(repo, "cat-file", "-t", oid).strip() != b"blob":
            continue
        blobs += 1
        private_history += any(part in PRIVATE for part in Path(name).parts)
        scan(git(repo, "cat-file", "blob", oid), oid, "git_history")
    return {
        "working_files_scanned": current,
        "reachable_history_blobs_scanned": blobs,
        "private_paths_tracked": sensitive_tracked,
        "private_paths_in_reachable_history": private_history,
        "findings": findings,
        "active_secret_matches": sum(
            f["category"] == "matches_active_local_secret" for f in findings
        ),
        "limits": "Pattern and exact-local-secret scan, not a proof of absence. Unreachable objects and external remotes are outside this scan. No history was rewritten. Matched values are never printed.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = audit(a.repo, a.source)
    a.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "findings"}))
