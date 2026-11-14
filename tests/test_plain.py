"""Plain encoder must produce codes every decoder reads, for many versions, levels and masks."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qart.encode import encode_plain  # noqa: E402
from qart.render import render_plain  # noqa: E402
from qart.verify import DECODERS, to_gray  # noqa: E402

PAYLOAD = "https://example.com/#"


def main() -> int:
    failures = []
    cases = [(v, e, m) for v in (1, 2, 3, 5, 6, 7, 8, 10, 12, 15, 20) for e in range(4) for m in (0, 2, 5, 7)]
    skipped = 0
    for v, e, m in cases:
        try:
            plan, matrix, _ = encode_plain(PAYLOAD, v, e, m, rotation=(v + m) % 4)
        except ValueError:
            skipped += 1
            continue
        img = render_plain(matrix, module_px=6)
        gray = to_gray(img)
        for name in DECODERS.names:
            t = DECODERS.decode(name, gray)
            if t != PAYLOAD:
                failures.append((v, e, m, name, t))
    print(f"{len(cases) - skipped} codes × {len(DECODERS.names)} decoders ({skipped} too small), wechat mode: {DECODERS._wechat_note!r}")
    if failures:
        from collections import Counter
        by = Counter(f[3] for f in failures)
        print("failures by decoder:", dict(by))
        for f in failures[:20]:
            print("  FAIL", f)
        return 1
    print("all decoded correctly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
