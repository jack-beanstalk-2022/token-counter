"""Regression tests for the ledger's response-identity discriminators.

Cases 1 and 2 are the counterexamples raised in round 3 of review: usage-tuple equality is
not response identity. Cases 3 and 4 pin the behaviour that must be preserved. Case 5 pins
the round-4 fix: a compaction in the middle of replayed history must not halt matching.

These run end to end -- synthetic rollouts on disk, through the real extractor and the real
ledger -- so an extraction change cannot silently break the guarantee.
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'plugins', 'token-counter', 'skills', 'token-report', 'scripts'))

from tokencounter import ledger, rollout, worker  # noqa: E402


def meta(thread_id, ts, session='S', parent=None):
    t = f'2026-01-01T00:00:{ts:02d}.000Z'
    pl = {'session_id': session, 'id': thread_id, 'timestamp': t}
    if parent:
        pl['parent_thread_id'] = parent
    return {'timestamp': t, 'type': 'session_meta', 'payload': pl}


def snap(ts, inp, cum, out=10):
    """legacy token_count: per-response `inp`, cumulative `cum`."""
    return {'timestamp': f'2026-01-01T00:00:{ts:02d}.000Z', 'type': 'event_msg',
            'payload': {'type': 'token_count', 'info': {
                'last_token_usage': {'input_tokens': inp, 'cached_input_tokens': 0,
                                     'output_tokens': out, 'reasoning_output_tokens': 0,
                                     'total_tokens': inp + out},
                'total_token_usage': {'input_tokens': cum, 'cached_input_tokens': 0,
                                      'output_tokens': out, 'reasoning_output_tokens': 0,
                                      'total_tokens': cum + out}}}}


def ctx(ts, total):
    """A context snapshot: zero in, zero out, positive total. Not a response."""
    return {'timestamp': f'2026-01-01T00:00:{ts:02d}.000Z', 'type': 'event_msg',
            'payload': {'type': 'token_count', 'info': {
                'last_token_usage': {'input_tokens': 0, 'cached_input_tokens': 0,
                                     'output_tokens': 0, 'reasoning_output_tokens': 0,
                                     'total_tokens': total},
                'total_token_usage': {'input_tokens': total, 'cached_input_tokens': 0,
                                      'output_tokens': 0, 'reasoning_output_tokens': 0,
                                      'total_tokens': total}}}}


def explicit(ts, rid, inp, cached=0, out=10):
    return {'timestamp': f'2026-01-01T00:00:{ts:02d}.000Z', 'type': 'token_usage_record',
            'payload': {'response_id': rid, 'usage': {
                'input_tokens': inp, 'cached_input_tokens': cached,
                'cache_write_input_tokens': 0, 'output_tokens': out,
                'reasoning_output_tokens': 0, 'total_tokens': inp + out}}}


def run(files):
    d = pathlib.Path(tempfile.mkdtemp())
    for name, recs in files.items():
        (d / name).write_text(
            ''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in recs),
            encoding='utf-8')
    paths = rollout.discover(str(d))
    data = {p: worker.metrics_only(p) for p in paths}
    charged, counters = ledger.build(data)
    return counters


CASES = [
    ('same thread repeats a usage value, cumulative advances -> 2 distinct responses',
     {'rollout-root.jsonl': [meta('root', 0), snap(1, 1000, 1000), snap(2, 1000, 2000)]},
     {'responses': 2}),

    ('independent sibling threads, identical usage and cumulative -> 2 distinct responses',
     {'rollout-root.jsonl': [meta('root', 0)],
      'rollout-a.jsonl': [meta('a', 1), snap(2, 1000, 1000)],
      'rollout-b.jsonl': [meta('b', 3), snap(4, 1000, 1000)]},
     {'responses': 2}),

    ('true immediate repeat, identical cumulative -> 1 response',
     {'rollout-root.jsonl': [meta('root', 0), snap(1, 1000, 1000), snap(1, 1000, 1000)]},
     {'responses': 1, 'legacy_repeat': 1}),

    ('fork replays a 3-record ancestor history -> parent 3, child 1 new = 4',
     {'rollout-p.jsonl': [meta('p', 0), snap(1, 100, 100), snap(2, 200, 300),
                          snap(3, 300, 600)],
      'rollout-c.jsonl': [meta('c', 4, parent='p'), snap(5, 100, 100), snap(6, 200, 300),
                          snap(7, 300, 600), snap(8, 400, 1000)]},
     {'responses': 4, 'inherited': 3}),

    ('a compaction inside replayed history must not halt matching (round-4 blocker)',
     {'rollout-p.jsonl': [meta('p', 0), snap(1, 100, 100), ctx(2, 300),
                          snap(3, 200, 300), snap(4, 300, 600)],
      'rollout-c.jsonl': [meta('c', 5, parent='p'), snap(6, 100, 100), ctx(7, 300),
                          snap(8, 200, 300), snap(9, 300, 600), snap(10, 400, 1000)]},
     {'responses': 4, 'inherited': 3}),

    ('a context snapshot is never a response',
     {'rollout-root.jsonl': [meta('root', 0), snap(1, 100, 100), ctx(2, 5000)]},
     {'responses': 1, 'context_snapshots': 1}),

    ('explicit stream wins and is deduplicated by response_id',
     {'rollout-root.jsonl': [meta('root', 0),
                             explicit(1, 'r1', 500), snap(1, 500, 500),
                             explicit(2, 'r1', 500),
                             explicit(3, 'r2', 900), snap(3, 900, 1400)]},
     {'responses': 2, 'files_explicit': 1, 'files_legacy': 0}),

    ('a fork child replaying the explicit stream is not charged twice',
     {'rollout-p.jsonl': [meta('p', 0), explicit(1, 'r1', 100), explicit(2, 'r2', 200)],
      'rollout-c.jsonl': [meta('c', 3), explicit(4, 'r1', 100), explicit(5, 'r2', 200),
                          explicit(6, 'r3', 300)]},
     {'responses': 3, 'cross_file_response_id': 2}),

    ('sibling threads with distinct response ids are both charged',
     {'rollout-p.jsonl': [meta('p', 0), explicit(1, 'r1', 100)],
      'rollout-c.jsonl': [meta('c', 2), explicit(3, 'r2', 100)]},
     {'responses': 2, 'cross_file_response_id': 0}),

    ('a singleton replay match is ambiguous: charged, and disclosed',
     {'rollout-p.jsonl': [meta('p', 0), snap(1, 100, 100), snap(2, 777, 877)],
      'rollout-c.jsonl': [meta('c', 3, parent='p'), snap(4, 777, 877),
                          snap(5, 400, 1277)]},
     {'responses': 4, 'inherited': 0, 'ambiguous': 1}),

    ('unrelated siblings sharing a multi-record run are NOT treated as a fork '
     '(round-6 blocker)',
     {'rollout-a.jsonl': [meta('a', 0), snap(1, 100, 100), snap(2, 200, 300)],
      'rollout-b.jsonl': [meta('b', 3), snap(4, 100, 100), snap(5, 200, 300)]},
     {'responses': 4, 'inherited': 0}),

    ('a grandchild replaying an inherited run is still caught',
     {'rollout-p.jsonl': [meta('p', 0), snap(1, 100, 100), snap(2, 200, 300)],
      'rollout-c.jsonl': [meta('c', 3, parent='p'), snap(4, 100, 100), snap(5, 200, 300),
                          snap(6, 300, 600)],
      'rollout-g.jsonl': [meta('g', 7, parent='c'), snap(8, 100, 100), snap(9, 200, 300),
                          snap(10, 300, 600), snap(11, 400, 1000)]},
     {'responses': 4, 'inherited': 5}),

    ('a corrupt complete record is counted, not silently dropped',
     {'rollout-x.jsonl': [meta('x', 0), snap(1, 100, 100)]},
     {'responses': 1}),
]


def main():
    bad = 0
    for name, files, expected in CASES:
        got = run(files)
        fails = {k: (v, got.get(k, 0)) for k, v in expected.items() if got.get(k, 0) != v}
        bad += bool(fails)
        print(f"[{'FAIL' if fails else 'PASS'}] {name}")
        if fails:
            for k, (want, have) in fails.items():
                print(f'        {k}: expected {want}, got {have}')
    print(f'\n{len(CASES) - bad}/{len(CASES)} passed')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
