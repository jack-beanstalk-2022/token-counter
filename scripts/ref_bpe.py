"""Pure-Python BPE, kept as a correctness oracle for the vendored tokenizer.

Not a runtime path. It produces byte-identical output to tiktoken but runs at roughly a
third of the speed, and it still needs the third-party `regex` module because the
`o200k_base` split pattern uses `\\p{L}`-style Unicode property classes that the standard
library's `re` cannot compile. Since neither option is dependency-free, the faster one wins.

Reads the vendored vocabulary, so it works offline like everything else.

    python scripts/ref_bpe.py
"""
import functools
import os
import sys
import time

import regex

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'plugins', 'token-counter', 'skills', 'token-report', 'scripts')
sys.path.insert(0, LIB)

from tiktoken.load import load_tiktoken_bpe                      # noqa: E402
from tokencounter import encoding as tcenc                       # noqa: E402

RANKS = load_tiktoken_bpe(tcenc.vendor_path())
PAT = regex.compile(tcenc.O200K_PAT)

SAMPLE = (
    'The quick brown fox\n\tjumps over 1234 lazy dogs.\r\n'
    'def f(x): return x ** 2  # éèê 中文 \U0001f600\n'
    '  trailing   spaces   \nhttps://example.com/path?q=1&r=2 "double" `tick`\n'
) * 40


def bpe(piece, ranks=RANKS):
    parts = [bytes([b]) for b in piece]
    while len(parts) > 1:
        best, bi = None, -1
        for i in range(len(parts) - 1):
            r = ranks.get(parts[i] + parts[i + 1])
            if r is not None and (best is None or r < best):
                best, bi = r, i
        if bi < 0:
            break
        parts[bi:bi + 2] = [parts[bi] + parts[bi + 1]]
    return [ranks[p] for p in parts]


@functools.lru_cache(maxsize=1 << 16)
def _cached(piece):
    return (RANKS[piece],) if piece in RANKS else tuple(bpe(piece))


def encode(text):
    out = []
    for m in PAT.findall(text):
        out.extend(_cached(m.encode()))
    return out


def main():
    enc = tcenc.load()
    t0 = time.time()
    ours = encode(SAMPLE)
    t_ours = time.time() - t0
    t0 = time.time()
    theirs = enc.encode_ordinary(SAMPLE)
    t_theirs = time.time() - t0
    nbytes = len(SAMPLE.encode())
    print(f'sample           : {nbytes:,} bytes')
    print(f'pure python      : {len(ours):,} tokens  {t_ours:.3f}s  '
          f'{nbytes/1e6/max(t_ours,1e-9):.2f} MB/s')
    print(f'vendored tiktoken: {len(theirs):,} tokens  {t_theirs:.3f}s  '
          f'{nbytes/1e6/max(t_theirs,1e-9):.2f} MB/s')
    print(f'identical        : {ours == theirs}')
    return 0 if ours == theirs else 1


if __name__ == '__main__':
    sys.exit(main())
