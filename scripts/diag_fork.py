"""Fork-replay diagnostic against the known parent/child pair.

Independent of the ledger's own bookkeeping: it re-derives the leading-run match directly so
the exclusion count in section 2.5 has a second witness rather than only the code that
produces it.

    python scripts/diag_fork.py                        # the documented pair, looked up by id
    python scripts/diag_fork.py PARENT.jsonl CHILD.jsonl

The pair below is the fork section 2.5 was measured against. It is resolved by session id
under the sessions root rather than by absolute path, so the diagnostic runs wherever that
corpus lives; pass two paths to witness a different fork.
"""
import collections
import glob
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'plugins', 'token-counter', 'skills', 'token-report', 'scripts')
sys.path.insert(0, LIB)

from tokencounter import ledger, worker                          # noqa: E402

# Session ids, not paths: the same pair sits at a different absolute path on every machine.
PARENT_ID = '019fa0e6-4d49-72a3-ac85-f1078f2c0207'
CHILD_ID = '019fa1a4-8536-7202-8134-a4ee0f95a1d5'


def sessions_root():
    """The corpus location, honouring ``CODEX_HOME`` the way the plugin itself does."""
    home = os.environ.get('CODEX_HOME') or os.path.join(os.path.expanduser('~'), '.codex')
    return os.path.join(home, 'sessions')


def find(session_id, root):
    hits = glob.glob(os.path.join(root, '**', f'rollout-*-{session_id}.jsonl'), recursive=True)
    return sorted(hits)[0] if hits else None


def resolve():
    """``(parent, child)`` paths, or ``None`` after explaining what was not found."""
    argv = sys.argv[1:]
    if len(argv) == 2:
        return argv
    if argv:
        print('usage: diag_fork.py [PARENT.jsonl CHILD.jsonl]', file=sys.stderr)
        return None
    root = sessions_root()
    pair = [(PARENT_ID, find(PARENT_ID, root)), (CHILD_ID, find(CHILD_ID, root))]
    missing = [sid for sid, path in pair if not path]
    if missing:
        print(f'not found under {root}: {", ".join(missing)}\n'
              '  this is the specific fork section 2.5 was measured against, so it only\n'
              '  resolves on a machine holding that corpus.  Pass two rollout paths to\n'
              '  witness a fork in your own corpus instead.', file=sys.stderr)
        return None
    return [path for _, path in pair]


def normalized(path):
    r = worker.metrics_only(path)
    norm, n_rep, rep_in, n_ctx = ledger.normalize(r['legacy'])
    return r, norm, n_rep, n_ctx


def main():
    resolved = resolve()
    if not resolved:
        return 2
    parent, child = resolved

    for p in (parent, child):
        if not os.path.isfile(p):
            print(f'missing: {p}', file=sys.stderr)
            return 2

    pr, pn, p_rep, p_ctx = normalized(parent)
    cr, cn, c_rep, c_ctx = normalized(child)
    pk = [k for k, _ in pn]
    ck = [k for k, _ in cn]

    print(f'parent: session={pr["session_id"]} ts={pr["started_at"]} '
          f'raw={len(pr["legacy"])} normalized={len(pk)} '
          f'(repeats {p_rep}, context {p_ctx})')
    print(f'child : session={cr["session_id"]} ts={cr["started_at"]} '
          f'raw={len(cr["legacy"])} normalized={len(ck)} '
          f'(repeats {c_rep}, context {c_ctx})')
    print(f'same session: {pr["session_id"] == cr["session_id"]}   '
          f'parent earlier: {(pr["started_at"] or "") < (cr["started_at"] or "")}')

    lead = ledger.longest_leading_replay(ck, pk)
    print(f'\nlongest LEADING run of child matching a contiguous run in parent: {lead}')

    pset = collections.Counter(pk)
    print(f'child records matching parent ANYWHERE (position-free): '
          f'{sum(1 for k in ck if pset[k])}')
    pos = [i for i, k in enumerate(pk) if ck and k == ck[0]]
    print(f'child[0] occurs in parent at indices: {pos[:10]}')

    inherited = sum(rec["last"].get("input_tokens") or 0 for _, rec in cn[:lead])
    caught = lead >= ledger.MIN_REPLAY_RUN
    print(f'\nMIN_REPLAY_RUN={ledger.MIN_REPLAY_RUN} -> '
          f'{"CAUGHT" if caught else "MISSED"}: {lead} records / '
          f'{inherited:,} input tokens excluded')
    return 0 if caught else 1


if __name__ == '__main__':
    sys.exit(main())
