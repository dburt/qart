"""Smoke test: steer bits for pjw.png, render, decode. Writes outputs to out/smoke/."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from qart.pipeline import Options, run  # noqa: E402
from qart.verify import verify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "smoke"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    img = Image.open(ROOT / "images" / "set" / "pjw.png")
    ok = True
    for name, opts in [
        ("pad-plain", Options(version=6, mask=2, free="pad", style="plain")),
        ("num-plain", Options(version=6, mask=2, free="num", style="plain")),
        ("pad-halftone-budget", Options(version=6, mask="auto", free="pad", style="halftone", spend="max")),
        ("pad-dots", Options(version=6, mask=2, free="pad", style="dots")),
        ("pad-dither", Options(version=6, mask=2, free="pad", style="plain", dither=True)),
    ]:
        r = run(img, opts)
        r.image.save(OUT / f"{name}.png")
        r.control_image().save(OUT / f"{name}-control.png")
        v = verify(r.image, r.expected_text)
        s = r.stats
        print(f"{name:<22} match {s['match']:.3f} (pre {s['match_pre_budget']:.3f}) ctrl {s['controlled_frac']:.2f} "
              f"budget {s['budget_used']}/{s['budget_limit']} fid {s['fidelity_module']:.2f}/{s['fidelity_sub']:.2f} "
              f"pass {v['pass_rate']:.2f} clean {v['clean_pass']}/4 t {s['t_total']:.1f}s expected_len {s['expected_len']}")
        for d, row in v["table"].items():
            bad = [k for k, val in row.items() if not val]
            if bad:
                print(f"    {d}: failed {bad}")
        if v["clean_pass"] < 2:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
