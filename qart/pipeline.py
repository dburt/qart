"""End-to-end: plan → target → solve → budget → render → metrics.

Split into a solve stage (slow, cached by the app) and a finish stage (budget + render, fast).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields
from functools import lru_cache

import numpy as np
from PIL import Image

from .bits import Message, layout_message, min_version
from .budget import Ledger, spend_budget
from .plan import DATA, Plan, build_plan, matrix_from_codewords
from .render import (module_colours, render_control, render_dots, render_halftone, render_halftone_px,
                     render_plain, render_residual)
from .solve import SolveResult, solve
from .tables import ecl_index
from .target import Target, build_target

# option names that affect the solve stage; anything else only affects budget/render
SOLVE_FIELDS = ("payload", "version", "ecl", "mask", "rotation", "free", "steer", "policy", "data_only",
                "dither", "outside", "threshold", "contrast", "brightness", "autocontrast", "scale", "dx", "dy", "seed")


@dataclass
class Options:
    payload: str = "https://example.com/#"
    version: int | str = 8  # int or "min"
    ecl: str = "L"
    mask: int | str = "auto"  # 0..7 or "auto"
    rotation: int | str = 0  # 0..3 or "auto"
    free: str = "pad"  # pad | num | none
    steer: bool = True
    policy: str = "hi"  # hi | lo | rand
    data_only: bool = False
    dither: bool = False
    outside: str = "white"  # white | free
    threshold: int | str = "otsu"  # luminance cut for dark modules, or "otsu"
    contrast: float = 1.0
    brightness: float = 1.0
    autocontrast: bool = False
    scale: float = 1.0
    dx: int = 0
    dy: int = 0
    spend: int | str = 0  # bytes per block, or "max"
    reserve: int = 3
    style: str = "plain"  # plain | halftone | dots
    halftone_dither: str = "bayer"
    halftone_sub: int = 3  # sub-cells per module side (grid renderer)
    halftone_centre: int = 1  # centre cells carrying the module value (grid renderer)
    halftone_contrast: float = 1.0  # contrast of the image cells around the centre
    halftone_centre_frac: float | None = None  # if set, use the pixel renderer with this centre width
    halftone_centre_alpha: float = 1.0
    halftone_cells: int = 6
    colour: bool = False
    colour_mode: str = "mono"  # mono | chroma
    dark_colour: str = "#000000"
    light_colour: str = "#ffffff"
    light_tint: float = 0.5
    saturation: float = 1.3
    dark_luma: float = 60.0
    light_luma: float = 205.0
    dot_scale: float = 0.35
    dot_light_alpha: float = 0.85
    dot_dark_alpha: float = 1.0
    dot_grayscale: bool = False
    dot_dimming: float = 0.0
    module_px: int = 9
    quiet: int = 4
    seed: int = 0

    def solve_key(self) -> tuple:
        return tuple((f, getattr(self, f)) for f in SOLVE_FIELDS)


@dataclass
class SolveStage:
    options: Options
    plan: Plan
    target: Target
    message: Message
    solve: SolveResult
    matrix: np.ndarray  # before budget
    match_pre: float
    mask_scores: dict = field(default_factory=dict)
    t_solve: float = 0.0


@dataclass
class Result:
    options: Options
    plan: Plan
    target: Target
    solve: SolveResult
    matrix: np.ndarray
    ledger: Ledger | None
    image: Image.Image
    expected_text: str
    stats: dict = field(default_factory=dict)

    def control_image(self, module_px: int | None = None) -> Image.Image:
        return render_control(self.matrix, self.plan, self.solve.control, module_px or self.options.module_px, self.options.quiet)

    def residual_image(self, module_px: int | None = None) -> Image.Image:
        return render_residual(self.matrix, self.plan, self.target, self.ledger.damaged if self.ledger else None,
                               module_px or self.options.module_px, self.options.quiet)


@lru_cache(maxsize=256)
def _plan(version: int, ecl: int, mask: int, rotation: int) -> Plan:
    return build_plan(version, ecl, mask, rotation)


def _match(plan: Plan, matrix: np.ndarray, target: Target) -> float:
    sel = (plan.role == DATA) & (target.weight > 0.5)
    if not sel.any():
        return 1.0
    w = target.weight[sel]
    ok = (matrix[sel] == target.want_dark[sel]).astype(np.float32)
    return float((ok * w).sum() / w.sum())


def _fidelity(img: Image.Image, plan: Plan, target: Target, quiet_px: int, sub: int) -> float:
    """Pearson correlation between rendered luminance (box-downsampled to sub×N) and the target."""
    N = plan.N
    g = img.convert("L")
    w, h = g.size
    core = g.crop((quiet_px, quiet_px, w - quiet_px, h - quiet_px)).resize((N * sub, N * sub), Image.BOX)
    r = np.asarray(core, np.float32)
    if sub == 1:
        t, sel = target.luma, target.weight > 0.5
    else:
        from .render import _resample_sub

        t = _resample_sub(target.sub_luma, N, sub)
        sel = _resample_sub(target.sub_alpha, N, sub) > 0.5
    if sel.sum() < 10:
        return float("nan")
    a, b = r[sel], t[sel]
    if a.std() < 1e-6 or b.std() < 1e-6:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def solve_stage(image: Image.Image, opts: Options) -> SolveStage:
    t0 = time.perf_counter()
    ecl = ecl_index(opts.ecl)
    version = min_version(opts.payload, ecl, opts.free) if opts.version == "min" else int(opts.version)
    masks = range(8) if opts.mask == "auto" else [int(opts.mask)]
    rots = range(4) if opts.rotation == "auto" else [int(opts.rotation)]
    message = layout_message(opts.payload, version, ecl, "none" if not opts.steer else opts.free)
    N = 4 * version + 17
    targets: dict[int, Target] = {}

    def target_for(rot: int) -> Target:
        if rot not in targets:
            targets[rot] = build_target(image, N, scale=opts.scale, dx=opts.dx, dy=opts.dy, threshold=opts.threshold,
                                        contrast=opts.contrast, brightness=opts.brightness,
                                        autocontrast=opts.autocontrast, rotation=rot)
        return targets[rot]

    best = None
    scores = {}
    for rot in rots:
        tgt = target_for(rot)
        for mask in masks:
            plan = _plan(version, ecl, mask, rot)
            if len(masks) == 1 and len(rots) == 1:
                sr = solve(plan, message, tgt if opts.steer else None, policy=opts.policy, data_only=opts.data_only,
                           dither=opts.dither, outside=opts.outside, seed=opts.seed)
            else:
                sr = solve(plan, message, tgt if opts.steer else None, policy=opts.policy, data_only=opts.data_only,
                           dither=False, outside=opts.outside, seed=opts.seed)
            m = matrix_from_codewords(plan, sr.codewords)
            score = _match(plan, m, tgt)
            scores[(mask, rot)] = score
            if best is None or score > best[0]:
                best = (score, mask, rot, sr, m)
    score, mask, rot, sr, matrix = best
    plan = _plan(version, ecl, mask, rot)
    tgt = target_for(rot)
    if opts.dither and (len(masks) > 1 or len(rots) > 1):
        sr = solve(plan, message, tgt if opts.steer else None, policy=opts.policy, data_only=opts.data_only,
                   dither=True, outside=opts.outside, seed=opts.seed)
        matrix = matrix_from_codewords(plan, sr.codewords)
    return SolveStage(opts, plan, tgt, message, sr, matrix, _match(plan, matrix, tgt),
                      {f"{k[0]}/{k[1]}": v for k, v in scores.items()}, time.perf_counter() - t0)


def finish_stage(stage: SolveStage, opts: Options) -> Result:
    t0 = time.perf_counter()
    plan, tgt, sr = stage.plan, stage.target, stage.solve
    matrix = stage.matrix
    ledger = None
    if opts.spend != 0 and opts.steer:
        matrix, ledger = spend_budget(plan, matrix, tgt, opts.spend, opts.reserve)
    colour_kw = dict(mode=opts.colour_mode, dark_colour=opts.dark_colour, light_colour=opts.light_colour,
                     light_tint=opts.light_tint, saturation=opts.saturation, dark_luma=opts.dark_luma, light_luma=opts.light_luma)
    coloured = opts.colour_mode != "mono" or opts.dark_colour.lower() != "#000000" or opts.light_colour.lower() != "#ffffff"
    if opts.style == "plain":
        img = render_plain(matrix, opts.module_px, opts.quiet, module_colours(matrix, plan, tgt, **colour_kw) if coloured else None)
    elif opts.style == "halftone":
        if opts.halftone_centre_frac is not None:
            img = render_halftone_px(matrix, plan, tgt, opts.module_px, opts.quiet, centre_frac=opts.halftone_centre_frac,
                                     centre_alpha=opts.halftone_centre_alpha, surround_contrast=opts.halftone_contrast,
                                     cells=opts.halftone_cells, dither=opts.halftone_dither, colour=opts.colour,
                                     colour_kw=colour_kw if coloured else None)
        else:
            mpx = -(-opts.module_px // opts.halftone_sub) * opts.halftone_sub
            img = render_halftone(matrix, plan, tgt, mpx, opts.quiet, dither=opts.halftone_dither, colour=opts.colour,
                                  sub=opts.halftone_sub, centre=opts.halftone_centre, surround_contrast=opts.halftone_contrast)
    elif opts.style == "dots":
        from .render import hex_rgb

        img = render_dots(matrix, plan, tgt, opts.module_px, opts.quiet, dot_scale=opts.dot_scale,
                          light_alpha=opts.dot_light_alpha, dark_alpha=opts.dot_dark_alpha,
                          grayscale=opts.dot_grayscale, dimming=opts.dot_dimming, dark_rgb=tuple(hex_rgb(opts.dark_colour)))
    else:
        raise ValueError(opts.style)
    expected = stage.message.expected_text(sr.data)
    qpx = opts.quiet * (img.size[0] // (plan.N + 2 * opts.quiet))
    stats = {
        "version": plan.version, "N": plan.N, "ecl": opts.ecl, "mask": plan.mask, "rotation": plan.rotation,
        "threshold": int(tgt.threshold), "match_pre_budget": stage.match_pre, "match": _match(plan, matrix, tgt),
        "controlled_frac": sr.stats["controlled"] / max(1, sr.stats["data_modules"]),
        "free_bits": stage.message.free_bits, "restarts": sr.restarts,
        "budget_used": ledger.used if ledger else [0] * plan.blocks,
        "budget_limit": ledger.limit if ledger else 0, "capacity": plan.capacity, "blocks": plan.blocks,
        "flipped_modules": ledger.flipped_modules if ledger else 0,
        "fidelity_module": _fidelity(img, plan, tgt, qpx, 1),
        "fidelity_sub": _fidelity(img, plan, tgt, qpx, 3),
        "expected_len": len(expected), "mask_scores": stage.mask_scores,
        "t_solve": stage.t_solve, "t_finish": time.perf_counter() - t0,
    }
    stats["t_total"] = stats["t_solve"] + stats["t_finish"]
    return Result(opts, plan, tgt, sr, matrix, ledger, img, expected, stats)


def run(image: Image.Image, opts: Options) -> Result:
    return finish_stage(solve_stage(image, opts), opts)


def options_from_dict(d: dict) -> Options:
    names = {f.name for f in fields(Options)}
    return Options(**{k: v for k, v in d.items() if k in names})
