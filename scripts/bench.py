"""Full-pipeline benchmark, with the two parallelism axes separated.

Unlike the earlier draft this measures the **whole** pipeline the plugin actually runs --
parse, classify, tokenize, image dimensions, prompt reconstruction, resend costing, then
ledger, analysis and HTML render -- not just extraction plus BPE.

Process count and ``encode_ordinary_batch(num_threads=...)`` are independent axes and are
reported separately; conflating them produced a misleading table once already.

    python scripts/bench.py              # the standard grid
    python scripts/bench.py --quick      # default and --fast only
"""
import argparse
import concurrent.futures as cf
import functools
import os
import sys
import time

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'plugins', 'token-counter', 'skills', 'token-report', 'scripts')
sys.path.insert(0, LIB)

from tokencounter import analyze, ledger, render, rollout, worker  # noqa: E402

GRID = ((1, 1), (1, 8), (4, 1), (8, 1), (16, 1), (8, 4), (8, 8))
QUICK = ((8, 1), (8, 4))


def extract(files, procs, threads):
    fn = functools.partial(worker.process, num_threads=threads)
    t0 = time.time()
    results = {}
    cpu = 0.0
    if procs > 1:
        with cf.ProcessPoolExecutor(max_workers=procs) as ex:
            for r in ex.map(fn, files, chunksize=4):
                results[r['path']] = r
                cpu += r.get('cpu_s') or 0.0
    else:
        for p in files:
            r = fn(p)
            results[r['path']] = r
            cpu += r.get('cpu_s') or 0.0
    return results, time.time() - t0, cpu


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--since')
    a = ap.parse_args()

    files = rollout.discover(since=a.since)
    total = sum(os.path.getsize(f) for f in files)
    print(f'corpus: {len(files):,} files, {total/1e9:.2f} GB\n')

    print(f"{'proc':>5} {'thr':>4} {'extract':>9} {'cpu-s':>8} {'util':>7} {'MB/s':>7}")
    last = None
    for procs, thr in (QUICK if a.quick else GRID):
        results, wall, cpu = extract(files, procs, thr)
        print(f'{procs:>5} {thr:>4} {wall:>8.1f}s {cpu:>7.0f}s '
              f'{cpu/wall:>6.1f}x {total/1e6/wall:>6.0f}')
        last = results

    print()
    t0 = time.time()
    charged, counters = ledger.build(last)
    t_ledger = time.time() - t0
    t0 = time.time()
    model = analyze.analyze(last, charged, counters, scope={'label': 'bench'})
    t_analyze = time.time() - t0
    t0 = time.time()
    html = render.render(model)
    t_render = time.time() - t0

    t = model['totals']
    print(f'ledger          {t_ledger:>7.2f}s')
    print(f'analysis        {t_analyze:>7.2f}s')
    print(f'render          {t_render:>7.2f}s   ({len(html)/1e3:.0f} KB)')
    print()
    print(f"responses       {t['responses']:>12,}")
    print(f"recorded input  {t['input']:>12,}")
    print(f"unique content  {t['unique_tokens']:>12,}")
    print(f"amplification   {t['amplification']:>12.1f}x")
    peak = max((r.get('size') or 0) for r in last.values())
    print(f'largest file    {peak/1e6:>12.0f} MB')


if __name__ == '__main__':
    main()
