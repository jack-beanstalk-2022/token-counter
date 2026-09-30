"""Tests for the token-share skill: the payload, what it must never carry, and the transport.

Run with ``python scripts/test_share.py``. No network: the server is a stub on localhost.
"""
import contextlib
import datetime
import gzip
import base64
import http.server
import io
import json
import os
import stat
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(REPO, 'plugins', 'token-counter', 'skills', 'token-share', 'scripts')
sys.path.insert(0, SHARE)

import share  # noqa: E402
from tokencounter import analyze, ledger, rollout, worker  # noqa: E402

RESULTS = []
SECRET_PROMPT = 'PLEASE-NEVER-UPLOAD-THIS-PROMPT'
SECRET_CWD = '/home/someone/very-private-project'


def check(name, ok, detail=''):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f'\n        {detail}' if not ok else ''))


def _session(sid, stamps, inp=1000, cached=400, out=50, model='gpt-5-codex'):
    """A rollout with one response per timestamp, and content that must never be sent."""
    recs = [{'timestamp': stamps[0], 'type': 'session_meta',
             'payload': {'session_id': sid, 'id': sid, 'cwd': SECRET_CWD}}]
    for i, ts in enumerate(stamps):
        recs.append({'timestamp': ts, 'type': 'turn_context',
                     'payload': {'model': model, 'effort': 'high', 'cwd': SECRET_CWD}})
        recs.append({'timestamp': ts, 'type': 'response_item',
                     'payload': {'type': 'message', 'role': 'user',
                                 'content': [{'type': 'input_text', 'text': SECRET_PROMPT}]}})
        recs.append({'timestamp': ts, 'type': 'token_usage_record',
                     'payload': {'response_id': f'{sid}-r{i}', 'usage': {
                         'input_tokens': inp, 'cached_input_tokens': cached,
                         'output_tokens': out, 'reasoning_output_tokens': out // 2,
                         'total_tokens': inp + out}}})
    return recs


def _corpus(sessions):
    root = tempfile.mkdtemp()
    for sid, stamps, kw in sessions:
        day = stamps[0][:10].split('-')
        d = os.path.join(root, *day)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f'rollout-{stamps[0][:10]}T00-00-00-{sid}.jsonl'), 'w',
                  encoding='utf-8') as fh:
            for r in _session(sid, stamps, **kw):
                fh.write(json.dumps(r) + '\n')
    return root


def _build(root):
    results = {p: worker.metrics_only(p) for p in rollout.discover(root)}
    charged, counters = ledger.build(results)
    payload, notes = share.build_payload(results, charged, handle='tester')
    return results, charged, counters, payload, notes


def _utc(monkey=True):
    """Pin the local zone to UTC so local days are predictable."""
    os.environ['TZ'] = 'UTC'
    if hasattr(__import__('time'), 'tzset'):
        __import__('time').tzset()
    analyze._local_day.cache_clear()


# ---------------------------------------------------------------------------- payload

CORPUS = [
    # 3 responses, 10 min apart, then a 2-hour gap, then 2 more 5 min apart: 25 min active.
    ('11111111-aaaa', ['2026-08-31T10:00:00.000Z', '2026-08-31T10:10:00.000Z',
                       '2026-08-31T10:20:00.000Z', '2026-08-31T12:20:00.000Z',
                       '2026-08-31T12:25:00.000Z'], {}),
    ('22222222-bbbb', ['2026-09-01T09:00:00.000Z', '2026-09-01T09:01:00.000Z'],
     {'inp': 50_000, 'cached': 10_000, 'out': 2_000}),
    ('33333333-cccc', ['2026-09-01T23:50:00.000Z', '2026-09-02T00:10:00.000Z'], {}),
]


def test_payload_shape():
    _utc()
    results, charged, counters, p, notes = _build(_corpus(CORPUS))
    check('schema and client are stamped', p['schema'] == 1 and p['client']['name'] == 'token-counter')
    check('the handle rides along when given', p.get('handle') == 'tester')
    days = {d['date']: d for d in p['days']}
    check('days are the local days responses landed on',
          sorted(days) == ['2026-08-31', '2026-09-01', '2026-09-02'], str(sorted(days)))
    check('a day counts its responses and tokens',
          days['2026-08-31']['responses'] == 5 and days['2026-08-31']['input'] == 5000
          and days['2026-08-31']['cached'] == 2000 and days['2026-08-31']['output'] == 250,
          str(days['2026-08-31']))
    check('a session is counted on the day of its first response',
          days['2026-09-01']['sessions'] == 2 and days['2026-09-02']['sessions'] == 0,
          str({k: v['sessions'] for k, v in days.items()}))

    s = {x['id']: x for x in p['sessions']}
    first = s[share.session_hash('11111111-aaaa')]
    check('active time leaves out gaps over the idle cap', first['active_s'] == 25 * 60,
          str(first['active_s']))
    check('start and end are the first and last response',
          first['start'] == '2026-08-31T10:00:00Z' and first['end'] == '2026-08-31T12:25:00Z',
          str((first['start'], first['end'])))
    night = s[share.session_hash('33333333-cccc')]
    check('a session crossing midnight belongs to the day it started',
          night['day'] == '2026-09-01' and night['active_s'] == 20 * 60, str(night))
    check('the model is named', first['model'] == 'gpt-5-codex')
    check('session ids are one-way hashes, 16 hex characters',
          all(len(x['id']) == 16 and all(c in '0123456789abcdef' for c in x['id'])
              for x in p['sessions']))


def test_payload_agrees_with_the_report():
    """The leaderboard must show what the local report shows, day for day."""
    _utc()
    results, charged, counters, p, _ = _build(_corpus(CORPUS))
    model = analyze.analyze(results, charged, counters, scope={'label': 'test'})
    rep = {d['date']: (d['responses'], d['input'], d['cached'], d['output']) for d in model['daily']}
    ours = {d['date']: (d['responses'], d['input'], d['cached'], d['output']) for d in p['days']}
    check('every day matches the report model', rep == ours, f'report={rep}\nshare={ours}')


def test_nothing_private_is_sent():
    _utc()
    *_, p, _ = _build(_corpus(CORPUS))
    blob = json.dumps(p)
    check('no prompt text in the payload', SECRET_PROMPT not in blob)
    check('no working directory in the payload', SECRET_CWD not in blob and 'someone' not in blob)
    check('no raw session id in the payload', '11111111-aaaa' not in blob)
    check('only the documented keys are sent',
          set(p) == {'schema', 'client', 'generated_at', 'days', 'sessions', 'windows', 'handle'}
          and all(set(d) == {'date', 'responses', 'input', 'cached', 'output', 'reasoning',
                             'sessions'} for d in p['days'])
          and all(set(x) == {'id', 'day', 'start', 'end', 'active_s', 'responses', 'input',
                             'cached', 'output', 'reasoning', 'model'} for x in p['sessions']),
          json.dumps(p)[:400])
    *_, w, _ = _build(_corpus_file(_limit_records()))
    check('only the documented keys are sent per limit window',
          w['windows'] and all(set(x) == {'start', 'window_minutes', 'plan', 'first_pct',
                                          'peak_pct', 'responses', 'input', 'cached', 'output'}
                               for x in w['windows']),
          json.dumps(w['windows'])[:400])


def test_sessions_are_capped_per_month():
    _utc()
    # Gaps of 1..29 minutes, all inside the idle cap; session 28 has the longest.
    many = [(f'{i:08d}-dddd', [f'2026-09-03T{i % 24:02d}:00:00.000Z',
                               f'2026-09-03T{i % 24:02d}:{1 + i % 29:02d}:00.000Z'],
             {'inp': 1000 + i}) for i in range(40)]
    *_, p, notes = _build(_corpus(many))
    n = len(p['sessions'])
    check('at most twice the per-month cap is sent', n <= 2 * share.SESSIONS_PER_MONTH, str(n))
    check('the total session count is still reported', notes['sessions_total'] == 40)
    longest = max(p['sessions'], key=lambda x: x['active_s'])
    check('the longest session survives the cap', longest['active_s'] == 29 * 60, str(longest))
    biggest = max(p['sessions'], key=lambda x: x['input'])
    check('the biggest session survives the cap', biggest['input'] == 1039 * 2, str(biggest))
    check('every session is still counted in its day',
          sum(d['sessions'] for d in p['days']) == 40)


def test_damaged_counts_are_clamped():
    _utc()
    *_, p, notes = _build(_corpus([('44444444-eeee', ['2026-09-04T10:00:00.000Z'],
                                     {'inp': 10, 'cached': 20})]))
    check('cached above input is clamped, and counted',
          p['days'][0]['cached'] == 10 and notes['clamped_cached'] == 1, str(p['days']))


# ---------------------------------------------------------------------------- limit windows

WEEK = 7 * 86400
T0 = 1789000000     # 2026-09-10T00:26:40Z


def _tc(t, pct, resets_at, cum, inp=1000, cached=400, out=10, window=10080, plan='prolite'):
    """A `token_count` event: one response's usage beside the server's rate-limit snapshot."""
    iso = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace(
        '+00:00', 'Z')
    last = {'input_tokens': inp, 'cached_input_tokens': cached, 'output_tokens': out,
            'reasoning_output_tokens': 0, 'total_tokens': inp + out}
    total = {'input_tokens': cum, 'cached_input_tokens': 0, 'output_tokens': 0,
             'reasoning_output_tokens': 0, 'total_tokens': cum}
    return {'timestamp': iso, 'type': 'event_msg',
            'payload': {'type': 'token_count',
                        'info': {'last_token_usage': last, 'total_token_usage': total},
                        'rate_limits': {'limit_id': 'codex', 'plan_type': plan,
                                        'rate_limit_reached_type': None,
                                        'primary': {'used_percent': pct,
                                                    'window_minutes': window,
                                                    'resets_at': resets_at},
                                        'secondary': None}}}


def _limit_records(first_pcts=(0.0, 10.0, 25.0, 40.0), window=10080, plan='prolite'):
    """One consumed window, then an early reset into a second, then an idle slide."""
    recs = [{'timestamp': '2026-09-10T00:26:40Z', 'type': 'session_meta',
             'payload': {'session_id': 'W', 'id': 'W', 'cwd': SECRET_CWD}}]
    cum = 0
    for i, pct in enumerate(first_pcts):
        cum += 1000
        recs.append(_tc(T0 + i * 3600, pct, T0 + WEEK, cum, window=window, plan=plan))
    t1 = T0 + len(first_pcts) * 3600
    for i, pct in enumerate((0.0, 5.0)):
        cum += 1000
        recs.append(_tc(t1 + i * 3600, pct, t1 + WEEK, cum, window=window, plan=plan))
    # Idle: 0% throughout, the reset re-quoted as now + 7 days on every call.
    t2 = t1 + 3 * 3600
    for i in range(3):
        recs.append(_tc(t2 + i * 60, 0.0, t2 + i * 60 + WEEK, cum, inp=0, cached=0, out=0,
                        window=window, plan=plan))
    return recs


def _corpus_file(recs):
    root = tempfile.mkdtemp()
    d = os.path.join(root, '2026', '09', '10')
    os.makedirs(d)
    with open(os.path.join(d, 'rollout-2026-09-10T00-00-00-W.jsonl'), 'w',
              encoding='utf-8') as fh:
        for r in recs:
            fh.write(json.dumps(r) + '\n')
    return root


def test_limit_windows():
    _utc()
    *_, p, _ = _build(_corpus_file(_limit_records()))
    w = p['windows']
    check('each consumed weekly window is sent, and an idle slide is not',
          len(w) == 2, json.dumps(w))
    a, b = w
    check('a window starts where it was first seen, and the next where the percentage drops',
          a['start'] == share._iso(T0) and b['start'] == share._iso(T0 + 4 * 3600),
          f"{a['start']} {b['start']}")
    check('the plan and the reported percentages ride along',
          a['plan'] == 'prolite' and a['first_pct'] == 0 and a['peak_pct'] == 40
          and b['peak_pct'] == 5 and a['window_minutes'] == 10080, json.dumps(a))
    check('tokens are split at the reset, not pooled',
          (a['responses'], a['input'], a['cached'], a['output']) == (4, 4000, 1600, 40)
          and (b['input'], b['output']) == (2000, 20), json.dumps(w))
    check('windows never hold more than the days',
          sum(x['input'] for x in w) <= sum(d['input'] for d in p['days']))

    # Logs that begin partway through a week: the window's first reading says how much of
    # the limit was already gone before this machine saw anything.
    *_, p, _ = _build(_corpus_file(_limit_records(first_pcts=(30.0, 45.0))))
    check('a window first seen partway through keeps its first reading',
          p['windows'][0]['first_pct'] == 30 and p['windows'][0]['peak_pct'] == 45,
          json.dumps(p['windows'][0]))

    *_, p, _ = _build(_corpus_file(_limit_records(window=300)))
    check('only weekly windows are sent', p['windows'] == [], json.dumps(p['windows']))

    # The server refuses a window dated before Codex shipped, and a repeated start; either
    # would fail the whole share, so neither is sent.
    results = {p: worker.metrics_only(p) for p in rollout.discover(_corpus_file(_limit_records()))}
    real = analyze.rate_limit_windows
    fake = real(results, [], newest=None)
    early = dict(fake['windows'][0], reset_at=1735689600)          # 2025-01-01
    twin = dict(fake['windows'][1], reset_at=fake['windows'][0]['reset_at'])
    analyze.rate_limit_windows = lambda *a, **k: dict(fake, windows=[early, fake['windows'][0], twin])
    try:
        sent = share.limit_windows(results, [], T0 + WEEK)
    finally:
        analyze.rate_limit_windows = real
    check('windows the server would refuse are dropped, not the share',
          [w['start'] for w in sent] == [share._iso(T0)], json.dumps(sent))

    *_, p, _ = _build(_corpus(CORPUS))
    check('logs without rate limits send an empty list', p['windows'] == [])


# ---------------------------------------------------------------------------- transport

class Stub(http.server.BaseHTTPRequestHandler):
    calls = []
    token = 'tu1.' + 'A' * 20 + '.' + 'b' * 43

    def log_message(self, *a):
        pass

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n))
        auth = self.headers.get('Authorization')
        Stub.calls.append(('POST', self.path, auth, body))
        if auth and auth != f'Bearer {Stub.token}':
            return self._send(401, {'error': 'bad_token', 'message': 'not recognised'})
        if not auth:
            return self._send(201, {'handle': body['handle'], 'url': 'http://x/u/' + body['handle'],
                                    'token': Stub.token, 'created': True})
        return self._send(200, {'handle': body.get('handle') or 'tester',
                                'url': 'http://x/u/tester', 'created': False})

    def do_PUT(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n))
        auth = self.headers.get('Authorization')
        Stub.calls.append(('PUT', self.path, auth, body))
        if auth != f'Bearer {Stub.token}':
            return self._send(401, {'error': 'bad_token', 'message': 'not recognised'})
        page = gzip.decompress(base64.b64decode(body['html_gz']))
        self._send(200, {'handle': 'tester', 'url': 'http://x/r/tester', 'bytes': len(page)})

    def do_DELETE(self):
        Stub.calls.append(('DELETE', self.path, self.headers.get('Authorization'), None))
        self._send(200, {'deleted': True, 'handle': 'tester'})


@contextlib.contextmanager
def _server():
    srv = http.server.HTTPServer(('127.0.0.1', 0), Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    Stub.calls.clear()
    try:
        yield f'http://127.0.0.1:{srv.server_address[1]}/api'
    finally:
        srv.shutdown()


def _last(method):
    return next((c for c in reversed(Stub.calls) if c[0] == method), None)


def _page(call):
    return gzip.decompress(base64.b64decode(call[3]['html_gz'])).decode('utf-8')


def _run(root, home, *argv):
    import report as cli
    keep = os.environ.get('CODEX_HOME')
    os.environ['CODEX_HOME'] = home
    cli._OUT_DIR.clear()
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = share.main(['--sessions-root', root, '--procs', '1', '--quiet', *argv])
    finally:
        cli._OUT_DIR.clear()
        if keep is None:
            os.environ.pop('CODEX_HOME', None)
        else:
            os.environ['CODEX_HOME'] = keep
    return rc, out.getvalue(), err.getvalue()


def test_transport():
    _utc()
    root = _corpus(CORPUS)
    home = tempfile.mkdtemp()
    state = os.path.join(home, 'token-counter', 'share.json')
    with _server() as api:
        rc, out, _ = _run(root, home, '--api', api)
        check('a dry run sends nothing', rc == 0 and not Stub.calls and 'dry run' in out, out)
        check('a dry run says what would be shared', '2026-09' in out and 'never sent' in out, out)
        check('a dry run says what it sends about rate limits', 'limit window' in out, out)
        shared = os.path.join(home, 'token-counter', 'report-shared.html')
        check('a dry run writes the report page it would publish, and names it',
              os.path.exists(shared) and shared in out and 'anyone with the link' in out, out)
        with open(shared, encoding='utf-8') as fh:
            dry_page = fh.read()
        check('the shared page is the token report',
              dry_page.startswith('<!doctype html>') and 'Codex Token Report' in dry_page
              and 'data-style="clinical"' in dry_page)
        leaked = [x for x in (SECRET_PROMPT, SECRET_CWD, os.path.basename(SECRET_CWD),
                              *(sid for sid, _, _ in CORPUS), *(sid[:8] for sid, _, _ in CORPUS))
                  if x in dry_page]
        check('the shared page carries no prompt, directory or session id', not leaked, str(leaked))

        rc, _, err = _run(root, home, '--api', api, '--yes')
        check('a first share without a handle is refused locally',
              rc == 2 and not Stub.calls and '--handle' in err, err)

        rc, out, err = _run(root, home, '--api', api, '--handle', 'Tester', '--yes')
        check('a first share prints the profile URL, then the report URL last',
              rc == 0 and 'http://x/u/tester' in out
              and out.strip().endswith('report: http://x/r/tester'), out + err)
        check('the handle is sent lowercased', _last('POST')[3].get('handle') == 'tester')
        check('the first share sends no token', _last('POST')[2] is None)
        put = _last('PUT')
        check('the report page goes up after the numbers, under the new token',
              Stub.calls[-1] is put and put[1] == '/api/report'
              and put[2] == f'Bearer {Stub.token}' and put[3]['schema'] == 1)
        with open(shared, encoding='utf-8') as fh:
            check('the page sent is the page the dry run showed', _page(put) == fh.read())
        with open(state, encoding='utf-8') as fh:
            saved = json.load(fh)['endpoints'][api]
        check('the token is stored', saved.get('token') == Stub.token and saved['handle'] == 'tester')
        if os.name == 'posix':
            check('the token file is private', stat.S_IMODE(os.stat(state).st_mode) == 0o600,
                  oct(os.stat(state).st_mode))
        check('the token is never printed', Stub.token not in out + err)

        rc, out, _ = _run(root, home, '--api', api, '--yes')
        check('a later share authenticates and leaves the handle out',
              rc == 0 and _last('POST')[2] == f'Bearer {Stub.token}'
              and 'handle' not in _last('POST')[3], str(_last('POST')[:3]))

        n = len(Stub.calls)
        rc, out, _ = _run(root, home, '--api', api, '--yes', '--no-report')
        check('--no-report shares the numbers only',
              rc == 0 and [c[0] for c in Stub.calls[n:]] == ['POST'], str(Stub.calls[n:]))

        rc, out, _ = _run(root, home, '--api', api, '--yes', '--style', 'matisse')
        check('--style is the style the shared page opens in',
              rc == 0 and '<html lang="en" data-style="matisse">' in _page(_last('PUT'))
              and 'data-next="nocturne"' in _page(_last('PUT')), out)

        n = len(Stub.calls)
        rc, out, _ = _run(root, home, '--api', api, '--delete-report')
        check('--delete-report without --yes only says what it would do',
              rc == 0 and len(Stub.calls) == n and 'would take down' in out, out)
        rc, out, _ = _run(root, home, '--api', api, '--delete-report', '--yes')
        with open(state, encoding='utf-8') as fh:
            kept = json.load(fh)['endpoints'].get(api) or {}
        check('--delete-report takes the page down and keeps the token',
              rc == 0 and Stub.calls[-1][:2] == ('DELETE', '/api/report')
              and kept.get('token') == Stub.token and 'report_url' not in kept, out)

        n = len(Stub.calls)
        rc, out, _ = _run(root, home, '--api', api, '--delete')
        check('delete without --yes only says what it would do',
              rc == 0 and len(Stub.calls) == n and 'would delete' in out, out)
        rc, out, _ = _run(root, home, '--api', api, '--delete', '--yes')
        with open(state, encoding='utf-8') as fh:
            left = json.load(fh)['endpoints']
        check('delete calls the server and forgets the token',
              rc == 0 and Stub.calls[-1][:2] == ('DELETE', '/api/share') and api not in left,
              str(left))

        # A rejected token is reported, with the way out.
        with open(state, 'w', encoding='utf-8') as fh:
            json.dump({'endpoints': {api: {'token': 'tu1.' + 'Z' * 20 + '.' + 'z' * 43,
                                           'handle': 'tester'}}}, fh)
        rc, out, err = _run(root, home, '--api', api, '--yes')
        check('a rejected token explains --forget', rc == 7 and '--forget' in err, err)

        out_path = os.path.join(home, 'p.json')
        n = len(Stub.calls)
        rc, out, _ = _run(root, home, '--api', api, '--out', out_path)
        with open(out_path, encoding='utf-8') as fh:
            written = json.load(fh)
        check('--out writes the payload and sends nothing',
              rc == 0 and len(Stub.calls) == n and written['schema'] == 1, out)

    rc, _, err = _run(root, home, '--api', 'http://127.0.0.1:9/api', '--handle', 'x-y-z', '--yes',
                      '--forget')
    check('--forget works offline', rc == 0)
    rc, _, err = _run(root, home, '--api', 'http://127.0.0.1:9/api', '--handle', 'x-y-z', '--yes')
    check('an unreachable server is reported, not raised', rc == 6 and 'could not reach' in err, err)


def main():
    test_payload_shape()
    test_payload_agrees_with_the_report()
    test_nothing_private_is_sent()
    test_sessions_are_capped_per_month()
    test_damaged_counts_are_clamped()
    test_limit_windows()
    test_transport()
    bad = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f'\n{len(RESULTS) - bad}/{len(RESULTS)} passed')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
