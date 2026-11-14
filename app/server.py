"""Local web app: FastAPI backend over the qart engine.

Run from the project root:  .venv/bin/python -m app.server   (then open http://127.0.0.1:8765)
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from PIL import Image  # noqa: E402

from qart.pipeline import Options, finish_stage, options_from_dict, solve_stage  # noqa: E402
from qart.tables import VERSIONS, ecl_index  # noqa: E402
from qart.verify import DECODERS, DEGRADATIONS, verify  # noqa: E402

app = FastAPI(title="qart")
STATIC = ROOT / "app" / "static"
UPLOADS = ROOT / "out" / "app" / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
SET = ROOT / "images" / "set"

_images: dict[str, Image.Image] = {}
_solves: OrderedDict[tuple, object] = OrderedDict()
_lock = threading.Lock()
SOLVE_CACHE = 48


def load_image(image_id: str) -> Image.Image:
    if image_id in _images:
        return _images[image_id]
    if image_id.startswith("u:"):
        p = UPLOADS / f"{image_id[2:]}.png"
    else:
        p = SET / f"{image_id}.png"
    if not p.exists():
        raise HTTPException(404, f"unknown image {image_id}")
    im = Image.open(p)
    im.load()
    im = im.convert("RGBA")
    _images[image_id] = im
    return im


def get_stage(image_id: str, opts: Options):
    key = (image_id, opts.solve_key())
    with _lock:
        if key in _solves:
            _solves.move_to_end(key)
            return _solves[key], True
    stage = solve_stage(load_image(image_id), opts)
    with _lock:
        _solves[key] = stage
        while len(_solves) > SOLVE_CACHE:
            _solves.popitem(last=False)
    return stage, False


def png_b64(img: Image.Image) -> str:
    bio = io.BytesIO()
    img.save(bio, "PNG", optimize=False, compress_level=3)
    return "data:image/png;base64," + base64.b64encode(bio.getvalue()).decode()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import Response

    return Response(status_code=204)


@app.get("/api/images")
def list_images():
    manifest = json.loads((SET / "manifest.json").read_text())
    out = [{"id": m["id"], "category": m["category"], "size": m["size"], "has_alpha": m["has_alpha"]} for m in manifest]
    for p in sorted(UPLOADS.glob("*.png")):
        out.append({"id": f"u:{p.stem}", "category": "upload", "size": None, "has_alpha": None})
    return out


@app.get("/api/thumb/{image_id}")
def thumb(image_id: str):
    im = load_image(image_id).copy()
    im.thumbnail((160, 160))
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    bio = io.BytesIO()
    bg.convert("RGB").save(bio, "PNG")
    return JSONResponse({"png": "data:image/png;base64," + base64.b64encode(bio.getvalue()).decode()})


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"not an image: {e}")
    h = hashlib.sha1(data).hexdigest()[:12]
    im = im.convert("RGBA")
    if max(im.size) > 1600:
        im.thumbnail((1600, 1600))
    im.save(UPLOADS / f"{h}.png")
    _images.pop(f"u:{h}", None)
    return {"id": f"u:{h}", "size": im.size}


@app.get("/api/capacity")
def capacity(version: int, ecl: str = "L", payload: str = "", free: str = "pad"):
    """What a version offers: data bytes, free bits after the payload, blocks, per-block correction."""
    from qart.bits import layout_message

    e = ecl_index(ecl)
    total, levels = VERSIONS[version]
    blocks, check = levels[e]
    try:
        msg = layout_message(payload, version, e, free)
        free_bits = msg.free_bits
    except ValueError:
        free_bits = -1
    return {"version": version, "N": 4 * version + 17, "total": total, "data_bytes": total - blocks * check,
            "blocks": blocks, "check": check, "capacity": check // 2, "free_bits": free_bits}


@app.post("/api/render")
def render(body: dict):
    image_id = body.get("image_id")
    if not image_id:
        raise HTTPException(400, "image_id required")
    opts = options_from_dict(body.get("options", {}))
    views = body.get("views", ["final"])
    t0 = time.perf_counter()
    try:
        stage, cached = get_stage(image_id, opts)
        res = finish_stage(stage, opts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out = {"stats": res.stats, "expected": res.expected_text, "cached_solve": cached,
           "size": res.image.size, "seconds": time.perf_counter() - t0}
    if "final" in views:
        out["final"] = png_b64(res.image)
    if "control" in views:
        out["control"] = png_b64(res.control_image())
    if "residual" in views:
        out["residual"] = png_b64(res.residual_image())
    out["stats"]["solve_cached"] = cached
    return out


@app.post("/api/verify")
def verify_api(body: dict):
    image_id = body.get("image_id")
    opts = options_from_dict(body.get("options", {}))
    decoders = body.get("decoders") or ["zxing", "wechat", "zbar", "opencv"]
    degradations = body.get("degradations") or DEGRADATIONS
    stage, _ = get_stage(image_id, opts)
    res = finish_stage(stage, opts)
    v = verify(res.image, res.expected_text, degradations=degradations, decoders=decoders)
    v.pop("texts", None)
    v["expected"] = res.expected_text
    return v


@app.get("/api/meta")
def meta():
    return {"decoders": DECODERS.names, "degradations": DEGRADATIONS}


if __name__ == "__main__":
    import uvicorn

    import os

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("QART_PORT", "8765")), log_level="warning")
