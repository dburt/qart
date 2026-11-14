"""Curate the experiment image set.

Copies the selected source images into images/set/ under stable ids, generates the
synthetic test images, and writes images/set/manifest.json describing each one.
Run from the project root:  .venv/bin/python experiments/prepare_images.py
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "images"
OUT = ROOT / "images" / "set"

# id -> (source file, category, why it is in the set)
SELECTED = {
    "pjw": ("_downloads/pjw.png", "portrait-bw", "Cox's canonical QArt test image (48px, high contrast)"),
    "pink": ("220px-ArthurPink.jpg", "portrait-bw", "grayscale photo portrait, mid-tones"),
    "mona": ("_downloads/mona_lisa.jpg", "portrait-colour", "colour painting, low contrast face, famous"),
    "cd": ("mbid-edd7bdc5-1fd4-4aed-9202-6c2d646680a9-36448102644_thumb250.jpg", "busy-photo-text", "busy cover with face and small text"),
    "lake": ("scenic-mountain-lake-surrounded-by-forest-and-dramatic-cloudy-sky-landscape-photo.jpg", "photo-landscape", "continuous tone natural photo, wide"),
    "veggie": ("videoframe_167126.png", "busy-scene", "3D rendered frame, many small elements, wide"),
    "painting": ("me.png", "low-contrast-texture", "textured painting, little luminance structure"),
    "bear": ("round1-grizzy-3d.png", "flat-illustration", "flat shapes, square, mid contrast"),
    "rockets": ("round5-star-realms.jpeg", "dark-background", "light subject on dark field, wide"),
    "chess": ("Gemini_Generated_Image_nn4pgdnn4pgdnn4p.png", "cartoon-white-bg", "cartoon on white, outlines plus colour fills"),
    "boyzone": ("boyzone_logo.png", "logo-text", "bold two-colour logo with large text"),
    "hut": ("favicon.ico", "icon-alpha", "simple icon with real transparency"),
    "grass": ("grass_block_side.png", "pixel-art", "16x16 pixel art, exact module mapping"),
    "triangle": ("icon.png", "line-art", "thin black lines on white, hardest render case"),
}

# Reference outputs (not inputs): existing artistic QR codes to compare against
REFERENCES = {
    "ref-duck": "duck_qr.png",
    "ref-stalfreds": "stalfreds-qart.png",
}


def synth(name: str, size: int = 256) -> Image.Image:
    im = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    if name == "text":
        font = None
        for cand in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"]:
            if Path(cand).exists():
                font = ImageFont.truetype(cand, int(size * 0.42))
                break
        if font is None:
            font = ImageFont.load_default()
        d.text((size / 2, size / 2), "QART", fill=(0, 0, 0, 255), font=font, anchor="mm")
    elif name == "ramp":
        for x in range(size):
            v = int(255 * x / (size - 1))
            d.line([(x, 0), (x, size)], fill=(v, v, v, 255))
        d.rectangle([size * 0.3, size * 0.3, size * 0.7, size * 0.7], fill=(0, 0, 0, 255))
    elif name == "heart-alpha":
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        cx, cy, r = size / 2, size * 0.5, size * 0.2
        # two circles and a triangle, good enough for a heart silhouette
        d.ellipse([cx - 2 * r, cy - 1.6 * r, cx, cy + 0.4 * r], fill=(0, 0, 0, 255))
        d.ellipse([cx, cy - 1.6 * r, cx + 2 * r, cy + 0.4 * r], fill=(0, 0, 0, 255))
        d.polygon([(cx - 1.95 * r, cy), (cx + 1.95 * r, cy), (cx, cy + 2.1 * r)], fill=(0, 0, 0, 255))
    elif name == "stripes":
        for y in range(0, size, 16):
            d.rectangle([0, y, size, y + 7], fill=(0, 0, 0, 255))
    elif name == "grey":
        d.rectangle([0, 0, size, size], fill=(128, 128, 128, 255))
    return im


SYNTH = {
    "text": ("typography", "bold word, tests letterform legibility at module scale"),
    "ramp": ("gradient", "grey ramp plus solid square, tests dithering and threshold"),
    "heart-alpha": ("silhouette-alpha", "black silhouette on transparent, alpha = don't care"),
    "stripes": ("adversarial-pattern", "horizontal stripes, interacts with masks and timing"),
    "grey": ("degenerate", "flat mid-grey, nothing to match; solver should not misbehave"),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for iid, (src, cat, why) in SELECTED.items():
        s = SRC / src
        if not s.exists():
            print("missing", s)
            continue
        im = Image.open(s)
        im.load()
        if s.suffix.lower() == ".ico":
            # take the largest frame
            best = max(im.info.get("sizes", {im.size}))
            im.size = best
            im.load()
        im = im.convert("RGBA")
        dst = OUT / f"{iid}.png"
        im.save(dst)
        manifest.append({"id": iid, "file": dst.name, "category": cat, "why": why, "source": src, "size": im.size, "has_alpha": im.getchannel("A").getextrema()[0] < 255})
    for iid, (cat, why) in SYNTH.items():
        im = synth(iid)
        dst = OUT / f"{iid}.png"
        im.save(dst)
        manifest.append({"id": iid, "file": dst.name, "category": cat, "why": why, "source": "synthetic", "size": im.size, "has_alpha": im.getchannel("A").getextrema()[0] < 255})
    refdir = OUT / "references"
    refdir.mkdir(exist_ok=True)
    for iid, src in REFERENCES.items():
        shutil.copy(SRC / src, refdir / f"{iid}.png")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"{len(manifest)} images in {OUT}")
    for m in manifest:
        print(f"  {m['id']:<12} {m['category']:<22} {m['size'][0]}x{m['size'][1]}{'  alpha' if m['has_alpha'] else ''}")


if __name__ == "__main__":
    main()
