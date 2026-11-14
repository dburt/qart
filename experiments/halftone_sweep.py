"""Sweep halftone geometry (sub-grid, centre size, surround contrast, dither) for robustness and looks.

Writes out/halftone/<image>-<variant>.png, a contact sheet, and prints pass rates (zxing+wechat+zbar).
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IMAGES = ["pjw", "mona", "lake", "chess", "boyzone", "bear"]
# (name, sub, centre, contrast, dither, module_px)
VARIANTS = [
    ("3-1 c1.0 bayer", 3, 1, 1.0, "bayer", 9),
    ("4-2 c1.0 bayer", 4, 2, 1.0, "bayer", 8),
    ("4-2 c0.75 bayer", 4, 2, 0.75, "bayer", 8),
    ("5-3 c1.0 bayer", 5, 3, 1.0, "bayer", 10),
    ("5-3 c0.6 bayer", 5, 3, 0.6, "bayer", 10),
    ("6-3 c1.0 bayer", 6, 3, 1.0, "bayer", 12),
    ("6-3 c0.75 bayer", 6, 3, 0.75, "bayer", 12),
    ("6-4 c1.0 bayer", 6, 4, 1.0, "bayer", 12),
    ("6-4 c0.75 bayer", 6, 4, 0.75, "bayer", 12),
    ("6-3 c1.0 fs", 6, 3, 1.0, "fs", 12),
    ("6-4 c1.0 fs", 6, 4, 1.0, "fs", 12),
    ("8-4 c1.0 bayer", 8, 4, 1.0, "bayer", 16),
    ("8-5 c1.0 bayer", 8, 5, 1.0, "bayer", 16),
]
DEGS = ["clean", "scale50", "scale30", "blur", "jpeg40", "skew"]
DECS = ["zxing", "wechat", "zbar"]


def one(args):
    image_id, (name, sub, centre, contrast, dither, mpx) = args
    from PIL import Image

    from qart.pipeline import Options, run
    from qart.verify import verify

    img = Image.open(ROOT / "images" / "set" / f"{image_id}.png")
    img.load()
    opts = Options(version=8, ecl="L", free="pad", mask="auto", style="halftone", halftone_sub=sub, halftone_centre=centre,
                   halftone_contrast=contrast, halftone_dither=dither, module_px=mpx)
    r = run(img, opts)
    out = ROOT / "out" / "halftone"
    out.mkdir(parents=True, exist_ok=True)
    fn = out / f"{image_id}-{name.replace(' ', '_')}.png"
    r.image.save(fn)
    v = verify(r.image, r.expected_text, decoders=DECS)
    per_deg = {g: sum(v["table"][g].values()) / len(DECS) for g in DEGS}
    return {"image": image_id, "variant": name, "pass": v["pass_rate"], "per_deg": per_deg,
            "fid_m": r.stats["fidelity_module"], "fid_s": r.stats["fidelity_sub"], "file": str(fn)}


def main() -> None:
    jobs = [(i, v) for v in VARIANTS for i in IMAGES]
    with ProcessPoolExecutor(max_workers=12) as ex:
        res = list(ex.map(one, jobs))
    (ROOT / "out" / "halftone" / "results.json").write_text(json.dumps(res, indent=1))
    print(f"{'variant':<18} {'pass':>5} {'fid_m':>6} {'fid_s':>6} | " + " ".join(f"{g:>7}" for g in DEGS))
    for name, *_ in VARIANTS:
        rs = [r for r in res if r["variant"] == name]
        p = sum(r["pass"] for r in rs) / len(rs)
        fm = sum(r["fid_m"] for r in rs if r["fid_m"] == r["fid_m"]) / len(rs)
        fs = sum(r["fid_s"] for r in rs if r["fid_s"] == r["fid_s"]) / len(rs)
        pd = {g: sum(r["per_deg"][g] for r in rs) / len(rs) for g in DEGS}
        print(f"{name:<18} {p:5.2f} {fm:6.2f} {fs:6.2f} | " + " ".join(f"{pd[g]:7.2f}" for g in DEGS))
    # contact sheet: rows = variants, cols = images
    from PIL import Image, ImageDraw

    cell = 230
    sheet = Image.new("RGB", (cell * len(IMAGES) + 150, (cell + 4) * len(VARIANTS)), "#777")
    d = ImageDraw.Draw(sheet)
    for vi, (name, *_) in enumerate(VARIANTS):
        d.text((4, vi * (cell + 4) + cell // 2), name, fill="white")
        for ii, image_id in enumerate(IMAGES):
            r = next(r for r in res if r["variant"] == name and r["image"] == image_id)
            im = Image.open(r["file"]).convert("RGB")
            im.thumbnail((cell - 4, cell - 4), Image.LANCZOS)
            sheet.paste(im, (150 + ii * cell, vi * (cell + 4)))
            d.text((150 + ii * cell + 4, vi * (cell + 4) + 2), f"{r['pass']:.2f}", fill="red")
    sheet.save(ROOT / "out" / "halftone" / "sheet.jpg", quality=80)


if __name__ == "__main__":
    main()
