# One-prompt onboarding for pilot players

Paste the block below into a **fresh Claude Code session** on each of
your machines. Claude Code will set up the leaderboard pilot automatically.

---

## The prompt

```text
You are helping me onboard to the DAK leaderboard pilot. Do the following
steps, in order, and confirm each one before moving to the next:

1. Ask me for my PLAYER_NAME (e.g., "Ahmad Alraeesi") and add
   `export PLAYER_NAME="<the name>"` to my ~/.zshrc. Tell me to run
   `source ~/.zshrc` after you're done.

2. Ask me to paste my GitHub token for thefel1991. Save it to
   ~/.claude/.thefel1991_token with mode 600. Do not echo the token
   back to me; only confirm it was saved.

3. Download these three scripts from the public pilot repo and save them
   to ~/.claude/:
   - https://raw.githubusercontent.com/thefel1991/dak-leaderboard-pilot/main/publish_project.py
   - https://raw.githubusercontent.com/thefel1991/dak-leaderboard-pilot/main/commit_stats.py
   - https://raw.githubusercontent.com/thefel1991/dak-leaderboard-pilot/main/sync_all.py
   Make sure all three are saved and readable.

4. Read ~/.claude/settings.json (create it if missing). Merge a Stop hook
   into the existing content so it runs sync_all.py at session end:
     {
       "hooks": {
         "Stop": [
           { "matcher": "*", "hooks": [{ "type": "command", "command": "python3 $HOME/.claude/sync_all.py" }] }
         ]
       }
     }
   Preserve any other keys already in the file (don't clobber).

5. Run a dry-run to confirm discovery works:
   PLAYER_NAME="<my name from step 1>" python3 ~/.claude/sync_all.py --dry-run
   Show me the output.

6. Tell me that setup is done, that the auto-sync will fire the next
   time I end a Claude Code session, and that I can disable it
   temporarily with `export SYNC_ALL_DISABLE=1`.

Important safety rules:
- Do not run any script with the real token until I explicitly say "go ahead"
  after seeing the dry-run output.
- If any step fails, stop and tell me; do not try to work around it.
- Treat my token like a password — don't paste it into any file other
  than ~/.claude/.thefel1991_token, and don't include it in any command
  output or log.
```

---

## What Ahmad (or any pilot player) sees

1. Opens Claude Code on his laptop
2. Pastes the prompt above
3. Claude Code asks for his player name → he types it
4. Claude Code asks for his GitHub token → he pastes it
5. Claude Code fetches the scripts, wires the hook, runs a dry-run, shows the output
6. Ahmad says "go ahead" (or fixes whatever the dry-run flagged)
7. Done. Every future session end auto-syncs.

## What you (admin) need to provide per player

- The GitHub token for `thefel1991` (one time, out-of-band — Slack DM,
  password manager share, whatever)
- A copy of this `ONBOARD_PROMPT.md` (or the prompt block above)

Players with no GitHub token can't participate in the pilot — which is
the correct behavior.
