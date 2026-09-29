"""Tests for the token-share skill: the payload, what it must never carry, and the transport.

Run with ``python scripts/test_share.py``. No network: the server is a stub on localhost.
"""
import contextlib
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
          set(p) == {'schema', 'client', 'generated_at', 'days', 'sessions', 'handle'}
          and all(set(d) == {'date', 'responses', 'input', 'cached', 'output', 'reasoning',
                             'sessions'} for d in p['days'])
          and all(set(x) == {'id', 'day', 'start', 'end', 'active_s', 'responses', 'input',
                             'cached', 'output', 'reasoning', 'model'} for x in p['sessions']),
          json.dumps(p)[:400])


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

        rc, _, err = _run(root, home, '--api', api, '--yes')
        check('a first share without a handle is refused locally',
              rc == 2 and not Stub.calls and '--handle' in err, err)

        rc, out, err = _run(root, home, '--api', api, '--handle', 'Tester', '--yes')
        check('a first share registers and prints the profile URL',
              rc == 0 and out.strip().endswith('http://x/u/tester'), out + err)
        check('the handle is sent lowercased', Stub.calls[-1][3].get('handle') == 'tester')
        check('the first share sends no token', Stub.calls[-1][2] is None)
        with open(state, encoding='utf-8') as fh:
            saved = json.load(fh)['endpoints'][api]
        check('the token is stored', saved.get('token') == Stub.token and saved['handle'] == 'tester')
        if os.name == 'posix':
            check('the token file is private', stat.S_IMODE(os.stat(state).st_mode) == 0o600,
                  oct(os.stat(state).st_mode))
        check('the token is never printed', Stub.token not in out + err)

        rc, out, _ = _run(root, home, '--api', api, '--yes')
        check('a later share authenticates and leaves the handle out',
              rc == 0 and Stub.calls[-1][2] == f'Bearer {Stub.token}'
              and 'handle' not in Stub.calls[-1][3], str(Stub.calls[-1][:3]))

        rc, out, _ = _run(root, home, '--api', api, '--delete')
        check('delete without --yes only says what it would do',
              rc == 0 and Stub.calls[-1][0] == 'POST' and 'would delete' in out, out)
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
    test_transport()
    bad = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f'\n{len(RESULTS) - bad}/{len(RESULTS)} passed')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
