"""Schema claims checked against a live corpus.

    python scripts/verify_schema.py              # ~/.codex/sessions, or $CODEX_HOME
    python scripts/verify_schema.py PATH         # a corpus somewhere else
"""
import glob, json, os, collections, sys


def sessions_root():
    """Honours ``CODEX_HOME`` and an explicit argument, the way the plugin itself does."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    home = os.environ.get('CODEX_HOME') or os.path.join(os.path.expanduser('~'), '.codex')
    return os.path.join(home, 'sessions')


ROOT = sessions_root()
files = sorted(glob.glob(os.path.join(ROOT, '**', 'rollout-*.jsonl'), recursive=True))
if not files:
    print(f'no rollout-*.jsonl under {ROOT}', file=sys.stderr)
    print('  set CODEX_HOME, or pass the sessions directory as an argument',
          file=sys.stderr)
    raise SystemExit(2)
print(f'{len(files):,} rollout files under {ROOT}')
both = 0
only_new = 0
only_old = 0
neither = 0
dup_pairs = 0
inp_new = inp_old = 0
legacy_has_cw = 0
legacy_total = 0
ver_new = collections.Counter()
first_resp_cached = 0
viol_total = 0
n_new = n_old = 0

for f in files:
    has_new = has_old = False
    ver = None
    recs_new = []
    recs_old = []
    for raw in open(f, 'rb'):
        if b'token_usage' not in raw and b'cli_version' not in raw:
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue
        p = o.get('payload') or {}
        if not isinstance(p, dict):
            continue
        if o.get('type') == 'session_meta' and ver is None:
            ver = p.get('cli_version')
        if o.get('type') == 'token_usage_record':
            has_new = True
            u = p.get('usage') or {}
            recs_new.append((p.get('response_id'), u))
        elif p.get('type') == 'token_count':
            info = p.get('info')
            if not info:
                continue
            u = info.get('last_token_usage')
            if not u:
                continue
            has_old = True
            recs_old.append(u)
            legacy_total += 1
            if 'cache_write_input_tokens' in u:
                legacy_has_cw += 1
            t, i, oo = u.get('total_tokens', 0), u.get('input_tokens', 0), u.get('output_tokens', 0)
            if t != i + oo:
                viol_total += 1
    if has_new and has_old:
        both += 1
        ver_new[ver] += 1
        # do the two streams mirror each other?
        if len(recs_new) == len(recs_old):
            same = sum(1 for (rid, a), b in zip(recs_new, recs_old)
                       if a.get('input_tokens') == b.get('input_tokens')
                       and a.get('output_tokens') == b.get('output_tokens'))
            if same == len(recs_new):
                dup_pairs += 1
    elif has_new:
        only_new += 1; ver_new[ver] += 1
    elif has_old:
        only_old += 1
    else:
        neither += 1
    for rid, u in recs_new:
        inp_new += u.get('input_tokens', 0)
    for u in recs_old:
        inp_old += u.get('input_tokens', 0)
    n_new += len(recs_new); n_old += len(recs_old)
    if recs_new and recs_new[0][1].get('cached_input_tokens', 0) > 0:
        first_resp_cached += 1

print(f"files={len(files)}  both={both}  only_new={only_new}  only_old={only_old}  none={neither}")
print(f"  of 'both' files, streams mirror exactly: {dup_pairs}/{both}")
print(f"responses: new-stream={n_new}  legacy-stream={n_old}")
print(f"input tokens: new={inp_new/1e9:.3f}B  legacy={inp_old/1e9:.3f}B  naive-sum={(inp_new+inp_old)/1e9:.3f}B")
print(f"CLI versions emitting token_usage_record: {dict(sorted(ver_new.items(), key=lambda kv: str(kv[0])))}")
print(f"legacy snapshots with cache_write_input_tokens present: {legacy_has_cw}/{legacy_total}")
print(f"legacy snapshots violating total==input+output: {viol_total}")
print(f"files whose FIRST new-stream response already has cached>0: {first_resp_cached}")


# ---------------------------------------------------------------- rate-limit schema claims
#
# Every structural claim the rate-limit reader depends on, checked against the live corpus.
# These are the assumptions that, if they drift, silently mis-draw the weekly-limit chart.

import datetime


def _ep(ts):
    try:
        return datetime.datetime.fromisoformat(str(ts).replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


rl_snapshots = 0
rl_carriers = collections.Counter()
rl_keys = collections.Counter()
win_shapes = collections.Counter()
win_lengths = collections.Counter()
slot_by_length = collections.Counter()
plans = collections.Counter()
reset_absent = 0
relative_reset = 0
weekly_obs = []          # (t, used_percent, resets_at)

for f in files:
    for raw in open(f, 'rb'):
        if b'rate_limits' not in raw:
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue
        p = o.get('payload') or {}
        if not isinstance(p, dict):
            continue
        rl = p.get('rate_limits')
        if not isinstance(rl, dict):
            continue
        rl_carriers[(o.get('type'), p.get('type'))] += 1
        if o.get('type') != 'event_msg' or p.get('type') != 'token_count':
            continue          # content that merely mentions the word, not a snapshot
        rl_snapshots += 1
        rl_keys[tuple(sorted(rl.keys()))] += 1
        plans[rl.get('plan_type')] += 1
        t = _ep(o.get('timestamp'))
        for slot in ('primary', 'secondary'):
            w = rl.get(slot)
            if not isinstance(w, dict):
                continue
            win_shapes[tuple(sorted(w.keys()))] += 1
            wm = w.get('window_minutes')
            win_lengths[wm] += 1
            slot_by_length[(wm, slot)] += 1
            if 'resets_at' not in w:
                if 'resets_in_seconds' in w:
                    relative_reset += 1
                else:
                    reset_absent += 1
            if wm == 10080 and w.get('resets_at'):
                weekly_obs.append((t, w.get('used_percent'), w['resets_at']))

print()
print('-- rate limits ' + '-' * 60)
print(f"snapshots (event_msg/token_count): {rl_snapshots}")
print(f"carriers seen: {dict(rl_carriers)}")
print(f"rate_limits key shapes: {len(rl_keys)}")
for k, n in rl_keys.most_common():
    print(f"    {n:7,}  {k}")
print(f"window key shapes: {dict(win_shapes)}")
print(f"window lengths (minutes): {dict(win_lengths)}")
print(f"which slot holds each length: {dict(slot_by_length)}")
print(f"plan_type values: {dict(plans)}")
print(f"windows with neither resets_at nor resets_in_seconds: {reset_absent}")
print(f"windows quoting a RELATIVE resets_in_seconds: {relative_reset}")

# The claim the window reconstruction rests on: a window that is being consumed holds its
# reset still, while an idle one re-quotes it as now+7d on every call.
weekly_obs.sort(key=lambda x: (x[0] or 0))
by_reset = {}
for t, pct, ra in weekly_obs:
    d = by_reset.setdefault(ra, {'first': t, 'last': t, 'pmax': pct or 0})
    d['last'] = t
    d['pmax'] = max(d['pmax'], pct or 0)
consumed = {ra: d for ra, d in by_reset.items() if d['pmax'] > 0}
idle = {ra: d for ra, d in by_reset.items() if d['pmax'] == 0}
print(f"\nweekly observations: {len(weekly_obs):,}")
print(f"distinct resets_at values: {len(by_reset):,}  "
      f"(consumed {len(consumed):,}, never-used {len(idle):,})")
lead = [ra - d['first'] for ra, d in idle.items() if d['first']]
if lead:
    print(f"never-used windows quote reset - first_seen: "
          f"min {min(lead)/86400:.3f}d  max {max(lead)/86400:.3f}d   "
          f"(the now+7d slide: this is why clustering is required)")
spans = sorted(d['last'] - d['first'] for d in consumed.values() if d['first'] and d['last'])
if spans:
    print(f"consumed windows hold one resets_at for up to {max(spans)/86400:.2f}d "
          f"(median {spans[len(spans)//2]/3600:.1f}h)")
