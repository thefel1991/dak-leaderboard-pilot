#!/usr/bin/env python3
"""
commit_stats.py — aggregate this player's Claude Code stats from local
session JSONLs and commit them to thefel1991/leaderboard-data as a single
JSON file at players/{player-slug}.json.

Runs alongside (not instead of) push_stats.py during the parallel-pilot
window. Commits are throttled to once every 5 min to be kind to rate limits.

Env:
  PLAYER_NAME        — required
  TOKEN_FILE         — default ~/.claude/.thefel1991_token
  DATA_REPO          — default thefel1991/leaderboard-data
  THROTTLE_SEC       — default 300 (5 min)
  CHECKOUT_DIR       — default ~/.claude/.leaderboard-data-checkout

Usage:
  python3 commit_stats.py               # commit if >5min since last
  python3 commit_stats.py --force       # ignore throttle
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

PLAYER_NAME = os.environ.get("PLAYER_NAME", "").strip()
TOKEN_FILE = Path(os.environ.get("TOKEN_FILE", str(Path.home() / ".claude" / ".thefel1991_token")))
DATA_REPO = os.environ.get("DATA_REPO", "thefel1991/leaderboard-data")
THROTTLE_SEC = int(os.environ.get("THROTTLE_SEC", "300"))
CHECKOUT_DIR = Path(os.environ.get("CHECKOUT_DIR", str(Path.home() / ".claude" / ".leaderboard-data-checkout")))
THROTTLE_FILE = Path.home() / ".claude" / ".commit_stats_last_push"

PROJECTS_DIR = Path.home() / ".claude" / "projects"
IDLE_THRESHOLD = 600  # 10 min gap between messages = idle

PRICING = {
    "opus":   {"input": 5/1e6, "output": 25/1e6, "cache_read": 0.50/1e6, "cache_write": 6.25/1e6},
    "sonnet": {"input": 3/1e6, "output": 15/1e6, "cache_read": 0.30/1e6, "cache_write": 3.75/1e6},
    "haiku":  {"input": 1/1e6, "output": 5/1e6,  "cache_read": 0.10/1e6, "cache_write": 1.25/1e6},
}


def slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9.-]", "", s)
    s = re.sub(r"-+", "-", s).strip(".-")
    return s[:50]


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def model_family(model: str) -> str:
    m = (model or "").lower()
    if "haiku" in m: return "haiku"
    if "sonnet" in m: return "sonnet"
    return "opus"


def cost_for(inp, out, cr, cw, model):
    p = PRICING[model_family(model)]
    return inp * p["input"] + out * p["output"] + cr * p["cache_read"] + cw * p["cache_write"]


def parse_jsonl(filepath: Path) -> dict:
    """Parse a single session JSONL and return a stats dict."""
    timestamps = []
    human = api = inp_t = out_t = cr_t = cw_t = lines = 0
    cost = 0.0
    project_votes: dict[str, int] = {}
    try:
        with filepath.open("r", errors="replace") as f:
            for line in f:
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ts_str = msg.get("timestamp")
                if ts_str:
                    try:
                        timestamps.append(datetime.fromisoformat(ts_str.replace("Z", "+00:00")))
                    except (ValueError, TypeError):
                        pass
                cwd = msg.get("cwd", "") or ""
                proj = extract_project(cwd)
                if proj:
                    project_votes[proj] = project_votes.get(proj, 0) + 1
                mtype = msg.get("type", "")
                inner = msg.get("message") or {}
                if mtype == "user" and not msg.get("toolUseResult"):
                    content = inner.get("content", "")
                    if (isinstance(content, str) and content.strip()) or \
                       (isinstance(content, list) and any(
                           isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").strip()
                           for c in content)):
                        human += 1
                if mtype == "assistant":
                    api += 1
                    model = inner.get("model", "unknown")
                    usage = inner.get("usage") or {}
                    i = usage.get("input_tokens", 0) or 0
                    o = usage.get("output_tokens", 0) or 0
                    cr = usage.get("cache_read_input_tokens", 0) or 0
                    cw = usage.get("cache_creation_input_tokens", 0) or 0
                    inp_t += i; out_t += o; cr_t += cr; cw_t += cw
                    cost += cost_for(i, o, cr, cw, model)
                    for block in (inner.get("content") or []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            bi = block.get("input") or {}
                            if block.get("name") == "Write" and bi.get("content"):
                                lines += bi["content"].count("\n") + 1
                            elif block.get("name") == "Edit" and bi.get("new_string"):
                                lines += bi["new_string"].count("\n") + 1
    except OSError:
        pass

    timestamps.sort()
    active = 0
    for i in range(1, len(timestamps)):
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if gap <= IDLE_THRESHOLD:
            active += gap

    dominant = max(project_votes, key=project_votes.get) if project_votes else None
    return {
        "human_prompts": human, "api_calls": api,
        "input_tokens": inp_t, "output_tokens": out_t,
        "cache_read": cr_t, "cache_write": cw_t,
        "lines_written": lines, "cost": round(cost, 2),
        "active_seconds": int(active),
        "project": dominant,
        "first_ts": timestamps[0].isoformat() if timestamps else None,
        "last_ts": timestamps[-1].isoformat() if timestamps else None,
    }


SKIP_DIRS = {".claude", ".config", ".local", ".cache", "node_modules", "venv",
             ".venv", "__pycache__", "desktop", "documents", "downloads",
             "library", "movies", "music", "pictures", "public", "sites",
             "applications", "go", "opt", "tmp", "bin"}
CONTAINER_DIRS = {"projects", "repos", "code", "dev", "src", "workspace",
                  "workspaces", "github", "gitlab", "bitbucket", "work",
                  "personal", "apps"}


def extract_project(path: str) -> str | None:
    if not path:
        return None
    home = str(Path.home()).replace("\\", "/")
    path = path.replace("\\", "/")
    if not path.lower().startswith(home.lower()):
        return None
    rem = path[len(home):].strip("/")
    if not rem:
        return None
    parts = rem.split("/")
    first = parts[0]
    if first.startswith(".") or first.lower() in SKIP_DIRS:
        return None
    if first.lower() in CONTAINER_DIRS:
        if len(parts) >= 2 and parts[1] and not parts[1].startswith("."):
            return parts[1]
        return None
    return first


def collect_all_stats(player: str) -> dict:
    totals = {
        "player": player,
        "player_slug": slug(player),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_sessions": 0, "total_prompts": 0, "total_api_calls": 0,
        "total_input_tokens": 0, "total_output_tokens": 0,
        "total_cache_read": 0, "total_cache_write": 0,
        "total_lines_written": 0, "total_cost": 0.0,
        "total_active_hours": 0.0,
        "earliest_session": None, "latest_session": None,
        "projects": {},
    }
    if not PROJECTS_DIR.exists():
        return totals

    for session_dir in PROJECTS_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        for jsonl in session_dir.glob("*.jsonl"):
            if jsonl.stat().st_size == 0:
                continue
            s = parse_jsonl(jsonl)
            totals["total_sessions"] += 1
            totals["total_prompts"] += s["human_prompts"]
            totals["total_api_calls"] += s["api_calls"]
            totals["total_input_tokens"] += s["input_tokens"]
            totals["total_output_tokens"] += s["output_tokens"]
            totals["total_cache_read"] += s["cache_read"]
            totals["total_cache_write"] += s["cache_write"]
            totals["total_lines_written"] += s["lines_written"]
            totals["total_cost"] += s["cost"]
            totals["total_active_hours"] += s["active_seconds"] / 3600
            if s["first_ts"] and (not totals["earliest_session"] or s["first_ts"] < totals["earliest_session"]):
                totals["earliest_session"] = s["first_ts"]
            if s["last_ts"] and (not totals["latest_session"] or s["last_ts"] > totals["latest_session"]):
                totals["latest_session"] = s["last_ts"]
            pname = s["project"] or "Other"
            p = totals["projects"].setdefault(pname, {
                "sessions": 0, "prompts": 0, "api_calls": 0,
                "input_tokens": 0, "output_tokens": 0,
                "cache_read": 0, "cache_write": 0,
                "lines_written": 0, "cost": 0.0,
            })
            p["sessions"] += 1
            p["prompts"] += s["human_prompts"]
            p["api_calls"] += s["api_calls"]
            p["input_tokens"] += s["input_tokens"]
            p["output_tokens"] += s["output_tokens"]
            p["cache_read"] += s["cache_read"]
            p["cache_write"] += s["cache_write"]
            p["lines_written"] += s["lines_written"]
            p["cost"] += s["cost"]

    totals["total_cost"] = round(totals["total_cost"], 2)
    totals["total_active_hours"] = round(totals["total_active_hours"], 2)
    for p in totals["projects"].values():
        p["cost"] = round(p["cost"], 2)
    return totals


def should_commit() -> bool:
    if "--force" in sys.argv:
        return True
    if THROTTLE_FILE.exists():
        try:
            last = float(THROTTLE_FILE.read_text().strip())
            if time.time() - last < THROTTLE_SEC:
                return False
        except (ValueError, OSError):
            pass
    return True


def mark_committed():
    try:
        THROTTLE_FILE.write_text(str(time.time()))
    except OSError:
        pass


def run(cmd, cwd=None, check=True):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        die(f"cmd {cmd!r} failed:\n  stdout: {res.stdout.strip()}\n  stderr: {res.stderr.strip()}")
    return res


def ensure_checkout(token: str):
    """Ensure CHECKOUT_DIR exists and is a clean clone of DATA_REPO."""
    clone_url = f"https://{token}@github.com/{DATA_REPO}.git"
    if not (CHECKOUT_DIR / ".git").exists():
        CHECKOUT_DIR.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", clone_url, str(CHECKOUT_DIR)])
    else:
        # Sanitize any previous token URL; reset to token-bearing for this pull
        run(["git", "remote", "set-url", "origin", clone_url], cwd=CHECKOUT_DIR)
        run(["git", "fetch", "origin"], cwd=CHECKOUT_DIR)
        run(["git", "reset", "--hard", "origin/main"], cwd=CHECKOUT_DIR, check=False)


def main():
    if not PLAYER_NAME:
        die("PLAYER_NAME env var required")
    if not TOKEN_FILE.exists():
        die(f"token file not found: {TOKEN_FILE}")
    token = TOKEN_FILE.read_text().strip()
    if not token:
        die(f"token file empty: {TOKEN_FILE}")

    if not should_commit():
        print("throttled; skipping")
        return

    print(f"collecting stats for {PLAYER_NAME}...")
    stats = collect_all_stats(PLAYER_NAME)
    print(f"  sessions={stats['total_sessions']} prompts={stats['total_prompts']} "
          f"cost=${stats['total_cost']}")

    print(f"preparing checkout at {CHECKOUT_DIR}")
    ensure_checkout(token)

    # Write player JSON
    players_dir = CHECKOUT_DIR / "players"
    players_dir.mkdir(exist_ok=True)
    player_file = players_dir / f"{stats['player_slug']}.json"
    new_content = json.dumps(stats, indent=2, sort_keys=True) + "\n"

    # Skip commit if unchanged (avoid spam)
    if player_file.exists() and player_file.read_text() == new_content:
        print("no changes; skipping commit")
        mark_committed()
        # Sanitize remote so token doesn't sit on disk
        clone_url_clean = f"https://github.com/{DATA_REPO}.git"
        run(["git", "remote", "set-url", "origin", clone_url_clean], cwd=CHECKOUT_DIR, check=False)
        return

    player_file.write_text(new_content)
    run(["git", "add", str(player_file)], cwd=CHECKOUT_DIR)

    run(["git", "-c", "user.email=leaderboard@thefel1991.local",
         "-c", f"user.name={PLAYER_NAME}",
         "commit", "-m", f"stats: {PLAYER_NAME} — {stats['total_sessions']} sessions"],
        cwd=CHECKOUT_DIR)
    run(["git", "push", "origin", "main"], cwd=CHECKOUT_DIR)

    # Sanitize remote (no token on disk between pushes)
    clone_url_clean = f"https://github.com/{DATA_REPO}.git"
    run(["git", "remote", "set-url", "origin", clone_url_clean], cwd=CHECKOUT_DIR, check=False)

    mark_committed()
    print(f"✅ committed: https://github.com/{DATA_REPO}/blob/main/players/{stats['player_slug']}.json")


if __name__ == "__main__":
    main()
