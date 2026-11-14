"""Run every image through every technique stack, verify with real decoders, write a report.

Usage (from the project root):
  .venv/bin/python experiments/run_matrix.py [--quick] [--images pjw,mona] [--stacks qart-L,...]
                                             [--version 8] [--payload URL] [--workers N] [--out out/matrix]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.stacks import QUICK, STACKS  # noqa: E402


def one(job: dict) -> dict:
    """Worker: run a single (image, stack) cell."""
    from PIL import Image

    from qart.pipeline import Options, run
    from qart.verify import verify

    out = Path(job["out"])
    cell_dir = out / job["image_id"]
    cell_dir.mkdir(parents=True, exist_ok=True)
    rec = {"image": job["image_id"], "stack": job["stack"], "category": job["category"]}
    try:
        img = Image.open(job["image_path"])
        img.load()
        opts = Options(payload=job["payload"], version=job["version"], **STACKS[job["stack"]])
        t0 = time.perf_counter()
        r = run(img, opts)
        r.image.save(cell_dir / f"{job['stack']}.png")
        r.control_image().save(cell_dir / f"{job['stack']}.control.png")
        v = verify(r.image, r.expected_text)
        rec.update(r.stats)
        rec["verify"] = {"table": v["table"], "pass_rate": v["pass_rate"], "clean_pass": v["clean_pass"],
                         "decoders_all_pass": v["decoders_all_pass"], "wechat_mode": v["wechat_mode"]}
        rec["expected"] = r.expected_text if len(r.expected_text) < 80 else r.expected_text[:60] + "…"
        rec["options"] = asdict(opts)
        rec["seconds"] = time.perf_counter() - t0
        rec["ok"] = True
    except Exception as e:  # keep the matrix running
        rec["ok"] = False
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["trace"] = traceback.format_exc()
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--images", default="")
    ap.add_argument("--stacks", default="")
    ap.add_argument("--version", type=int, default=8)
    ap.add_argument("--payload", default="https://example.com/#")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", default="out/matrix")
    args = ap.parse_args()

    manifest = json.loads((ROOT / "images" / "set" / "manifest.json").read_text())
    if args.images:
        want = set(args.images.split(","))
        manifest = [m for m in manifest if m["id"] in want]
    stacks = args.stacks.split(",") if args.stacks else (QUICK if args.quick else [s for s in STACKS if not s.startswith("sweep-")])
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    jobs = [{"image_id": m["id"], "image_path": str(ROOT / "images" / "set" / m["file"]), "category": m["category"],
             "stack": s, "payload": args.payload, "version": args.version, "out": str(out)}
            for m in manifest for s in stacks]
    print(f"{len(manifest)} images × {len(stacks)} stacks = {len(jobs)} runs, {args.workers} workers")
    t0 = time.perf_counter()
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, j): j for j in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            rec = f.result()
            results.append(rec)
            if rec["ok"]:
                v = rec["verify"]
                print(f"[{i:>3}/{len(jobs)}] {rec['image']:<12} {rec['stack']:<20} match {rec['match']:.3f} "
                      f"fid {rec['fidelity_sub']:.2f} pass {v['pass_rate']:.2f} clean {v['clean_pass']}/4 {rec['seconds']:.1f}s")
            else:
                print(f"[{i:>3}/{len(jobs)}] {rec['image']:<12} {rec['stack']:<20} ERROR {rec['error']}")
    results.sort(key=lambda r: (r["image"], r["stack"]))
    meta = {"payload": args.payload, "version": args.version, "stacks": stacks, "images": [m["id"] for m in manifest],
            "seconds": time.perf_counter() - t0, "stack_options": {s: STACKS[s] for s in stacks}}
    (out / "results.json").write_text(json.dumps({"meta": meta, "results": results}, indent=1, default=str))
    print(f"done in {meta['seconds']:.0f}s → {out / 'results.json'}")
    from experiments.report import write_report

    write_report(out)
    print(f"report → {out / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
