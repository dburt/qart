"""Technique stacks for the experiment matrix. Each is a name and the Options overrides."""
from __future__ import annotations

STACKS: dict[str, dict] = {
    # baselines: what existing tools do (no bit steering, ECL H, render tricks only)
    "base-H-ht33": dict(steer=False, ecl="H", free="none", style="halftone", halftone_sub=3, halftone_centre=1, mask="auto"),
    "base-H-dots": dict(steer=False, ecl="H", free="none", style="dots", dot_scale=0.35, mask="auto"),
    # bit steering alone
    "qart-L": dict(ecl="L", free="pad", style="plain"),
    "qart-L-num": dict(ecl="L", free="num", style="plain"),
    "qart-L-dither": dict(ecl="L", free="pad", style="plain", dither=True),
    # steering + error budget (spend 3 of 9 bytes per block at v8-L; 3 of 11 at M)
    "qart-L-budget": dict(ecl="L", free="pad", style="plain", spend=3, reserve=3),
    "qart-M-budget": dict(ecl="M", free="pad", style="plain", spend=3, reserve=3),
    # steering + halftone: ht33 = classic one-third centre; ht64 = two-thirds centre, error-diffusion surround
    "qart-L-ht33": dict(ecl="L", free="pad", style="halftone", halftone_sub=3, halftone_centre=1),
    "qart-L-ht64": dict(ecl="L", free="pad", style="halftone", halftone_sub=6, halftone_centre=4, halftone_dither="fs", module_px=12),
    # all three
    "qart-L-ht64-budget": dict(ecl="L", free="pad", style="halftone", halftone_sub=6, halftone_centre=4, halftone_dither="fs", module_px=12, spend=3, reserve=3),
    "qart-M-ht64-budget": dict(ecl="M", free="pad", style="halftone", halftone_sub=6, halftone_centre=4, halftone_dither="fs", module_px=12, spend=3, reserve=3),
    # steering + dots over the colour image
    "qart-L-dots": dict(ecl="L", free="pad", style="dots", dot_scale=0.5),
}

# budget sweep: how much of the per-block capacity (9 bytes at v8-L, 11 at v8-M) can be spent
for _s in (1, 2, 3, 4, 6):
    STACKS[f"sweep-L-s{_s}"] = dict(ecl="L", free="pad", style="plain", spend=_s, reserve=0)
    STACKS[f"sweep-L-ht60-s{_s}"] = dict(ecl="L", free="pad", style="halftone", halftone_sub=5, halftone_centre=3,
                                        halftone_contrast=0.6, module_px=10, spend=_s, reserve=0)
SWEEP = [k for k in STACKS if k.startswith("sweep-")]

# A smaller set for quick runs
QUICK = ["base-H-ht33", "qart-L", "qart-L-budget", "qart-L-ht64-budget"]
