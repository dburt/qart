# qart

Artistic QR codes by combining techniques that existing tools use in isolation:

- **Bit steering** (layer A): choose the Reed-Solomon codeword bits so the data modules
  match an image, with no errors introduced. Free bits come from padding bytes (clean
  decoded URL) or from a numeric URL fragment (Russ Cox's original QArt).
- **Error budget** (layer B): flip the remaining mismatches, counted per codeword byte
  per block, keeping a reserve for real-world damage.
- **Halftone rendering** (layer C): the centre of each module carries the module value,
  the surround carries the dithered image. Centre size and surround contrast are dials.
- **Verification** (layer F): decode the rendered bitmap with zxing-cpp, OpenCV's WeChat
  model, OpenCV's QRCodeDetector and zbar under downscale, blur, JPEG and skew.

See `research/RESEARCH.md` for the technique survey, `design/` for UI concepts and the
pipeline flowchart, and `experiments/` for the batch matrix.

## Setup

```sh
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python numpy pillow zxing-cpp pyzbar opencv-contrib-python-headless
```

`pyzbar` needs `libzbar0` on the system. The WeChat decoder needs the ONNX models in
`models/` (`detect.onnx`, `sr.onnx`, from shimat/caffe-ssd-to-onnx releases); without
them it falls back to its classical detector.

## Tests

```sh
.venv/bin/python tests/test_plain.py   # plain encoder vs all decoders, many versions/levels/masks
.venv/bin/python tests/test_qart.py    # steer bits for pjw.png, render, decode; writes out/smoke/
```

## Interactive app

```sh
./run            # creates .venv and installs deps if needed, starts the server, opens your browser
./run --no-open  # server only;  PORT=9000 ./run  for another port
```

Or by hand: `.venv/bin/python -m app.server` and open http://127.0.0.1:8765.

Two primary dials: **Size** (QR version) and **Scannability**, which sets the halftone
centre width and opacity, surround contrast, dot size and opacity, and how much of the
per-block error budget is spent. Style (halftone, plain, dots on photo), colour (mono,
brand colours, hue from the image) and every underlying option sit in the advanced
panels; moving one of them switches the dial to "custom". The preview has final,
controllable-module and residual views; the scannability check runs the four decoders
under six degradations on the actual render after each change. The backend caches the
solve per image and solver settings, so render-only changes return in well under a
second; changing the image, version or encoding re-solves (about 1 s at version 8 with
the 8-mask search, longer for large versions).

## Experiments

```sh
.venv/bin/python experiments/prepare_images.py          # curate images/set/ + manifest
.venv/bin/python experiments/run_matrix.py --quick      # 4 stacks, all images
.venv/bin/python experiments/run_matrix.py              # all stacks (experiments/stacks.py)
open out/matrix/report.html
```

Each run writes `out/<name>/<image>/<stack>.png` (render), `<stack>.control.png`
(controllable-module view), `results.json` and `report.html`.

## Library

```python
from PIL import Image
from qart.pipeline import Options, run
from qart.verify import verify

r = run(Image.open("images/set/pjw.png"),
        Options(payload="https://example.com/#", version=8, ecl="L", free="pad",
                mask="auto", style="halftone", halftone_sub=5, halftone_centre=3,
                halftone_contrast=0.6, spend="max", reserve=3))
r.image.save("out.png")
print(r.stats, verify(r.image, r.expected_text)["pass_rate"])
```

Package layout: `tables.py` (capacities, masks), `gf256.py` (RS), `plan.py` (module map),
`bits.py` (message layout and free bits), `solve.py` (GF(2) elimination), `budget.py`,
`target.py` (image → targets), `render.py`, `verify.py`, `pipeline.py`.
