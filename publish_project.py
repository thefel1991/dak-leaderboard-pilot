#!/usr/bin/env python3
"""
Publish a project directory as a private repo on the thefel1991 GitHub
account, under `{player-slug}__{project-slug}`. Pilot tool — client-side.

Usage:
  python3 publish_project.py                 # use CWD as project dir
  python3 publish_project.py /path/to/proj   # explicit path

Env:
  PLAYER_NAME         — required, e.g. "Ahmad Alraeesi"
  GITHUB_OWNER        — default: thefel1991
  TOKEN_FILE          — default: ~/.claude/.thefel1991_token

Exits non-zero on any failure. Prints what it did to stdout.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "thefel1991")
TOKEN_FILE = Path(os.environ.get("TOKEN_FILE", str(Path.home() / ".claude" / ".thefel1991_token")))
API = "https://api.github.com"

# Files that immediately indicate a secret is being committed. Abort, don't skip.
SECRET_PATTERNS = [
    r"^\.env(\..+)?$", r".*\.pem$", r".*\.key$", r".*\.p12$", r".*\.pfx$",
    r"^credentials\.json$", r"^secrets?\.json$", r"^id_rsa$", r"^id_ed25519$",
    r".*\.sqlite3?$", r".*\.db$",
]

DEFAULT_GITIGNORE = """\
# Dependencies & build artifacts
node_modules/
venv/
.venv/
__pycache__/
*.pyc
dist/
build/
.next/
.nuxt/

# Secrets (do not commit)
.env
.env.*
!.env.example
*.pem
*.key
*.p12
*.pfx
credentials.json
secrets.json

# Local data
*.sqlite
*.sqlite3
*.db
*.log

# OS / editor
.DS_Store
Thumbs.db
.idea/
.vscode/
"""


def slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9.-]", "", s)
    s = re.sub(r"-+", "-", s).strip(".-")
    return s[:50]  # keep each half short; repo name total must be < 100


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd, cwd=None, check=True):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        die(f"cmd {cmd!r} failed:\n  stdout: {res.stdout.strip()}\n  stderr: {res.stderr.strip()}")
    return res


def load_token() -> str:
    if not TOKEN_FILE.exists():
        die(f"token file not found: {TOKEN_FILE}")
    tok = TOKEN_FILE.read_text().strip()
    if not tok:
        die(f"token file is empty: {TOKEN_FILE}")
    return tok


def gh_request(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "publish_project.py/0.1",
            **({"Content-Type": "application/json"} if body else {}),
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        payload = json.loads(resp.read().decode() or "{}")
        return resp.status, payload
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read().decode()
        except Exception:
            pass
        return e.code, {"error": e.reason, "body": body_txt}


def ensure_repo(owner: str, name: str, token: str) -> dict:
    status, payload = gh_request("GET", f"/repos/{owner}/{name}", token)
    if status == 200:
        print(f"  repo exists: {owner}/{name}")
        return payload
    if status != 404:
        die(f"unexpected {status} checking repo: {payload}")
    print(f"  creating repo: {owner}/{name}")
    # For a User account, POST /user/repos (ignore owner — goes to authenticated user).
    status, payload = gh_request("POST", "/user/repos", token, body={
        "name": name,
        "private": True,
        "description": f"Leaderboard pilot — player/project sync",
        "auto_init": False,
    })
    if status not in (200, 201):
        die(f"create repo failed: {status} {payload}")
    return payload


def scan_secrets(project_dir: Path) -> list[str]:
    """Walk the project respecting a minimal ignore list; return a list of files
    that match any SECRET_PATTERNS. Skip obvious ignore dirs so we don't scan
    node_modules or venv."""
    skip_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__",
                 "dist", "build", ".next", ".nuxt"}
    patterns = [re.compile(p, re.IGNORECASE) for p in SECRET_PATTERNS]
    hits = []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), project_dir)
            name = os.path.basename(rel)
            if any(p.match(name) for p in patterns):
                hits.append(rel)
    return hits


def ensure_gitignore(project_dir: Path):
    gi = project_dir / ".gitignore"
    if gi.exists():
        print(f"  .gitignore present ({gi.stat().st_size} bytes)")
        return
    print(f"  writing default .gitignore")
    gi.write_text(DEFAULT_GITIGNORE)


def main():
    player = os.environ.get("PLAYER_NAME", "").strip()
    if not player:
        die("PLAYER_NAME env var required")

    project_dir = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else Path.cwd()
    if not project_dir.is_dir():
        die(f"not a directory: {project_dir}")

    project_name = project_dir.name
    player_slug = slug(player)
    project_slug = slug(project_name)
    if not player_slug or not project_slug:
        die(f"could not slugify player={player!r} project={project_name!r}")
    repo_name = f"{player_slug}__{project_slug}"
    if len(repo_name) > 100:
        die(f"repo name too long ({len(repo_name)}): {repo_name}")

    print(f"player:      {player} → {player_slug}")
    print(f"project:     {project_name} → {project_slug}")
    print(f"repo:        {GITHUB_OWNER}/{repo_name}")
    print(f"project dir: {project_dir}")

    token = load_token()

    print("\n[1/5] secret scan")
    hits = scan_secrets(project_dir)
    if hits:
        print("  🛑 possible secrets found — aborting. Review these files first:")
        for h in hits:
            print(f"    - {h}")
        print("  Either remove them, add them to .gitignore, or override by setting FORCE=1")
        if os.environ.get("FORCE") != "1":
            sys.exit(2)
        print("  FORCE=1 set — continuing despite secret matches")
    else:
        print("  clean")

    print("\n[2/5] .gitignore")
    ensure_gitignore(project_dir)

    print("\n[3/5] GitHub repo")
    repo = ensure_repo(GITHUB_OWNER, repo_name, token)
    clone_url_auth = repo["clone_url"].replace("https://", f"https://{token}@")

    print("\n[4/5] local git")
    if not (project_dir / ".git").exists():
        print("  git init")
        run(["git", "init", "-b", "main"], cwd=project_dir)
    else:
        print("  .git present")

    # Set remote (overwrite if exists)
    run(["git", "remote", "remove", "thefel1991"], cwd=project_dir, check=False)
    run(["git", "remote", "add", "thefel1991", clone_url_auth], cwd=project_dir)

    # Stage + commit
    run(["git", "add", "-A"], cwd=project_dir)
    status = run(["git", "status", "--porcelain"], cwd=project_dir)
    if not status.stdout.strip():
        print("  nothing to commit (working tree clean after add)")
    else:
        print("  committing")
        run(["git", "-c", "user.email=leaderboard@thefel1991.local",
             "-c", f"user.name={player}",
             "commit", "-m", f"pilot: sync from {player}"], cwd=project_dir)

    print("\n[5/5] push")
    # Branch name — push whatever HEAD is to main
    run(["git", "branch", "-M", "main"], cwd=project_dir, check=False)
    push = run(["git", "push", "-u", "thefel1991", "main"], cwd=project_dir)
    print(push.stdout.strip() or push.stderr.strip())

    # Clean up: replace the embedded-token remote with a token-less URL so the
    # token doesn't sit in .git/config on disk.
    run(["git", "remote", "set-url", "thefel1991", repo["clone_url"]], cwd=project_dir)

    print(f"\n✅ done: https://github.com/{GITHUB_OWNER}/{repo_name}")


if __name__ == "__main__":
    main()
