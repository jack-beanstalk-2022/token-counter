---
name: token-report
description: Build a local HTML report of Codex token usage from rollout logs. Use when asked about token usage, the context window, cached vs uncached input, prompt caching, which sessions or tool outputs burn the most tokens, when the weekly rate limit last reset or how much of it is used, which account the usage belongs to, or for a token/usage report or dashboard.
---

# Token Report

Re-tokenizes `~/.codex/sessions/**/rollout-*.jsonl` locally and renders a self-contained HTML
dashboard. Every figure comes from rollout logs; `~/.codex/auth.json` is read only to name
the account (id_token identity claims — never the access or refresh tokens, and
`--no-account` skips it). Nothing is intercepted, nothing of the user's is sent anywhere, and
no pricing translation is applied. The one network call is installing `tiktoken` from PyPI,
once, when it is missing (see below). Publishing numbers to the tokenusage.dev leaderboard is
a different skill, token-share, used only when the user asks to share.

Run from this skill's directory. Use whichever interpreter name exists on the machine:
`python3` on macOS and Linux, `python` on Windows, where `python3` is usually a Microsoft
Store stub that opens a web page instead of running anything. Python 3.8 or newer.

```bash
python3 scripts/report.py            # macOS, Linux
python  scripts/report.py            # Windows
```

That covers every session, writes `~/.codex/token-counter/report-all.html`, and opens it.

## First run: tiktoken installs itself

Installing the plugin does not install Python packages. When `tiktoken` is not importable,
the first run installs it with `pip install --target` into
`~/.codex/token-counter/lib/<interpreter>-<platform>/` and prints
`tiktoken is not installed; installing it for token-counter (once, from PyPI)`. The user's own
Python environment is not changed, and later runs reuse that directory without the network.

That install needs network access, which the Codex sandbox usually blocks. If the output says
`tiktoken could not be installed: PyPI is unreachable`, the report was still written, with
every usage figure exact and only the content composition empty. Tell the user, and offer to
re-run the same command once with network access they approve. Do not run `pip install`
yourself instead. If the user does not want anything downloaded, pass `--no-install`.

## Options

```bash
python3 scripts/report.py --since 2026-09-01          # windowed
python3 scripts/report.py --since 2026-09-01 --until 2026-09-14
python3 scripts/report.py --session 019fa0e6          # one session (id or prefix)
python3 scripts/report.py --fast                      # use every core instead of half
python3 scripts/report.py --metrics-only              # usage ledger only, no tokenization
python3 scripts/report.py --json model.json           # machine-readable model
python3 scripts/report.py --no-open                   # write the file, do not launch a browser
python3 scripts/report.py --rebuild                   # discard the index and re-parse
python3 scripts/report.py --no-account                # do not read auth.json
python3 scripts/report.py --no-install                # never install tiktoken from PyPI
python3 scripts/report.py --sessions-root PATH       # a corpus somewhere else
python3 scripts/report.py --doctor                   # what this machine provides, then exit
```

Run `--doctor` first when anything looks wrong or empty. It prints the interpreter, the
resolved `CODEX_HOME`, the sessions root and what is in it, the auth mode, the vocabulary and
the index, marking every failed check with `!`, and exits non-zero if any check failed.

Use `--no-open` when the user only wants numbers, and read the summary the script prints on
stdout: one line of totals (responses, recorded input, uncached, cache hit, output) and, when
an account and a limit window are available, a second line with the account, plan, weekly
limit used, when the current window opened, and when it next resets. Use `--json` when you
need to answer a specific question rather than hand over a page.

The first run parses the whole corpus; later runs reuse a SQLite index at
`~/.codex/token-counter/index.db` and only re-read files that changed. On the development
machine (1,953 files / 7.7 GB, 16 cores) a cold run is ~31s (~22s with `--fast`) and a warm
run ~4s.

## Reporting results

Four things must survive into whatever you tell the user, because the report is easy to
over-read:

- **Recorded, not billed.** These are the counts Codex wrote to its logs. Legacy compaction
  intervals carry no usage evidence, so this is recorded coverage, not a billing total.
- **Cached input is a subset of recorded input**, not an extra charge on top of it.
- **Cache-divergence rows are leads, not findings.** Rollouts carry no request body, no send
  timestamp, no cache key and no TTL, so cache *causation* is not recoverable. Cache also
  survives across sessions, and the log is written in completion order, not request order.
- **The reconciliation residual does not validate the tokenizer.** `o200k_base` is an
  assumption; the encoding current Codex models use is not published.
- **Weekly-limit percentages are the server's own figures**, recorded verbatim from the
  logs — not something this tool measured, and not derived from any token count on the page.
  Window boundaries, by contrast, *are* inferred: they are taken from where the reported
  percentage drops, because resets are routinely earlier than seven days.

Do not convert token counts into money, and do not invent a tokens-per-percent rate — the
corpus shows 95% of a weekly window costing 2.81B recorded input one week and 790M another.
Quoting the reported percentage and the reset times is fine; those are recorded facts.

## If it fails

- `tokenizer unavailable` — the run continues without it. `tiktoken` and the vendored
  vocabulary are needed only for content composition, so the headline numbers, the weekly
  limit chart and the daily chart are still exact and the fourth panel says why it is empty.
  If the reason is that `tiktoken` could not be installed, re-run with network access (see
  *First run*). A missing or corrupt vocabulary is fixed with `python3 scripts/fetch_vocab.py`
  once from the repository root; that is a packaging step, and the report never downloads
  the vocabulary.
- `No rollout files found` — Codex has not written any sessions yet, or `CODEX_HOME` points
  elsewhere. The message prints the directory it searched. Pass `--sessions-root` to override.
- `index unusable` / `process pool unavailable` — both are optimisations and both degrade on
  their own: the run continues without the cache, or on a single process. Slower, identical
  output.
- `could not write ...` — the report falls back to the system temp directory and prints the
  path it actually used. The last line of stdout is always where the file is.
- Anything else — `--doctor` first, then `--rebuild`, which discards the index and re-parses.

## What the report contains

Five headline numbers — recorded input, output, cache hit, sessions, and the weekly limit as
the server's own reported percentage — and three charts: a cumulative token curve per weekly
limit window with the reported percentage overlaid; daily recorded input stacked by the model
that was charged for it; and content composition by category.

The two time charts share one axis, so a day in one is the same x in the other. Scrolling,
dragging or pinching either chart zooms and pans both — horizontally only, the value axes do
not move — and the composition chart recomposes over whatever range is on screen. It works
the same on a phone; the toolbar above the charts has zoom and reset buttons either way.

Everything else the run produces — sessions, reconciliation, images, the per-window table and
the data-quality counters — is in `--json` and the stdout summary, not on the page.

Figures derived from inference rather than measurement are marked in the page itself.
