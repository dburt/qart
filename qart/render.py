"""Renderers: turn a module matrix (plus optional target image) into a PIL image."""
from __future__ import annotations

import numpy as np
from PIL import Image

from .plan import DATA, EXTRA, FINDER, ALIGN, Plan
from .target import Target

BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32)


def _with_quiet(arr: np.ndarray, quiet_px: int, fill) -> np.ndarray:
    if arr.ndim == 2:
        return np.pad(arr, quiet_px, constant_values=fill)
    return np.pad(arr, ((quiet_px, quiet_px), (quiet_px, quiet_px), (0, 0)), constant_values=fill)


def hex_rgb(s: str) -> np.ndarray:
    s = s.lstrip("#")
    return np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def luma_of(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def colourise(rgb: np.ndarray, is_dark: np.ndarray, inside: np.ndarray, *, mode: str = "mono",
              dark_colour: str = "#000000", light_colour: str = "#ffffff", light_tint: float = 0.5,
              saturation: float = 1.3, dark_luma: float = 60.0, light_luma: float = 205.0) -> np.ndarray:
    """Per-cell colours that keep luminance decodable.

    mode "mono": dark/light colours everywhere. "chroma": inside the image, dark cells take the
    image hue scaled to `dark_luma`, light cells a tint of the image lightened to `light_luma`
    (strength `light_tint`); outside the image the dark/light colours. Function patterns are
    handled by the caller (they pass inside=False).
    """
    dk = hex_rgb(dark_colour)
    lt = hex_rgb(light_colour)
    out = np.where(is_dark[..., None], dk, lt).astype(np.float32)
    if mode != "chroma":
        return np.clip(out, 0, 255).astype(np.uint8)
    rgb = rgb.astype(np.float32)
    lum = np.maximum(luma_of(rgb), 1.0)[..., None]
    # boost saturation around the grey axis
    rgb = np.clip(lum + (rgb - lum) * saturation, 0, 255)
    lum = np.maximum(luma_of(rgb), 1.0)[..., None]
    # dark cells: scale the colour so its luminance is dark_luma; grey image colours fall back to dark_colour
    sat = (rgb.max(axis=-1) - rgb.min(axis=-1)) / np.maximum(rgb.max(axis=-1), 1.0)
    dark_c = np.clip(rgb * (dark_luma / lum), 0, 255)
    # if scaling up would clip (very dark saturated colours), the luminance overshoots little; clip is fine
    w = np.clip(sat * 2.0, 0, 1)[..., None]
    dark_c = dark_c * w + dk * (1 - w)
    # light cells: reduce the "ink" until luminance is at least light_luma, then weaken by light_tint
    ink = 255.0 - rgb
    k = np.minimum(1.0, (255.0 - light_luma) / np.maximum(255.0 - lum[..., 0], 1.0))[..., None]
    light_c = 255.0 - ink * k * light_tint
    light_c = light_c * w + lt * (1 - w)
    chosen = np.where(is_dark[..., None], dark_c, light_c)
    out = np.where(inside[..., None], chosen, out)
    return np.clip(out, 0, 255).astype(np.uint8)


def module_colours(matrix: np.ndarray, plan: Plan, target: Target, **kw) -> np.ndarray:
    """N×N×3 colours for every module (function patterns use the dark/light colours)."""
    N = plan.N
    rgb = np.asarray(Image.fromarray(target.rgb, "RGB").resize((N, N), Image.BOX), np.float32)
    inside = (target.weight > 0.5) & ~_function_mask(plan)
    return colourise(rgb, matrix, inside, **kw)


def render_plain(matrix: np.ndarray, module_px: int = 9, quiet: int = 4, colours: np.ndarray | None = None) -> Image.Image:
    if colours is None:
        px = np.where(matrix, 0, 255).astype(np.uint8)
        px = np.kron(px, np.ones((module_px, module_px), np.uint8))
        px = _with_quiet(px, quiet * module_px, 255)
        return Image.fromarray(px, "L")
    px = np.kron(colours, np.ones((module_px, module_px, 1), np.uint8))
    px = _with_quiet(px, quiet * module_px, 255)
    return Image.fromarray(px, "RGB")


def _function_mask(plan: Plan, keep_timing: bool = True, keep_align: bool = True) -> np.ndarray:
    """Modules that are drawn at full size (not halftoned)."""
    role = plan.role
    m = (role != DATA) & (role != EXTRA)
    return m


def _resample_sub(arr: np.ndarray, N: int, sub: int) -> np.ndarray:
    """Resample a target plane to (sub*N × sub*N) by nearest neighbour."""
    S = N * sub
    if arr.shape[0] == S:
        return arr
    idx = (np.arange(S) * arr.shape[0] / S).astype(int)
    return arr[idx][:, idx]


def _dither_plane(lum: np.ndarray, alpha: np.ndarray, thr: float, mode: str) -> np.ndarray:
    """Binary dark mask for an image plane: ordered (bayer), error diffusion (fs) or threshold."""
    S0, S1 = lum.shape
    if mode == "bayer":
        t = (np.tile(BAYER4, (S0 // 4 + 1, S1 // 4 + 1))[:S0, :S1] + 0.5) / 16.0 * 255.0
        dark = lum < thr + (t - 127.5)
    elif mode == "fs":
        dark = _floyd_steinberg(lum, thr)
    else:
        dark = lum < thr
    return dark & (alpha > 0.5)


def render_halftone_px(matrix: np.ndarray, plan: Plan, target: Target, module_px: int = 12, quiet: int = 4,
                       centre_frac: float = 0.67, centre_alpha: float = 1.0, surround_contrast: float = 1.0,
                       cells: int = 6, dither: str = "fs", colour: bool = False,
                       function_solid: bool = True, colour_kw: dict | None = None) -> Image.Image:
    """Pixel-level halftone with continuous dials.

    The image is dithered on a grid of `cells` per module and drawn at `surround_contrast`;
    a centre square of side `centre_frac` × module carries the module value, blended over the
    continuous image with opacity `centre_alpha`. Function patterns are drawn solid.
    """
    N = plan.N
    cell_px = max(1, module_px // cells)
    module_px = cell_px * cells
    S = N * cells
    lum = _resample_sub(target.sub_luma, N, cells)
    alpha = _resample_sub(target.sub_alpha, N, cells)
    dark = _dither_plane(lum, alpha, float(target.threshold), dither)
    lo = 127.5 - 127.5 * surround_contrast
    hi = 127.5 + 127.5 * surround_contrast
    cells_v = np.where(dark, lo, hi).astype(np.float32)
    cells_v[alpha <= 0.5] = 255.0
    px = np.kron(cells_v, np.ones((cell_px, cell_px), np.float32))  # (N*module_px)²
    # continuous image at pixel resolution, for blending translucent centres
    cont = np.kron(lum, np.ones((cell_px, cell_px), np.float32))
    cont[np.kron(alpha, np.ones((cell_px, cell_px), np.float32)) <= 0.5] = 255.0
    side = int(round(centre_frac * module_px))
    side = max(1, min(module_px, side))
    o = (module_px - side) // 2
    mod_val = np.where(matrix, 0.0, 255.0).astype(np.float32)
    # centre mask at pixel resolution
    cm = np.zeros((module_px, module_px), bool)
    cm[o:o + side, o:o + side] = True
    cmask = np.tile(cm, (N, N))
    val_full = np.kron(mod_val, np.ones((module_px, module_px), np.float32))
    blended = centre_alpha * val_full + (1.0 - centre_alpha) * cont
    px = np.where(cmask, blended, px)
    func = _function_mask(plan)
    fmask = np.kron(func, np.ones((module_px, module_px), bool))
    if function_solid:
        px = np.where(fmask, val_full, px)
    if colour_kw and colour_kw.get("mode", "mono") != "mono":
        # colour per pixel: luminance from px, hue from the image (dark/light decided by luminance)
        rgb_cells = _resample_sub(target.rgb, N, cells)
        rgb_px = np.kron(rgb_cells, np.ones((cell_px, cell_px, 1), np.uint8))
        inside_px = np.kron(alpha, np.ones((cell_px, cell_px), np.float32)) > 0.5
        inside_px &= ~fmask
        is_dark = px < 127.5
        base = colourise(rgb_px, is_dark, inside_px, **colour_kw).astype(np.float32)
        # follow the mono pixel value, but never darker than the colour's own luminance for dark cells
        # (so hue survives) nor lighter than it for light cells (so the tint survives)
        bl = np.maximum(luma_of(base), 1.0)
        L = np.where(is_dark, np.maximum(px, bl), np.minimum(px, bl))
        L = np.where(inside_px, L, px)
        out = np.clip(base * (L / bl)[..., None], 0, 255)
        out = _with_quiet(out.astype(np.uint8), quiet * module_px, 255)
        return Image.fromarray(out, "RGB")
    if colour:
        rgb = _resample_sub(target.rgb, N, cells).astype(np.float32)
        rgb = np.kron(rgb, np.ones((cell_px, cell_px, 1), np.float32))
        l = np.maximum(np.kron(lum, np.ones((cell_px, cell_px), np.float32)), 1.0)[..., None]
        out = np.clip(rgb * (px[..., None] / l), 0, 255)
        grey_mask = cmask | fmask if function_solid else cmask
        out = np.where(grey_mask[..., None], np.repeat(px[..., None], 3, axis=2), out)
        out = _with_quiet(np.clip(out, 0, 255).astype(np.uint8), quiet * module_px, 255)
        return Image.fromarray(out, "RGB")
    out = _with_quiet(np.clip(px, 0, 255).astype(np.uint8), quiet * module_px, 255)
    return Image.fromarray(out, "L")


def render_residual(matrix: np.ndarray, plan: Plan, target: Target, damaged: np.ndarray | None = None,
                    module_px: int = 9, quiet: int = 4) -> Image.Image:
    """Debug view: modules in black/white; in-image mismatches red (should be dark) or amber (should be light);
    budget-flipped modules outlined in blue."""
    N = plan.N
    px = np.zeros((N, N, 3), np.uint8)
    px[:] = np.where(matrix[..., None], 0, 255)
    inimg = (plan.role == DATA) & (target.weight > 0.5)
    want = target.want_dark
    px[inimg & want & ~matrix] = (217, 71, 43)
    px[inimg & ~want & matrix] = (230, 176, 74)
    px = np.kron(px, np.ones((module_px, module_px, 1), np.uint8))
    if damaged is not None and damaged.any():
        ys, xs = np.nonzero(damaged)
        for y, x in zip(ys.tolist(), xs.tolist()):
            y0, x0 = y * module_px, x * module_px
            px[y0, x0:x0 + module_px] = (31, 78, 156)
            px[y0 + module_px - 1, x0:x0 + module_px] = (31, 78, 156)
            px[y0:y0 + module_px, x0] = (31, 78, 156)
            px[y0:y0 + module_px, x0 + module_px - 1] = (31, 78, 156)
    px = _with_quiet(px, quiet * module_px, 255)
    return Image.fromarray(px, "RGB")


def render_halftone(matrix: np.ndarray, plan: Plan, target: Target, module_px: int = 9, quiet: int = 4,
                    dither: str = "bayer", colour: bool = False, sub_threshold: int | None = None,
                    sub: int = 3, centre: int = 1, surround_contrast: float = 1.0) -> Image.Image:
    """Halftone QR: the centre `centre`×`centre` cells of each `sub`×`sub` data module carry the
    module value; the surrounding cells carry the dithered image at `surround_contrast`
    (1.0 = full black/white, 0.5 = 64..192 grey). Function patterns are drawn solid."""
    N = plan.N
    S = N * sub
    lum = _resample_sub(target.sub_luma, N, sub)
    alpha = _resample_sub(target.sub_alpha, N, sub)
    thr = target.threshold if sub_threshold is None else sub_threshold
    if dither == "bayer":
        t = (np.tile(BAYER4, (S // 4 + 1, S // 4 + 1))[:S, :S] + 0.5) / 16.0 * 255.0
        t = thr + (t - 127.5)
        dark = lum < t
    elif dither == "fs":
        dark = _floyd_steinberg(lum, thr)
    else:
        dark = lum < thr
    dark = dark & (alpha > 0.5)
    lo = int(round(127.5 - 127.5 * surround_contrast))
    hi = int(round(127.5 + 127.5 * surround_contrast))
    cells = np.where(dark, lo, hi).astype(np.uint8)
    cells[alpha <= 0.5] = 255
    # centre cells carry the module value at full contrast
    centre_val = np.where(matrix, 0, 255).astype(np.uint8)
    o = (sub - centre) // 2
    cmask = np.zeros((sub, sub), bool)
    cmask[o:o + centre, o:o + centre] = True
    cmask_full = np.tile(cmask, (N, N))
    solid = np.kron(centre_val, np.ones((sub, sub), np.uint8))
    cells = np.where(cmask_full, solid, cells)
    # function patterns solid
    func = _function_mask(plan)
    func_sub = np.kron(func, np.ones((sub, sub), bool))
    cells = np.where(func_sub, solid, cells)
    cell_px = max(1, module_px // sub)
    if colour:
        rgb = _resample_sub(target.rgb, N, sub).astype(np.float32)
        l = np.maximum(lum, 1.0)[..., None]
        out = np.clip(rgb * (cells[..., None] / l), 0, 255)
        out = np.where(cells[..., None] == 0, rgb * 0.15, out)
        out = np.where((func_sub | cmask_full)[..., None], np.repeat(cells[..., None], 3, axis=2), out).astype(np.uint8)
        px = np.kron(out, np.ones((cell_px, cell_px, 1), np.uint8))
        px = _with_quiet(px, quiet * cell_px * sub, 255)
        return Image.fromarray(px, "RGB")
    px = np.kron(cells, np.ones((cell_px, cell_px), np.uint8))
    px = _with_quiet(px, quiet * cell_px * sub, 255)
    return Image.fromarray(px, "L")


def _floyd_steinberg(lum: np.ndarray, thr: float) -> np.ndarray:
    h, w = lum.shape
    v = lum.astype(np.float32).copy()
    out = np.zeros((h, w), bool)
    for y in range(h):
        for x in range(w):
            old = v[y, x]
            dark = old < thr
            out[y, x] = dark
            new = 0.0 if dark else 255.0
            err = old - new
            if x + 1 < w:
                v[y, x + 1] += err * 7 / 16
            if y + 1 < h:
                if x > 0:
                    v[y + 1, x - 1] += err * 3 / 16
                v[y + 1, x] += err * 5 / 16
                if x + 1 < w:
                    v[y + 1, x + 1] += err * 1 / 16
    return out


def render_dots(matrix: np.ndarray, plan: Plan, target: Target, module_px: int = 9, quiet: int = 4,
                dot_scale: float = 0.35, dark_rgb=(0, 0, 0), light_alpha: float = 0.85,
                dark_alpha: float = 1.0, grayscale: bool = False, dimming: float = 0.0) -> Image.Image:
    """Image as background, each data module a small centred square; function patterns solid on white."""
    N = plan.N
    S = N * module_px
    src = target.rgb
    if grayscale:
        src = np.repeat(target.sub_luma.astype(np.uint8)[..., None], 3, axis=2)
    bg = Image.fromarray(src, "RGB").resize((S, S), Image.LANCZOS)
    px = np.asarray(bg).astype(np.float32)
    if dimming:
        px = px * (1 - abs(dimming)) + (0 if dimming > 0 else 255) * abs(dimming)
    d = max(1, int(round(module_px * dot_scale)))
    o = (module_px - d) // 2
    func = _function_mask(plan)
    dark = np.array(dark_rgb, np.float32)
    for y in range(N):
        for x in range(N):
            y0, x0 = y * module_px, x * module_px
            if func[y, x]:
                px[y0:y0 + module_px, x0:x0 + module_px] = 0 if matrix[y, x] else 255
            else:
                cell = px[y0 + o:y0 + o + d, x0 + o:x0 + o + d]
                if matrix[y, x]:
                    cell[:] = cell * (1 - dark_alpha) + dark * dark_alpha
                else:
                    cell[:] = cell * (1 - light_alpha) + 255 * light_alpha
    px = np.clip(px, 0, 255).astype(np.uint8)
    px = _with_quiet(px, quiet * module_px, 255)
    return Image.fromarray(px, "RGB")


def render_control(matrix: np.ndarray, plan: Plan, control: np.ndarray, module_px: int = 9, quiet: int = 4) -> Image.Image:
    """Debug view: controlled modules in black/white, uncontrolled greyed, message-fixed in blue."""
    N = plan.N
    px = np.zeros((N, N, 3), np.uint8)
    dark = matrix
    px[:] = np.where(dark[..., None], 0, 255)
    unc = (control == 0) & (plan.role == DATA)
    px[unc & dark] = (96, 96, 96)
    px[unc & ~dark] = (212, 212, 212)
    fx = (control == 2)
    px[fx & dark] = (59, 78, 120)
    px[fx & ~dark] = (201, 211, 234)
    px = np.kron(px, np.ones((module_px, module_px, 1), np.uint8))
    px = _with_quiet(px, quiet * module_px, 255)
    return Image.fromarray(px, "RGB")
