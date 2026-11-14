"""Image to per-module targets: luminance, alpha, weights, and the 3x sub-grid for halftone."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance, ImageOps


@dataclass
class Target:
    N: int
    sub: int  # sub-pixels per module side in sub_luma / sub_alpha / rgb
    luma: np.ndarray  # N×N float32 0..255, module mean luminance (255 outside the image)
    alpha: np.ndarray  # N×N float32 0..1 coverage of the image
    want_dark: np.ndarray  # N×N bool
    weight: np.ndarray  # N×N float32 priority weight (0 = don't care)
    variance: np.ndarray  # N×N float32 local luminance variance
    sub_luma: np.ndarray  # 3N×3N float32
    sub_alpha: np.ndarray  # 3N×3N float32
    rgb: np.ndarray  # 3N×3N×3 uint8 colour image on white
    threshold: int

    def in_image(self) -> np.ndarray:
        return self.alpha > 0.5


def otsu(values: np.ndarray) -> int:
    """Otsu's threshold on 0..255 values; falls back to 128 for degenerate input."""
    v = np.clip(values, 0, 255).astype(np.int32)
    hist = np.bincount(v.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0 or v.min() == v.max():
        return 128
    w0 = np.cumsum(hist)
    w1 = total - w0
    m = np.cumsum(hist * np.arange(256))
    mean0 = m / np.maximum(w0, 1)
    mean1 = (m[-1] - m) / np.maximum(w1, 1)
    between = w0 * w1 * (mean0 - mean1) ** 2
    between[(w0 == 0) | (w1 == 0)] = -1
    t = int(np.argmax(between))
    return max(16, min(240, t + 1))


def _fit_image(img: Image.Image, N: int, scale: float, dx: int, dy: int, sub: int) -> Image.Image:
    """Place the image on a (N*sub)×(N*sub) transparent canvas, longest side = scale*N modules."""
    S = N * sub
    img = img.convert("RGBA")
    w, h = img.size
    long_side = max(1, int(round(scale * S)))
    if w >= h:
        nw, nh = long_side, max(1, int(round(h * long_side / w)))
    else:
        nh, nw = long_side, max(1, int(round(w * long_side / h)))
    # box filter down, nearest up (keeps pixel art crisp)
    resample = Image.BOX if (nw < w or nh < h) else Image.NEAREST
    img = img.resize((nw, nh), resample)
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ox = (S - nw) // 2 + dx * sub
    oy = (S - nh) // 2 + dy * sub
    canvas.alpha_composite(img, (ox, oy))
    return canvas


def build_target(img: Image.Image, N: int, *, scale: float = 1.0, dx: int = 0, dy: int = 0,
                 threshold: int | str = "otsu", contrast: float = 1.0, brightness: float = 1.0,
                 autocontrast: bool = False, invert: bool = False, weight_mode: str = "alpha",
                 variance_window: int = 5, rotation: int = 0, sub: int = 6) -> Target:
    if rotation % 4:
        img = img.rotate(-90 * (rotation % 4), expand=True)
    canvas = _fit_image(img, N, scale, dx, dy, sub)
    alpha = np.asarray(canvas.getchannel("A"), np.float32) / 255.0
    rgb_img = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
    rgb_img.alpha_composite(canvas)
    rgb_img = rgb_img.convert("RGB")
    if autocontrast:
        rgb_img = ImageOps.autocontrast(rgb_img, cutoff=1)
    if contrast != 1.0:
        rgb_img = ImageEnhance.Contrast(rgb_img).enhance(contrast)
    if brightness != 1.0:
        rgb_img = ImageEnhance.Brightness(rgb_img).enhance(brightness)
    if invert:
        rgb_img = ImageOps.invert(rgb_img)
    rgb = np.asarray(rgb_img, np.uint8)
    sub_luma = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)
    # module-level means (only over covered sub-pixels; 255 where nothing is covered)
    a3 = alpha.reshape(N, sub, N, sub)
    l3 = sub_luma.reshape(N, sub, N, sub)
    cov = a3.sum(axis=(1, 3))
    luma = np.where(cov > 0, (l3 * a3).sum(axis=(1, 3)) / np.maximum(cov, 1e-6), 255.0).astype(np.float32)
    malpha = (cov / (sub * sub)).astype(np.float32)
    if threshold == "otsu":
        threshold = otsu(luma[malpha > 0.5]) if (malpha > 0.5).any() else 128
    want_dark = luma < threshold
    # local variance of module luminance (QArt's contrast measure)
    k = variance_window
    pad = k // 2
    padded = np.pad(luma, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(padded, (k, k))
    mean = win.mean(axis=(2, 3))
    var = np.maximum((win * win).mean(axis=(2, 3)) - mean * mean, 0.0)
    if weight_mode == "alpha":
        weight = malpha.copy()
    elif weight_mode == "flat":
        weight = (malpha > 0).astype(np.float32)
    else:
        raise ValueError(weight_mode)
    return Target(N, sub, luma, malpha, want_dark, weight, var.astype(np.float32), sub_luma, alpha, rgb, threshold)
