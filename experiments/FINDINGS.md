# Batch experiment findings (2026-10-18)

Setup: 19 images (`images/set/manifest.json`), payload `https://example.com/#`, version 8
(49×49 modules), mask chosen automatically by predicted match. Every output is rendered,
degraded six ways (clean, 50% scale, 30% scale, blur at 50%, JPEG q40 at 50%, perspective
skew at 60%) and decoded by zxing-cpp, OpenCV WeChat (ONNX models), zbar and OpenCV's
QRCodeDetector. "pass" below is the fraction of degradation × decoder cells that returned
exactly the expected text, over zxing, WeChat and zbar. OpenCV's own detector misses 6 of
152 perfectly valid plain codes in `tests/test_plain.py`, so it is reported but not counted.

Metrics: **match** = weighted fraction of in-image data modules equal to the thresholded
target (the ceiling for pure bit steering at this version and URL is about 0.86, because
71% of data modules are controllable and the rest land right half the time); **fid m / s** =
correlation between the rendered luminance and the image at module and sub-module scale.

Full report with every image: `out/matrix/report.html`. Halftone geometry sheet:
`out/halftone/sheet.jpg`.

## Main matrix

| stack | match | fid m | fid s | pass | zxing | wechat | zbar | clean | scale50 | scale30 | blur | jpeg40 | skew |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base-H-ht33 (no steering, ECL H, ⅓ centre halftone; qart.js / amzqr style) | 0.513 | 0.53 | 0.37 | 0.45 | 0.00 | 0.00 | 0.00 | 0.77 | 0.65 | 0.05 | 0.00 | 0.63 | 0.62 |
| base-H-dots (no steering, ECL H, dots over photo; Awesome-qr style) | 0.513 | 0.57 | 0.52 | 0.60 | 0.05 | 0.00 | 0.00 | 0.98 | 0.82 | 0.37 | 0.05 | 0.75 | 0.65 |
| qart-L (steer, pad bytes, plain) | 0.894 | 0.54 | 0.50 | 0.98 | 1.00 | 1.00 | 0.85 | 1.00 | 0.98 | 0.98 | 0.95 | 0.98 | 1.00 |
| qart-L-num (steer, numeric fragment, plain; Cox's original) | 0.887 | 0.53 | 0.49 | 0.99 | 1.00 | 1.00 | 0.90 | 1.00 | 0.98 | 1.00 | 0.97 | 0.98 | 1.00 |
| qart-L-dither | 0.721 | 0.33 | 0.32 | 0.99 | 1.00 | 1.00 | 0.95 | 1.00 | 0.98 | 1.00 | 0.98 | 0.98 | 1.00 |
| qart-L-budget (spend 3 of 9 bytes/block) | 0.908 | 0.56 | 0.52 | 0.98 | 1.00 | 1.00 | 0.75 | 1.00 | 0.98 | 0.98 | 0.92 | 0.98 | 1.00 |
| qart-M-budget (spend 3 of 11) | 0.848 | 0.47 | 0.44 | 0.99 | 1.00 | 1.00 | 0.95 | 1.00 | 1.00 | 1.00 | 0.98 | 0.98 | 1.00 |
| qart-L-ht33 (steer + ⅓ centre halftone) | 0.894 | 0.59 | 0.42 | 0.40 | 0.00 | 0.00 | 0.00 | 0.65 | 0.60 | 0.03 | 0.00 | 0.52 | 0.60 |
| **qart-L-ht64** (steer + ⅔ centre halftone, error diffusion) | 0.894 | 0.61 | 0.58 | 0.96 | 1.00 | 0.80 | 0.85 | 0.97 | 0.93 | 0.97 | 0.97 | 0.93 | 0.98 |
| qart-L-ht64-budget | 0.908 | 0.62 | 0.59 | 0.94 | 1.00 | 0.65 | 0.80 | 0.95 | 0.92 | 0.95 | 0.97 | 0.90 | 0.97 |
| qart-M-ht64-budget | 0.848 | 0.59 | 0.56 | 0.98 | 1.00 | 0.75 | 0.95 | 0.95 | 0.98 | 0.98 | 1.00 | 0.97 | 0.98 |
| qart-L-dots (steer + dots over photo) | 0.894 | 0.62 | 0.60 | 0.62 | 0.15 | 0.40 | 0.00 | 0.87 | 0.75 | 0.45 | 0.23 | 0.68 | 0.72 |

Decoder columns are the fraction of images where that decoder passed all six degradations.

## What the numbers say

1. **Bit steering works and costs nothing in robustness.** Steered codes at ECL L decode
   as reliably as plain ones (pass 0.98, zxing and WeChat perfect), while the module
   pattern matches the image 0.89 instead of 0.51. Cox's claim holds in practice.
2. **Padding bytes are a safe source of free bits.** Every decoder accepted codes whose pad
   bytes were arbitrary, and the decoded text is the clean URL. The numeric-fragment
   variant scored the same but decodes to a 288-character string. Default to padding.
3. **The classic one-third-centre halftone does not scan reliably**, steered or not. zbar
   fails it even on a clean render; zxing and WeChat lose it at 30% scale and under blur.
   A **two-thirds-width centre** (6×6 sub-grid, 4×4 centre) scans at 0.96–0.98 and the
   surround still carries a recognisable photo. Centre width is the dial, not surround
   contrast: reducing surround contrast to 0.6 changed pass rates by at most 0.01.
   Geometry sweep (six images, zxing+WeChat+zbar):

   | centre width | grid/centre | pass |
   |---|---|---|
   | 0.33 | 3/1 | 0.40 |
   | 0.50 | 4/2, 6/3, 8/4 | 0.69, 0.74, 0.91 |
   | 0.60 | 5/3 | 0.95 |
   | 0.63 | 8/5 | 0.94 |
   | 0.67 | 6/4 | 0.98 (0.97 with error diffusion) |

4. **Steering plus halftone is the best-looking robust combination.** It has the highest
   module-scale fidelity (0.61) and sub-module fidelity (0.58) of anything that passes,
   because the centres are right at module scale and the surround adds detail.
5. **The error budget buys a little match for a measurable robustness cost, paid by zbar.**
   At v8-L each block corrects 9 bytes. Spending 1→6 bytes raised match from 0.899 to
   0.920 and dropped the three-decoder pass from 0.98 to 0.94, almost entirely zbar
   (1.00 → 0.45 of images passing everything). zxing and WeChat were unaffected up to 6.
   ECL M with a budget of 3 is as robust as unbudgeted L but has fewer controllable bits
   (match 0.85). Recommendation: L with a spend of 2–3 and a reserve of 3; expose the dial.
6. **Dots over a photo is fragile** whatever the encoding (pass 0.60–0.62), because the
   light modules are translucent dots on arbitrary image luminance. It looks good on
   screens at full size and should be labelled as such.
7. **Error diffusion at module level hurts** at 49 modules (match 0.72, fid 0.33): the
   diffused noise destroys shapes. It belongs in the halftone surround, not in the
   module decisions, at least below version 15 or so.
8. **Residual failures of the recommended stack** are concentrated: WeChat on the
   bold-text logo (boyzone) and busy cover (cd) at 50% scale and JPEG, and zbar on the
   alpha silhouette (heart-alpha) in every condition, where most of the code is driven
   white and zbar cannot find enough structure. Alpha images probably need
   `outside="free"` or a dimmed fill rather than white.

## What is still open

- Image placement and scale were fixed (cover the whole code). QArt's own best results
  come from positioning a small image away from the fixed strips; an automatic search
  over offset and scale is the next solver improvement.
- Match is capped by the 71% controllable fraction at this version and URL length. A
  longer URL lowers it; higher versions add pixels but not proportion. The budget is the
  only way past it besides ECL choice.
- Colour has not been evaluated; the halftone renderer has a keep-chroma mode that is
  untested against decoders.
- No phone cameras yet. The degradations are proxies; a print-and-scan pass with iOS and
  Android would calibrate them.
- Speed: a v8 run with the 8-mask search takes about 4 s in pure Python. Fine for batch,
  too slow for an interactive UI without a Rust/WASM or vectorised solver.
