"""Build a source-only ZIP from an explicit allowlist, with a hash manifest."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = (
    "AGENTS.md",
    "LICENSE",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    ".gitignore",
    ".prettierrc",
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "package-lock.json",
    "mcp_schema.json",
    "board.py",
    "board_storage.py",
    "board_review.py",
    "server.py",
    "ipad_server.py",
    "pairing.py",
    "validation.py",
    "ai_operations.py",
    "file_lock.py",
    "http_limits.py",
    "http_body.py",
    "media.py",
    "media_worker.py", "browser_snapshot.py",
    "build.py",
    "test.py",
    "load_session.py",
    "server.js",
    "ipad-server.js",
    "load-session.js",
    "build-native.mjs",
)
FOLDERS = ("src", "tests", "scripts", "samples", "docs", ".github", "ios")


def package(root, output):
    paths = [root / name for name in CORE if (root / name).is_file()]
    for folder in FOLDERS:
        paths.extend(
            p
            for p in (root / folder).rglob("*")
            if p.is_file()
            and not any(part in ("__pycache__", "raw-video", "xcuserdata") for part in p.parts)
            and p.name not in ("Signing.local.xcconfig", "CHANGE_AUDIT_2026-11-11.md")
            and (
                "demo" not in p.relative_to(root).parts
                or "candidate" in p.relative_to(root).parts
            )
        )
    paths = sorted(set(paths))
    manifest = {}
    for path in paths:
        if path.is_symlink():
            raise ValueError("Symlinks are not release sources")
        name = path.relative_to(root).as_posix()
        if any(
            part
            in ("data", "sessions", "code-backups", ".git", "node_modules", ".venv")
            for part in Path(name).parts
        ):
            raise ValueError("Private path in release allowlist")
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.relative_to(root).as_posix())
        archive.writestr(
            "RELEASE_MANIFEST.json",
            json.dumps({"version": "1.3.0rc1", "files": manifest}, indent=2),
        )
    with zipfile.ZipFile(output) as archive:
        for name, digest in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("Package verification failed")
    print(
        json.dumps(
            {
                "file": output.name,
                "files": len(manifest),
                "bytes": output.stat().st_size,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    package(a.root, a.output)
