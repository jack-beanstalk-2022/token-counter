#!/usr/bin/env python3
"""Share Codex token usage with the tokenusage.dev leaderboard.

    share.py                          # dry run: show what would be sent, send nothing
    share.py --handle NAME --yes      # first share: claim NAME on the leaderboard
    share.py --yes                    # later shares: update the numbers
    share.py --out payload.json       # write the exact payload to a file, send nothing
    share.py --delete --yes           # remove everything shared, and forget the token

The token-report skill never touches the network. This script is the one exception, and it
sends only when run with --yes. What it sends is daily token counts and a handful of
per-session summaries -- counts, times and a model name. Never prompts, file contents,
paths, session titles, or anything from auth.json. See the SKILL.md next to this file.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.normpath(os.path.join(HERE, '..', '..', 'token-report', 'scripts'))
sys.path.insert(0, REPORT)

import report as reportcli  # noqa: E402  -- enforces the Python floor on import
from tokencounter import analyze, ledger, rollout, worker  # noqa: E402

CLIENT = {'name': 'token-counter', 'version': '1.1.0'}
SCHEMA = 1
DEFAULT_API = 'https://tokenusage.dev/api'

# A gap between two responses longer than this is time away, not time in the session, and
# counts for nothing. Responses are stamped when they complete, so the gaps between them
# include the model working, tools running and the user reading and typing; half an hour
# covers a long build or test run without crediting a lunch break.
IDLE_CAP_S = 30 * 60

# Per month, the sessions sent are the top few by active time and by tokens: enough for the
# server to pick each month's record holders, without shipping every session ever run.
SESSIONS_PER_MONTH = 10


def _iso(epoch_s):
    return datetime.datetime.fromtimestamp(epoch_s, datetime.timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


def session_hash(sid):
    """Stable, one-way id for a session: the leaderboard can tell sessions apart, and no one
    can map one back to a rollout file."""
    return hashlib.sha256(('tokenusage.dev:' + str(sid)).encode('utf-8')).hexdigest()[:16]


def active_seconds(epochs):
    """Time between consecutive responses, skipping gaps longer than IDLE_CAP_S."""
    total = 0.0
    for a, b in zip(epochs, epochs[1:]):
        gap = b - a
        if 0 < gap <= IDLE_CAP_S:
            total += gap
    return int(round(total))


def build_payload(results, charged, handle=None, now=None):
    """The share payload from extracted files and their charged ledger rows.

    Returns ``(payload, notes)``. Everything is counted from the canonical ledger, exactly as
    the report counts it, so the leaderboard and the local report agree on every day.
    Days are the sharer's **local** calendar days, as in the report.
    """
    notes = collections.Counter()
    days = collections.defaultdict(collections.Counter)
    sessions = {}

    for path, fr in sorted(results.items()):
        rows = charged.get(path) or []
        sid = fr.get('session_id') or path
        s = sessions.setdefault(sid, {'rows': [], 'counts': collections.Counter(),
                                      'models': collections.Counter()})
        for r in rows:
            u = r.get('usage') or {}
            inp = int(u.get('input_tokens') or 0)
            cch = int(u.get('cached_input_tokens') or 0)
            out = int(u.get('output_tokens') or 0)
            rsn = int(u.get('reasoning_output_tokens') or 0)
            # The server rejects arithmetic no log should produce. A damaged record is not a
            # reason to refuse the whole share, so it is clamped here and counted.
            if cch > inp:
                cch = inp
                notes['clamped_cached'] += 1
            if rsn > out:
                rsn = out
                notes['clamped_reasoning'] += 1
            ts = r.get('ts')
            d = analyze._day(ts, fr.get('date'))
            if not d:
                notes['undated_responses'] += 1
                continue
            e = worker.epoch(ts)
            dd = days[d]
            dd['responses'] += 1
            dd['input'] += inp
            dd['cached'] += cch
            dd['output'] += out
            dd['reasoning'] += rsn
            c = s['counts']
            c['responses'] += 1
            c['input'] += inp
            c['cached'] += cch
            c['output'] += out
            c['reasoning'] += rsn
            s['models'][r.get('model') or 'unknown'] += 1
            s['rows'].append((e, d))

    summaries = []
    for sid, s in sessions.items():
        timed = sorted((e, d) for e, d in s['rows'] if e is not None)
        if not s['rows']:
            continue
        # The session's day is the local day of its first response, the same bucket that
        # response was counted in above.
        first_day = timed[0][1] if timed else min(d for _, d in s['rows'])
        days[first_day]['sessions'] += 1
        if not timed:
            notes['untimed_sessions'] += 1
            continue
        epochs = [e for e, _ in timed]
        c = s['counts']
        model = s['models'].most_common(1)[0][0] if s['models'] else None
        summaries.append({
            'id': session_hash(sid),
            'day': first_day,
            'start': _iso(epochs[0]),
            'end': _iso(epochs[-1]),
            'active_s': active_seconds(epochs),
            'responses': c['responses'],
            'input': c['input'],
            'cached': c['cached'],
            'output': c['output'],
            'reasoning': c['reasoning'],
            'model': None if model == 'unknown' else model[:80],
        })

    by_month = collections.defaultdict(list)
    for x in summaries:
        by_month[x['day'][:7]].append(x)
    keep = {}
    for group in by_month.values():
        for key in (lambda x: (x['active_s'], x['input'] + x['output']),
                    lambda x: (x['input'] + x['output'], x['active_s'])):
            for x in sorted(group, key=key, reverse=True)[:SESSIONS_PER_MONTH]:
                keep[x['id']] = x
    notes['sessions_total'] = len(summaries)

    payload = {
        'schema': SCHEMA,
        'client': dict(CLIENT),
        'generated_at': _iso((now or datetime.datetime.now(datetime.timezone.utc)).timestamp()),
        'days': [dict(date=d, responses=v['responses'], input=v['input'], cached=v['cached'],
                      output=v['output'], reasoning=v['reasoning'], sessions=v['sessions'])
                 for d, v in sorted(days.items()) if v['responses']],
        'sessions': sorted(keep.values(), key=lambda x: (x['start'], x['id'])),
    }
    if handle:
        payload['handle'] = handle
    return payload, notes


# ------------------------------------------------------------------------ local state

def state_path():
    return os.path.join(reportcli.out_dir(), 'share.json')


def load_state():
    try:
        with open(state_path(), encoding='utf-8') as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    """The token is a credential: written 0600, and never printed."""
    p = state_path()
    tmp = p + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, p)


def endpoint_state(state, api):
    return (state.get('endpoints') or {}).get(api) or {}


def set_endpoint_state(state, api, entry):
    eps = state.setdefault('endpoints', {})
    if entry is None:
        eps.pop(api, None)
    else:
        eps[api] = entry


# ------------------------------------------------------------------------ transport

def request(method, url, body=None, token=None, timeout=60):
    """``(status, parsed_json_or_None)``. Network failures raise OSError."""
    data = None if body is None else json.dumps(body, separators=(',', ':')).encode('utf-8')
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('User-Agent', f"{CLIENT['name']}/{CLIENT['version']}")
    req.add_header('Accept', 'application/json')
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw.decode('utf-8')) if raw else None
    except ValueError:
        return status, None


def _explain(status, body):
    body = body if isinstance(body, dict) else {}
    msg = body.get('message') or f'HTTP {status}'
    lines = [f'the server refused it ({status}): {msg}']
    for issue in (body.get('issues') or [])[:10]:
        lines.append(f'  - {issue}')
    return '\n'.join(lines)


# ------------------------------------------------------------------------ output

def _n(v):
    for unit, size in (('B', 1e9), ('M', 1e6), ('K', 1e3)):
        if v >= size:
            return f'{v / size:.2f}{unit}'
    return str(v)


def _dur(s):
    h, m = divmod(int(s) // 60, 60)
    return f'{h}h {m:02d}m' if h else f'{m}m'


def describe(payload, notes, handle, api):
    """What will be sent, in words: printed before anything leaves the machine."""
    days = payload['days']
    months = collections.OrderedDict()
    for d in days:
        m = months.setdefault(d['date'][:7], collections.Counter())
        for k in ('input', 'output', 'responses', 'sessions'):
            m[k] += d[k]
    tot_in = sum(d['input'] for d in days)
    tot_out = sum(d['output'] for d in days)
    lines = [
        f"sharing as   {handle or '(no handle yet)'} -> {api}",
        f"covers       {len(days):,} active days"
        + (f", {days[0]['date']} .. {days[-1]['date']}" if days else ''),
        f"tokens       {_n(tot_in + tot_out)} total ({_n(tot_in)} recorded input, "
        f"{_n(tot_out)} output)",
        f"sessions     {len(payload['sessions']):,} summarised of {notes['sessions_total']:,}"
        f" (per month, the top {SESSIONS_PER_MONTH} by active time and by tokens)",
        '',
        'month        tokens      sessions  longest session',
    ]
    longest = {}
    for s in payload['sessions']:
        k = s['day'][:7]
        if s['active_s'] > longest.get(k, -1):
            longest[k] = s['active_s']
    for k, m in reversed(months.items()):
        lines.append(f"{k}      {_n(m['input'] + m['output']):>9}  {m['sessions']:>8,}  "
                     f"{_dur(longest[k]) if k in longest else '--'}")
    lines += [
        '',
        'sent: per-day token counts, and per-session start/end times, active time, token',
        'counts and model name, under a one-way hash of the session id.',
        'never sent: prompts, outputs, file contents or paths, session titles, your',
        'account or email.',
    ]
    for k, label in (('clamped_cached', 'responses had cached > input (clamped)'),
                     ('clamped_reasoning', 'responses had reasoning > output (clamped)'),
                     ('undated_responses', 'responses had no usable timestamp (skipped)'),
                     ('untimed_sessions', 'sessions had no usable timestamps (not summarised)')):
        if notes.get(k):
            lines.append(f'note: {notes[k]:,} {label}')
    return '\n'.join(lines)


# ------------------------------------------------------------------------ main

def collect_payload(a, handle):
    files = rollout.discover(a.sessions_root)
    if not files:
        root = rollout.sessions_root(a.sessions_root)
        print(f'No rollout files found under {root}\n'
              f'  CODEX_HOME={os.environ.get("CODEX_HOME") or "(unset)"}\n'
              f'  Point at another location with --sessions-root PATH.', file=sys.stderr)
        return None
    cores = os.cpu_count() or 4
    procs = a.procs or (cores if a.fast else max(1, min(8, cores // 2)))
    # The usage ledger alone: nothing is tokenized, and the report's index is neither read
    # nor written, so this cannot disturb it.
    results, _ = reportcli.collect(files, procs, 1, None, None, a.quiet, True, 0)
    charged, _ = ledger.build(results)
    return build_payload(results, charged, handle=handle)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='share.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--handle', help='leaderboard name: 3-24 of a-z 0-9 -; required the first '
                                     'time, renames you if given later')
    ap.add_argument('--yes', action='store_true', help='actually send (default: dry run)')
    ap.add_argument('--out', metavar='PATH', help='write the payload as JSON, send nothing')
    ap.add_argument('--delete', action='store_true',
                    help='with --yes: delete everything shared from this machine\'s token')
    ap.add_argument('--forget', action='store_true',
                    help='drop the locally stored token without contacting the server')
    ap.add_argument('--api', default=os.environ.get('TOKENUSAGE_API') or DEFAULT_API,
                    help=f'API base URL (default {DEFAULT_API}, or $TOKENUSAGE_API)')
    ap.add_argument('--sessions-root', metavar='PATH', help='override ~/.codex/sessions')
    ap.add_argument('--fast', action='store_true', help='use every core instead of half')
    ap.add_argument('--procs', type=int, default=None, help='worker processes')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    api = a.api.rstrip('/')

    state = load_state()
    mine = endpoint_state(state, api)

    if a.forget:
        set_endpoint_state(state, api, None)
        save_state(state)
        print(f"forgot the token for {api}"
              + (f" (handle {mine['handle']})" if mine.get('handle') else ''))
        return 0

    if a.delete:
        if not mine.get('token'):
            print(f'nothing to delete: no share token for {api} on this machine', file=sys.stderr)
            return 2
        if not a.yes:
            print(f"would delete {mine.get('handle')!r} and everything it shared from {api}.\n"
                  f"re-run with --delete --yes to do it.")
            return 0
        try:
            status, body = request('DELETE', f'{api}/share', token=mine['token'])
        except OSError as exc:
            print(f'could not reach {api}: {exc}', file=sys.stderr)
            return 6
        if status != 200:
            print(_explain(status, body), file=sys.stderr)
            return 7
        set_endpoint_state(state, api, None)
        save_state(state)
        print(f"deleted {mine.get('handle')!r} from {api}")
        return 0

    handle = a.handle.strip().lower() if a.handle else None
    if not mine.get('token') and not handle and not a.out:
        # A dry run is still useful without one; only sending needs it.
        if a.yes:
            print('the first share needs a leaderboard name: add --handle NAME '
                  '(3-24 characters: a-z, 0-9, hyphens)', file=sys.stderr)
            return 2
    built = collect_payload(a, handle if handle != mine.get('handle') else None)
    if built is None:
        return 2
    payload, notes = built
    if not payload['days']:
        print('no recorded usage to share', file=sys.stderr)
        return 2

    shown = handle or mine.get('handle')
    print(describe(payload, notes, shown, api))

    if a.out:
        with open(a.out, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, indent=1)
        print(f'\nwrote the payload to {a.out}; nothing was sent')
        return 0
    if not a.yes:
        print('\ndry run: nothing was sent. re-run with --yes to share'
              + ('' if mine.get('token') or handle else ' (and --handle NAME the first time)')
              + '.')
        return 0

    try:
        status, body = request('POST', f'{api}/share', body=payload, token=mine.get('token'))
    except OSError as exc:
        print(f'\ncould not reach {api}: {exc}\n'
              f'  inside the Codex sandbox, the network is off unless you approve it for '
              f'this command.', file=sys.stderr)
        return 6
    if status not in (200, 201) or not isinstance(body, dict):
        print('\n' + _explain(status, body), file=sys.stderr)
        if status == 401:
            print('  the stored token is not accepted. `--forget` drops it; the next share '
                  'then registers a new handle.', file=sys.stderr)
        return 7

    entry = dict(mine)
    entry.update(handle=body.get('handle'), url=body.get('url'),
                 last_shared_at=payload['generated_at'])
    if body.get('token'):
        entry['token'] = body['token']
    set_endpoint_state(state, api, entry)
    save_state(state)
    verb = 'shared' if body.get('created') else 'updated'
    print(f"\n{verb}: {body.get('url')}")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\ninterrupted', file=sys.stderr)
        sys.exit(130)
