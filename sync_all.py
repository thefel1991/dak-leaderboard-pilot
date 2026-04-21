#!/usr/bin/env python3
"""
sync_all.py — Claude Code Stop-hook wrapper.

Walks the player's session data, finds every project directory they've
worked in, and publishes each one to thefel1991 via publish_project.py.
Then triggers push_stats.py for the leaderboard stats.

One failed project doesn't block the rest — each is run in its own
subprocess. Results are logged to ~/.claude/.sync_all_last.log

Env:
  PLAYER_NAME            — required (inherited from shell)
  SYNC_ALL_DISABLE       — set to "1" to no-op (kill switch)

Usage:
  python3 sync_all.py               # scan and publish all projects
  python3 sync_all.py --dry-run     # show what would be pushed, do nothing
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLISH_SCRIPT = HERE / "publish_project.py"
COMMIT_STATS_SCRIPT = HERE / "commit_stats.py"
PUSH_STATS_SCRIPT = HERE / "push_stats.py"

PROJECTS_JSONL_DIR = Path.home() / ".claude" / "projects"
LOG_FILE = Path.home() / ".claude" / ".sync_all_last.log"

# Dirs that are definitely not projects we should push
SKIP_PROJECT_NAMES = {
    "Other", "AppData", "Desktop", "Documents", "Downloads", "Library",
    "Movies", "Music", "Pictures", "Public", "Applications", "Sites",
    ".claude", ".config", ".local", ".cache",
    "node_modules", "venv", "__pycache__",
}


def log(msg: str, *, also_stderr: bool = True):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    try:
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if also_stderr:
        print(line, file=sys.stderr)


def find_project_dirs_from_sessions() -> list[Path]:
    """Scan ~/.claude/projects/*.jsonl, extract each session's cwd, and
    resolve to a set of unique project directories on disk."""
    found: dict[str, Path] = {}  # abs path str -> Path
    home = str(Path.home())

    if not PROJECTS_JSONL_DIR.exists():
        return []

    for session_dir in PROJECTS_JSONL_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        for jsonl in session_dir.glob("*.jsonl"):
            if jsonl.stat().st_size == 0:
                continue
            try:
                with jsonl.open("r", errors="replace") as f:
                    for line in f:
                        try:
                            msg = json.loads(line)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        cwd = msg.get("cwd")
                        if not cwd:
                            continue
                        proj_path = resolve_project_path(cwd, home)
                        if proj_path:
                            found.setdefault(str(proj_path), proj_path)
                        break  # one cwd per session is enough
            except OSError:
                continue

    return sorted(found.values(), key=lambda p: str(p).lower())


CONTAINER_DIRS = {"projects", "repos", "code", "dev", "src", "workspace",
                  "workspaces", "github", "gitlab", "bitbucket", "work",
                  "personal", "apps"}


def resolve_project_path(cwd: str, home: str) -> Path | None:
    """Given a cwd, return the project root directory, or None if it's
    a system dir we should skip."""
    if not cwd or not cwd.startswith(home):
        return None
    relative = cwd[len(home):].strip("/")
    if not relative:
        return None
    parts = relative.split("/")
    first = parts[0]
    if first.startswith(".") or first in SKIP_PROJECT_NAMES:
        return None
    # If cwd is deep inside a container like Projects/Foo/src, the project
    # is Projects/Foo.
    if first.lower() in CONTAINER_DIRS:
        if len(parts) >= 2 and parts[1] and not parts[1].startswith("."):
            project_root = Path(home) / first / parts[1]
        else:
            return None
    else:
        project_root = Path(home) / first
    if not project_root.is_dir():
        return None
    return project_root


def publish_one(project_dir: Path, player: str, dry_run: bool) -> dict:
    """Run publish_project.py as a subprocess on the given project."""
    cmd = ["python3", str(PUBLISH_SCRIPT), str(project_dir)]
    if dry_run:
        return {"project": project_dir.name, "path": str(project_dir),
                "status": "dry-run", "url": None, "error": None}
    env = {**os.environ, "PLAYER_NAME": player}
    try:
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"project": project_dir.name, "path": str(project_dir),
                "status": "timeout", "url": None, "error": "180s timeout"}
    # Script exits 2 when secrets are found — skip cleanly
    if res.returncode == 2:
        return {"project": project_dir.name, "path": str(project_dir),
                "status": "skipped-secrets", "url": None,
                "error": "secrets detected; skipped"}
    if res.returncode != 0:
        err = (res.stderr.strip().splitlines() or [""])[-1][:300]
        return {"project": project_dir.name, "path": str(project_dir),
                "status": "failed", "url": None, "error": err}
    # Extract URL from stdout's last line
    url = None
    for line in reversed(res.stdout.splitlines()):
        if "https://github.com" in line:
            url = line.split("https://", 1)[1]
            url = "https://" + url.rstrip("⏎ ").strip()
            break
    return {"project": project_dir.name, "path": str(project_dir),
            "status": "ok", "url": url, "error": None}


def run_script(script: Path, dry_run: bool, label: str) -> dict:
    if dry_run:
        return {"label": label, "status": "dry-run"}
    if not script.exists():
        return {"label": label, "status": "skipped",
                "reason": f"{script.name} not found"}
    try:
        res = subprocess.run(["python3", str(script)],
                             capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"label": label, "status": "timeout"}
    if res.returncode != 0:
        return {"label": label, "status": "failed",
                "error": (res.stderr.strip().splitlines() or [""])[-1][:300]}
    return {"label": label, "status": "ok"}


def main():
    if os.environ.get("SYNC_ALL_DISABLE") == "1":
        log("kill switch set; skipping")
        return

    player = os.environ.get("PLAYER_NAME", "").strip()
    if not player:
        log("PLAYER_NAME not set; nothing to do")
        return

    dry_run = "--dry-run" in sys.argv

    if not PUBLISH_SCRIPT.exists():
        log(f"publish_project.py missing at {PUBLISH_SCRIPT}")
        return

    start = time.time()
    log(f"sync_all start — player={player} dry_run={dry_run}")

    projects = find_project_dirs_from_sessions()
    log(f"discovered {len(projects)} project directories")

    results = []
    for p in projects:
        log(f"  → {p}")
        r = publish_one(p, player, dry_run)
        log(f"    {r['status']}" + (f" — {r['error']}" if r.get("error") else ""))
        results.append(r)

    # Parallel-pilot: commit stats JSON to leaderboard-data repo (new path)
    commit_result = run_script(COMMIT_STATS_SCRIPT, dry_run, "commit_stats")
    log(f"commit_stats: {commit_result['status']}" +
        (f" — {commit_result.get('error','')}" if commit_result.get("error") else ""))

    # Parallel-pilot: also POST to legacy HTTP endpoint if push_stats.py exists
    stats_result = run_script(PUSH_STATS_SCRIPT, dry_run, "push_stats")
    log(f"push_stats: {stats_result['status']}" +
        (f" — {stats_result.get('error','')}" if stats_result.get("error") else ""))

    elapsed = time.time() - start
    ok = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"].startswith("skipped"))
    failed = sum(1 for r in results if r["status"] in ("failed", "timeout"))
    log(f"sync_all done in {elapsed:.1f}s — ok={ok} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
