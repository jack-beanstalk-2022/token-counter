---
name: token-share
description: Share Codex token usage with the public tokenusage.dev leaderboard, update a previous share, or delete it. Use only when the user explicitly asks to share, publish, post or upload their token usage or report, to join or update the tokenusage.dev leaderboard, or to remove their data from it. For a private local report, use token-report instead.
---

# Token Share

Sends a summary of the user's Codex token usage to **tokenusage.dev**, where it appears on
public leaderboards (most tokens in a month, longest session, biggest session, most active
days) and on a public profile page at `https://tokenusage.dev/u/<handle>`.

This is the only part of the plugin that uses the network, and it sends nothing unless run
with `--yes`. The numbers come from the same canonical usage ledger as token-report, so the
leaderboard and the local report agree day for day.

Run from this skill's directory, with `python3` on macOS and Linux or `python` on Windows.

## Always dry-run first, and get consent

1. Run the dry run. It reads the logs, prints exactly what would be shared and sends nothing:

   ```bash
   python3 scripts/share.py
   ```

2. Show the user the summary it printed: the handle, the date range, the totals, and the
   per-month lines. Tell them the leaderboard is **public**.
3. On a first share, ask which **handle** they want shown (3-24 characters: lowercase
   letters, digits, hyphens). Do not invent one, and do not derive it from their account,
   email or machine name.
4. Only after they agree, send:

   ```bash
   python3 scripts/share.py --handle THEIR-HANDLE --yes   # first share
   python3 scripts/share.py --yes                         # every later share
   ```

   The last line of output is their profile URL. Give it to them.

Sending needs network access. Inside the Codex sandbox the network is usually off, so the
`--yes` command must run with network access the user approves. The dry run needs none.

## What is sent, and what never is

Sent: per-day counts (responses, recorded input, cached input, output, reasoning, sessions
started); for each month the top sessions by active time and by tokens, each as a one-way
hash of its id, start and end times, active time, token counts and the model name; and for
each weekly rate-limit window, its start, the plan type and the percentages used that Codex
logged in its rate-limit snapshots, and the tokens counted in it. tokenusage.dev uses the
windows to estimate how many tokens each plan's weekly limit holds.

Never sent: prompts, outputs, tool results, file contents or paths, working directories,
session titles, anything from `auth.json`, or the account email. The plan type comes from
the rollout logs, not from the account. The handle the user picks is the only identity.

Use `--out payload.json` to write the exact payload to a file for the user to inspect,
without sending it.

## Options

```bash
python3 scripts/share.py                        # dry run
python3 scripts/share.py --out payload.json     # write the payload, send nothing
python3 scripts/share.py --handle NEW --yes     # rename (the old handle is released)
python3 scripts/share.py --delete               # say what would be deleted
python3 scripts/share.py --delete --yes         # delete everything shared, forget the token
python3 scripts/share.py --forget               # drop the local token only
python3 scripts/share.py --sessions-root PATH   # a corpus somewhere else
python3 scripts/share.py --fast                 # every core instead of half
```

The first share returns a token, stored in `~/.codex/token-counter/share.json` (mode 0600).
It is the only proof of who owns the handle: later shares and deletion need it. Never print
it or paste it anywhere. Lose it, and the numbers already shared cannot be updated or deleted
from this machine; `--forget` then lets the user start over under a new handle.

## How the figures are defined

- **Tokens** on the leaderboard are recorded input plus output. Cached input is part of
  recorded input, not added to it. These are counts Codex wrote to its logs, not a bill.
- **Days** are the user's local calendar days, as in token-report; a session belongs to the
  day and month its first response landed in.
- **Active time** is the time between consecutive responses in a session, leaving out any
  gap longer than 30 minutes. Wall-clock span is shown beside it.
- Each share replaces the months it contains. Months no longer in the logs (pruned rollout
  files) stay as they were last shared.

Every figure is self-reported. The server rejects arithmetic no log can produce, but it
cannot verify a report, and the site says so.

## If it fails

- `the first share needs a leaderboard name` -- ask the user for a handle, add `--handle`.
- `handle_taken` (409) -- that handle belongs to someone else; ask for another.
- `bad_token` (401) -- the stored token was rejected. `--forget`, then share under a handle.
- `rate_limited` (429) -- too many shares this hour; later.
- `could not reach` -- no network: approve network access for the command, or try later.
- `No rollout files found` -- same as token-report; `--sessions-root` or `CODEX_HOME`.
