"""Build report.html from results.json: a contact sheet (images × stacks) plus summary tables."""
from __future__ import annotations

import html
import json
import sys
from collections import defaultdict
from pathlib import Path

DEGS = ["clean", "scale50", "scale30", "blur", "jpeg40", "skew"]
DECS = ["zxing", "wechat", "opencv", "zbar"]

CSS = """
:root{--bg:#f4f1ea;--panel:#fffdf8;--ink:#1c1b18;--ink2:#5b574e;--line:#dcd6c8;--ok:#2f8f5b;--warn:#c98a12;--hot:#d9472b;--accent:#1f4e9c}
@media(prefers-color-scheme:dark){:root{--bg:#15161a;--panel:#1d1f25;--ink:#ecebe6;--ink2:#b4b1a8;--line:#32363f;--ok:#5ccb8a;--warn:#e6b04a;--hot:#ff7a5c;--accent:#6ea0ff}}
body{margin:0;padding:20px;background:var(--bg);color:var(--ink);font:13px/1.45 system-ui,sans-serif}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}p{margin:0 0 8px;color:var(--ink2);max-width:900px}
table{border-collapse:collapse;background:var(--panel);border:1px solid var(--line)}
th,td{padding:4px 8px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}
th{color:var(--ink2);font-weight:500;text-align:right}th:first-child,td:first-child{text-align:left;font-family:ui-monospace,monospace;font-size:12px}
.sheet{overflow-x:auto}.sheet table td{padding:4px;vertical-align:top}
.cell{width:150px}.cell img{width:150px;height:150px;image-rendering:pixelated;display:block;background:#fff;border:1px solid var(--line)}
.cell .m{display:flex;justify-content:space-between;font-family:ui-monospace,monospace;font-size:11px;margin-top:2px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.hot{color:var(--hot)}
.rowhead{font-family:ui-monospace,monospace;font-size:12px;white-space:nowrap}.rowhead small{display:block;color:var(--ink2)}
.bar{display:inline-block;height:9px;background:var(--accent);vertical-align:middle;border-radius:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.card table{width:100%;border:0}.dot{display:inline-block;width:9px;height:9px;border-radius:50%}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--hot)}
.note{font-size:12px;color:var(--ink2)}
"""


def cls(v: float, good: float, mid: float) -> str:
    return "ok" if v >= good else "warn" if v >= mid else "hot"


def write_report(out: Path) -> None:
    data = json.loads((out / "results.json").read_text())
    meta, results = data["meta"], [r for r in data["results"] if r.get("ok")]
    errors = [r for r in data["results"] if not r.get("ok")]
    stacks, images = meta["stacks"], meta["images"]
    by = {(r["image"], r["stack"]): r for r in results}
    cats = {r["image"]: r["category"] for r in results}

    def mean(xs):
        xs = [x for x in xs if x is not None and x == x]
        return sum(xs) / len(xs) if xs else float("nan")

    # ---- summary per stack
    rows = []
    for s in stacks:
        rs = [by[(i, s)] for i in images if (i, s) in by]
        if not rs:
            continue
        per_dec = {d: mean([all(r["verify"]["table"][g][d] for g in DEGS if g in r["verify"]["table"]) for r in rs]) for d in DECS}
        per_deg = {g: mean([sum(r["verify"]["table"][g].values()) / len(DECS) for r in rs if g in r["verify"]["table"]]) for g in DEGS}
        rows.append({"stack": s, "n": len(rs), "match": mean([r["match"] for r in rs]), "match_pre": mean([r["match_pre_budget"] for r in rs]),
                     "ctrl": mean([r["controlled_frac"] for r in rs]), "fid_m": mean([r["fidelity_module"] for r in rs]),
                     "fid_s": mean([r["fidelity_sub"] for r in rs]), "pass": mean([r["verify"]["pass_rate"] for r in rs]),
                     "clean": mean([r["verify"]["clean_pass"] / len(DECS) for r in rs]), "per_dec": per_dec, "per_deg": per_deg,
                     "sec": mean([r["seconds"] for r in rs]), "budget": mean([max(r["budget_used"]) / max(1, r["capacity"]) for r in rs])})

    h = [f"<!doctype html><html><head><meta charset='utf-8'><title>QArt matrix report</title><style>{CSS}</style></head><body>"]
    h.append(f"<h1>Technique matrix — {len(images)} images × {len(stacks)} stacks</h1>")
    h.append(f"<p>Payload <code>{html.escape(meta['payload'])}</code>, version {meta['version']}, {meta['seconds']:.0f} s total. "
             "match = weighted fraction of in-image data modules equal to the target; fid = correlation between rendered luminance and the image at module (m) and sub-module (s) resolution; "
             "pass = fraction of (degradation × decoder) cells that decoded the exact expected text; clean = decoders passing on the clean render.</p>")
    if errors:
        h.append(f"<p class='hot'>{len(errors)} runs failed: " + ", ".join(f"{e['image']}/{e['stack']} ({html.escape(e['error'])})" for e in errors[:10]) + "</p>")

    h.append("<h2>Summary by stack</h2><table><tr><th>stack</th><th>n</th><th>match</th><th>pre-budget</th><th>controlled</th><th>fid m</th><th>fid s</th><th>budget used</th><th>pass</th><th>clean</th>"
             + "".join(f"<th>{d}</th>" for d in DECS) + "".join(f"<th>{g}</th>" for g in DEGS) + "<th>s/run</th></tr>")
    for r in rows:
        h.append(f"<tr><td>{r['stack']}</td><td>{r['n']}</td><td>{r['match']:.3f}</td><td>{r['match_pre']:.3f}</td><td>{r['ctrl']:.2f}</td>"
                 f"<td>{r['fid_m']:.2f}</td><td>{r['fid_s']:.2f}</td><td>{r['budget']:.2f}</td>"
                 f"<td class='{cls(r['pass'], .9, .7)}'>{r['pass']:.2f}</td><td class='{cls(r['clean'], .99, .74)}'>{r['clean']:.2f}</td>"
                 + "".join(f"<td class='{cls(r['per_dec'][d], .95, .7)}'>{r['per_dec'][d]:.2f}</td>" for d in DECS)
                 + "".join(f"<td class='{cls(r['per_deg'][g], .95, .7)}'>{r['per_deg'][g]:.2f}</td>" for g in DEGS)
                 + f"<td>{r['sec']:.1f}</td></tr>")
    h.append("</table><p class='note'>Decoder columns: fraction of images where that decoder passed every degradation. Degradation columns: mean fraction of decoders passing at that degradation. OpenCV's QRCodeDetector fails some perfectly valid plain codes, so treat its column as a lower bound.</p>")

    # ---- contact sheet
    h.append("<h2>Contact sheet</h2><div class='sheet'><table><tr><th></th>" + "".join(f"<th style='text-align:left'>{s}</th>" for s in stacks) + "</tr>")
    for i in images:
        h.append(f"<tr><td class='rowhead'>{i}<small>{html.escape(cats.get(i, ''))}</small></td>")
        for s in stacks:
            r = by.get((i, s))
            if not r:
                h.append("<td class='cell hot'>—</td>")
                continue
            v = r["verify"]
            p = v["pass_rate"]
            bud = f"{max(r['budget_used'])}/{r['capacity']}" if r["budget_limit"] else ""
            h.append(f"<td class='cell'><a href='{i}/{s}.png'><img src='{i}/{s}.png' loading='lazy' title='control view: {i}/{s}.control.png'></a>"
                     f"<div class='m'><span>m {r['match']:.2f}</span><span>f {r['fidelity_sub']:.2f}</span><span class='{cls(p, .9, .7)}'>✓ {p:.2f}</span></div>"
                     f"<div class='m'><span>mask {r['mask']}</span><span>{bud}</span><span>clean {v['clean_pass']}/4</span></div></td>")
        h.append("</tr>")
    h.append("</table></div>")

    # ---- per-image decode detail for the full stacks
    h.append("<h2>Decode detail (degradation × decoder) per image and stack</h2><div class='grid'>")
    for i in images:
        h.append(f"<div class='card'><b>{i}</b> <span class='note'>{html.escape(cats.get(i, ''))}</span><table><tr><th>stack</th>" + "".join(f"<th>{g}</th>" for g in DEGS) + "</tr>")
        for s in stacks:
            r = by.get((i, s))
            if not r:
                continue
            t = r["verify"]["table"]
            cells = "".join("<td>" + "".join(f"<span class='dot {'ok' if t[g][d] else 'bad'}' title='{d}'></span>" for d in DECS) + "</td>" for g in DEGS)
            h.append(f"<tr><td>{s}</td>{cells}</tr>")
        h.append("</table></div>")
    h.append("</div><p class='note'>Dots in each cell are, in order: " + ", ".join(DECS) + ".</p>")
    h.append("</body></html>")
    (out / "report.html").write_text("\n".join(h))


if __name__ == "__main__":
    write_report(Path(sys.argv[1] if len(sys.argv) > 1 else "out/matrix"))
