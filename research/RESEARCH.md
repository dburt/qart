# Artistic QR codes: research notes

Gathered 2026-10-18 from the sources listed at the end. Source repos are cloned
under `research/repos/` (shallow clones, read-only reference).

The goal is a flexible tool that produces QR codes incorporating images by
combining techniques. This document catalogues the techniques, explains the
mechanism behind each, records what each existing project actually does
(verified against source, not READMEs), and ends with a compatibility matrix
and the design questions that fall out of it.

---

## 1. QR fundamentals that matter for art

Everything below follows from how a QR symbol is built and, just as
importantly, from what real decoders actually look at.

### 1.1 Layout

- A version `v` symbol is `4v+17` modules square (v1 = 21, v6 = 41, v10 = 57,
  v40 = 177).
- **Function patterns** (fixed, not data): three 7×7 finder patterns plus a
  1-module white separator; the timing rows/columns (row 6, col 6); 5×5
  alignment patterns (v2+); 15-bit format info (two copies, around the
  finders); 18-bit version info (two copies, v7+); one always-dark module.
- Everything else is **codeword** modules: data codewords followed by
  error-correction (EC) codewords, placed in a two-column boustrophedon
  starting bottom-right, going up, then down, skipping column 6. The
  placement is fixed per version, so **each bit of the final codeword stream
  maps to one known module**. `rsc/qr/coding/qr.go` (`lplan`) and fuqr's
  `blueprint.ts` both build exactly this map, tagging each module with its
  role and bit offset.
- A **mask** (one of 8 XOR patterns) is applied to codeword modules only.
  Normal encoders score all 8 and pick the lowest penalty. Art encoders pick
  the mask first (or search it for best image fit); any mask yields a valid
  symbol.

### 1.2 Data stream

`[mode 4b][char count][payload]... [terminator 0000][pad to byte][pad bytes 0xEC,0x11,...]`

- Modes: numeric (3 digits per 10 bits), alphanumeric (2 chars per 11 bits),
  byte (8 bits/char, UTF-8 in practice), kanji. Modes can be **mixed** within
  one symbol.
- Padding bytes fill the gap between the message and the version's data
  capacity. Decoders stop at the terminator and **ignore pad bytes**.

### 1.3 Error correction

- Data bytes are split into blocks (1 to 81 blocks depending on version/level).
  Each block gets `nc` Reed-Solomon check bytes over GF(256) (poly 0x11d).
  Blocks are then **byte-interleaved** (byte 0 of every block, byte 1 of every
  block, ...), which is why a logo in one region touches many blocks.
- A block can correct up to `floor(nc/2)` wrong **bytes** at unknown positions.
  Errors are counted per byte, not per bit, so eight wrong bits in one
  codeword cost the same as one wrong bit. This is the single most useful
  fact for spending error budget deliberately.
- Levels: L ≈ 7%, M ≈ 15%, Q ≈ 25%, H ≈ 30% of codewords recoverable. L
  maximises data bits (hence maximises bit-steerable modules); H maximises
  the budget for damage done at render time. This is the central tension
  between technique families.
- Two RS properties exploited by bit steering:
  1. **Systematic**: data bytes appear verbatim, check bytes follow.
  2. **Linear**: the XOR of two valid codewords is a valid codeword (the set
     of valid blocks is a vector space over GF(2), and also a linear code over
     GF(256)). Equivalently, RS codes are MDS: *any* `k` of the `k+nc` symbols
     uniquely determine the rest.

### 1.4 What decoders actually do (de facto behaviour)

Collected from ZXing/OpenCV/WeChat behaviour, the Halftone QR paper, fuqr's
"undocumented knowledge" notes and Anthony Fu's AI-QR work:

- **Sampling**: after locating finders and estimating the grid (perspective
  transform), decoders sample the luminance **near the centre of each
  module**. The Halftone QR paper shows the centre third (a/3 × a/3) of a
  module is what needs to carry the correct colour. Everything in the outer
  8/9 of a module is, within reason, free.
- **Binarisation** is by luminance with a local/adaptive threshold (ZXing's
  hybrid binarizer uses 8×8 blocks). Hue is irrelevant; local contrast is
  what counts. Dark blue on light yellow decodes fine; dark grey on mid grey
  does not.
- **Finder patterns** are located by the 1:1:3:1:1 dark/light run ratio
  along scanlines through them. They must be preserved in ratio and
  contrast, but their *shape* can be rounded, circular, etc.
- **Timing patterns are not used** by mainstream decoders (fuqr notes;
  fuqr's PixelArt overwrites them). Keep them unless you need the pixels.
- **Alignment patterns**: fuqr reports only the bottom-right one is used,
  and small codes decode without it. Larger/warped scans rely on them more.
- **Format info** is BCH-coded with two copies and tolerates 3 bit errors;
  version info similarly. These are small but critical; Fu found many AI-QR
  failures were format/version damage, not data damage.
- **Quiet zone**: spec says 4 modules; most decoders cope with 1–2 if the
  surroundings are not QR-like. Noise in the margin is survivable if a clear
  ring is kept at least around the finders (qrframe/qrcode-toolkit patterns).
- Mask choice has no measurable effect on scannability (fuqr); mask 2
  (vertical stripes) is a sensible default for art because its regular
  structure is easy to reason about.

---

## 2. Technique catalogue

Organised by the *layer* at which the technique operates. Layers compose; see
section 4.

### Layer A: Encoding-level bit steering (choose the bits to match the image)

The image is realised by the actual module values. No errors are introduced.
The decoded message is still correct.

#### A1. QArt (Russ Cox, 2012) — `research/repos/qr/qart/qr.go`

The reference technique. Verified mechanics from the Go source:

1. **Free bits.** Encode `URL + "#"` in byte mode, then a numeric-mode
   segment whose digits fill all remaining data bits (`dbit/10*3` digits).
   Browsers ignore unknown fragments, so the URL still works. Numeric mode is
   used because its 10-bit groups accept any value < 1000, i.e. 9.96 of 10
   bits are free, whereas arbitrary bytes would decode as garbage in the
   visible message. Everything after the numeric segment (remaining <10 bits)
   stays as terminator/padding and is preserved.
2. **Level L, user-chosen mask (default 2), version clamped to ≤ 8 in the web
   tool** (runtime grows with version; the table supports all 40).
3. **Target image**: resized so one image pixel ≈ one module
   (`17+4v+Size`), offset by `Dx, Dy`; converted to luma; threshold at 128.
   Transparent pixels and modules outside the image get target **white**
   with the lowest priority, which is why QArt output tends toward a white
   field around the picture.
4. **Per RS block, Gaussian elimination over GF(2).** Build the basis matrix
   `M` with one row per data bit: unit vector plus its RS check bits
   (`newBlock`). `canSet(bit, value)` finds a basis row with that bit set,
   XORs it out of all other rows (so no other row touches that bit any more),
   applies it to the codeword if needed, and retires the row. Each
   successfully isolated bit consumes one degree of freedom; a bit is
   uncontrollable when no remaining row has it set. The retired rows are kept
   (in the slice's spare capacity) so `reset()` can flip an already-chosen
   bit later, which the dither pass uses.
5. **Order of attempts = priority.** Bits fixed by the URL prefix and
   terminator are forced first (`canSet` must succeed or it errors). Then all
   editable data bits and all check bits of the block are sorted by
   `priority = contrast << 8 | random`, where `contrast` is the luma
   *variance* in an 11×11 window around the module. **The Go code processes
   high-variance modules first**; the blog text says low-contrast regions
   matter more, and pyqart defaults to low-contrast-first with a
   `higher_first` switch. Treat this as a tunable, not a settled fact.
   Random mode replaces contrast with a random number, producing the "binary
   fog" look.
6. **Data-only option** skips check bits in the candidate list. It is much
   faster (fewer candidates) and leaves the check-bit region as noise.
7. **Numeric overflow fix-up.** After choosing, every 10-bit group ≥ 1000 has
   bit index 3 of the group marked `HardZero` with huge priority and the whole
   process restarts (`goto Again`). (All of bits 512..32 must be set for the
   group to overflow, so forcing one of them to zero fixes it.)
8. **Dither** (optional): a second pass over the *chosen* modules applying
   error diffusion with the 7/16 right-neighbour term only (the vertical
   terms are disabled with `if false`), flipping chosen bits via `reset()`.
9. **Rotation**: rotates the plan's pixel map 0/90/180/270° so the
   uncontrollable URL-prefix strip (right side) and check-bit region (left)
   land where the image can spare them.
10. **Control view**: renders controlled modules in full black/white and
    uncontrolled ones greyed, a very useful debug/UX affordance.
11. Finally the chosen digits are re-encoded through the normal encoder and
    the result is byte-compared against the steered codewords as a self-check.

Capacity intuition: data bits controllable ≈ `8·DataBytes − URL bits`;
check bits are only partially controllable (their basis rows are dependent),
and every controlled check bit costs a data-bit degree of freedom, so the
total controllable count is fixed and only its *distribution* can be moved.
Output quality therefore rises steeply with version (more bits per image area
and more freedom relative to the URL cost).

Ports: `pyqart` (Python, MIT; adds `only_data`, `rotation`, `higher_first`,
dither; works at Python speed and is slow in full mode), `qart4j` (Java,
GPLv3; adds colour output, CLI geometry options). `kciter/qart.js` is **not**
a QArt port despite the name (see C2).

#### A2. Padding-symbol solving + error budget (fuqr `PixelArt`, 2024–2026) — `research/repos/fuqr/typescript/src/extras/PixelArt.ts`

A modern, byte-granular reformulation with three advances over QArt:

1. **Free bits come from padding, not a URL fragment.** If the message uses
   `c` of `k` data symbols, the `p = k − c` pad symbols are arbitrary to a
   decoder. The decoded URL is therefore **clean** (no giant numeric
   fragment), and the free-bit count is the same as QArt's (slightly higher,
   since no numeric header or <1000 constraint). The author chooses a version
   large enough for the stencil. Risk: a hypothetical strict decoder that
   validates `0xEC/0x11` padding. In practice none do; this is the main
   decision to make for our tool (offer both, default padding).
2. **GF(256) linear solve instead of GF(2) elimination.** Forcing bits in a
   *data* column just sets the byte (systematic). Forcing bits in an *EC*
   column pairs that constraint with one unknown pad byte and solves a `t×t`
   system `A·x = b` over GF(256) (`solveBlock`, `gf256Solve`); the system is
   always invertible because every square submatrix of an RS parity matrix
   is non-singular (MDS). Granularity cost: one EC-byte constraint consumes a
   whole pad byte even when only one bit of it is forced, so for sparse
   constraints QArt's bit-level elimination is up to 8× more efficient. For
   dense pixel art (every bit in a byte forced) they are equivalent.
3. **Weighted stencil and a deliberate error budget.** Each module carries
   `(weight<<1)|bit`; weight 0 = don't care. Constraints that cannot be
   satisfied by pad solving (message bytes, or more EC fixes than pad bytes)
   become **breaks**: the byte is overwritten anyway, sorted by weight, up to
   `floor(nc/2) − reservedEc` per block (default reserve 3 bytes for
   real-world damage). This lets the picture extend over the message bytes
   themselves, at half the efficiency of solving.
4. Also overwrites timing patterns and all alignment patterns except the
   bottom-right one with stencil values (exploiting decoder behaviour).
5. **Animation support**: across frames, uncovered pad and EC bytes are
   pinned to their previous values via extra solver unknowns so only `r`
   bytes churn per frame and the EC region does not strobe (`horse.ts`).
   Uses ECL L, mask fixed.

#### A3. Choosing version / mask / rotation / position for fit

All encoders expose these as user knobs (QArt: rotate button, arrow keys;
qrframe/qrcode-toolkit: mask 0–7, min/max version, rotate). Nobody searches
them automatically. Cheap win: score all 8 masks × 4 rotations × a few
offsets by predicted mismatch and pick the best, since the solve is fast
relative to a UI interaction.

#### A4. Alternative free-bit sources

- Numeric fragment (QArt): spec-pure, URL grows by hundreds of digits.
- Padding (fuqr): clean URL, relies on decoders ignoring pads (they do).
- Alphanumeric fragment: 2025 of 2048 values valid per 11 bits (≈99%), gives
  uppercase/digit noise instead of digits; no real advantage.
- Query parameter instead of fragment: server sees it; avoid.
- For non-URL payloads (vCard, Wi-Fi) only padding works cleanly.

### Layer B: Error-budget spending (render damage the decoder can repair)

The symbol is encoded normally; some modules are then rendered wrong on
purpose, counted against RS capacity.

#### B1. Logo hole with exact per-block accounting (fuqr `FittedLogo`)

Most libraries (qr-code-styling, art-qr, qrframe Basic/Circle) just skip
drawing modules under a centred logo, recommend ECL Q/H and an `imageSize`
≤ 0.4–0.5, and leave the risk to the user. fuqr instead rasterises the logo's
alpha stencil onto the module grid (with a `gap` ring), maps every covered
module to its codeword via the blueprint, counts **distinct damaged
codewords per block**, and grows the version (forcing ECL H) until every
block stays within `floor(nc/2) − reserve`. This is the right primitive: an
*error ledger* per block that every damaging technique debits.

#### B2. Deliberate flips of uncontrollable modules

After bit steering (A1/A2) some modules still mismatch the image. With a
ledger, the most visible mismatches can simply be flipped, preferring
modules that share a codeword already debited (a second wrong bit in an
already-wrong byte is free). QArt does not do this; fuqr's breaks are the
same idea applied to forced bytes. Combining A1 + B2 is the main unexplored
gain: use L for freedom, then spend the modest L budget (and keep a reserve)
on the worst residuals, or use M/Q and trade freedom for budget.

#### B3. ECL as a dial, not a constant

With a ledger the choice becomes quantitative: per block, freedom (data
bits) vs repairable damage (`floor(nc/2)` bytes). The tool should show the
remaining budget per block and let the user move the dial.

### Layer C: Module-geometry rendering (keep the centre, free the rest)

Rendering tricks that exploit centre sampling. The encoding is untouched, so
these compose with A and B.

#### C1. Halftone QR (Chu, Chang, Lee, Mitra, SIGGRAPH Asia 2013)

Each module is split into 3×3 submodules; the centre submodule carries the
module's true colour; the other eight carry a halftoned version of the image.
The paper additionally (a) optimises which 3×3 binary patterns to use per
module, balancing image similarity against a measured *reliability* of each
pattern under decoder sampling, with a regularisation term for visual
coherence, and (b) spends some error correction to let selected whole
modules follow the image. Follow-ups: Micrography QR codes (text instead of
halftone dots), "Stylize Aesthetic QR", ART-UP (module scanning-probability
models), ArtCoder/QRnet (neural style transfer with a module-based code loss
and sampling-simulation layer). Common finding across that literature:
module value is determined by the centre third; aesthetics come from the
outer region plus a probabilistic error budget.

Practical implementations of the simple version (image everywhere, centre
1/3 dot truthful):

- `qart.js` (kciter): image drawn at 3 px/module, every `(x%3==1 && y%3==1)`
  pixel kept as QR; image thresholded (Rec.709 luma ≥ 127) or in colour;
  finders protected as 8×8 corners; alignment patterns copied back; **format,
  timing and version modules are not protected** beyond their centre pixel;
  ECL forced H; v10 default. No dither.
- `amazing-qr` (amzqr): same 3 px/module scheme with a paste mask that keeps
  finders (8×8), timing row/col, alignment 5×5 boxes and the centre sub-pixel
  of every module; image gets PIL contrast/brightness, converted to 1-bit
  (no dither) or kept in colour; default ECL H; GIF frames processed
  individually to make animated codes.
- `qrframe` Halftone preset: 3 px/module, image with CSS brightness/contrast,
  **Floyd–Steinberg dither** to fg/bg, then finders (optionally alignment and
  timing) full-size and every other module as the centre pixel only.
- `pyqart` `QrHalftonePrinter` ("HalfArt"): pastes the image through a mask
  that exposes everything except the centre third of data/EC modules; when
  driven by a `QArtist` the module values already match the image, so the
  centre dots and the halftone agree. **This is the only existing A + C
  combination**, and it is the proof that the two layers compose.
- `fuqr` `dithering.ts`: Bayer-ordered dither at one cell per module third,
  then module centres overlaid; notes that downscaling plus dithering "looks
  especially good".

#### C2. Shrunken dots over a photo (Awesome-qr / art-qr, Q-Art-Codes, qr-code-styling "dots")

The full-colour image is the background; each data module is a small square
or circle (`dotScale` 0.3–0.4) at the module centre: dark modules in a dark
colour, light modules in translucent white. Function patterns are drawn full
size, usually on a translucent white pad. Variants: `maskedDots` (each dot is
a crop of the image tinted 50% dark / 70% white), `autoColor` (dark colour =
image average ignoring near-white), `backgroundDimming` overlay for contrast,
`binarize` for a pure B/W result, and GIF backgrounds. Neither art-qr nor
Q-Art-Codes analyses the image; scannability comes from the dots' own
contrast plus ECL. These are visually weaker than C1 because the dot grid is
imposed on the picture rather than drawn from it, but they handle arbitrary
colour photos with zero preprocessing.

#### C3. Module and finder styling (qr-code-styling, qrcode-toolkit, qrframe)

Pure aesthetics, compatible with everything:

- Pixel styles: square, rounded, dot, squircle, classy, row/column-connected
  runs, random size.
- Finder styles: rounded, circle, plus, box, octagon, "tiny-plus", per-corner
  overrides, inner shape overrides. Plus-shaped markers blend better but
  reduce scannability (Fu).
- Gradients and multi-colour palettes; all fine as long as luminance contrast
  at module centres survives.
- Cluster substitution: `art-qrcode` greedily matches dark-module runs and 2×2
  blocks (4-run → 2×2 → 3-run → L → 2-run → single) and swaps in designer
  sprites; qrframe's Blocks/Bubbles/Alien/Quantum do the same with vector
  shapes (X strokes for 3×3, rings for plus clusters, bars for runs, atoms for
  finders).
- Region tracing: qrframe's Camo/Neon/Glass/Drawing/Mondrian trace connected
  regions of same-valued modules into rounded outlines, stained-glass shards,
  Voronoi fills or hand-drawn roughjs strokes, often padding the margin with
  random modules first so the code has no hard square edge.
- Misregistration/layer effects: duplicate the module path with sub-module
  offsets and blend modes (CMYK-misprint look); spiral dot sampling.
- Margin noise with a protected ring around finders (`marginNoiseSpace`:
  none/marker/full/minimal/extreme), per-side margins, perspective transform
  of the whole code.

### Layer D: Colour and luminance

- Chroma is a free channel. Keep the image's hue and saturation everywhere
  and quantise only luminance at module centres (Visualead-style "QR
  images", Garateguy et al.). Combined with A-layer steering this yields a
  colour picture whose luminance structure *is* the code.
- Contrast management: dim or brighten the photo globally (overlay),
  enhance contrast before thresholding (amzqr `-con`, qrframe filters), or
  locally push module centres apart (Fu's "preview corrected": lighten
  too-dark light modules, darken too-light dark ones, only where needed).
- Dark/light are relative: inverted codes (light modules on dark) are
  decodable by most modern readers but not all; the toolkit exposes `invert`.

### Layer E: Generative / diffusion (ControlNet "QR Code Monster", 2023)

Not something to reimplement, but a consumer of everything above. The base
QR is a ControlNet conditioning image; **grey (#808080) means "don't care"**
to the QR-Monster/QR-Pattern models. Fu's method: render only selected
regions (data / function / finders) on grey to make multi-layer controls
(full code at medium weight, function patterns at high weight), add a
low-weight Brightness ControlNet to raise local contrast, use dotted or
crystallised inputs so the model composes freely, then verify with the
compare tool and repair with img2img at low denoise. Our tool can emit these
conditioning images directly (per-role renders, grey don't-care, margin
noise, perspective).

### Layer F: Verification and feedback

The missing piece in almost every project (only qrcode-toolkit has it):

- **Decode the actual output** with real decoders (zxing-cpp, OpenCV
  QRCodeDetector and its WeChat model, zbar) at several scales, with blur,
  JPEG compression and mild perspective, and report pass/fail per decoder.
- **Module diff** (toolkit `diff.ts`): overlay the intended grid on the
  rendered/generated image, sample each module's luminance, classify against
  an adaptive threshold, and list mismatches split into dark→light and
  light→dark, with the per-block error count against capacity.
- **Correction mask export** for manual or inpainting repair.
- QArt's own self-check (re-encode and compare) and its "show controllable
  pixels" view belong here too.

---

## 3. Project summary table

| Project | Language / licence | Changes encoding? | Core technique | Image analysis | ECL | Verification | Notes |
|---|---|---|---|---|---|---|---|
| rsc/qr `qart` | Go, BSD | **Yes** (GF(2) elimination) | A1 QArt | luma threshold, 11×11 variance priority, optional horizontal dither | L fixed | re-encode check, control view | WASM web tool; v ≤ 8 in UI; URL fragment of digits |
| pyqart | Python, MIT | Yes (port) | A1 + C1 "HalfArt" | as above, low-contrast-first default | L | none | slow in full mode; only A+C combo found |
| qart4j | Java, GPLv3 | Yes (port) | A1 | as above | L | none | colour output, geometry CLI |
| fuqr / furious-qr | TS + Rust, MIT | **Yes** (GF(256) pad solve + breaks) | A2, B1, C1 examples | weighted stencil supplied by caller | L (PixelArt), H (FittedLogo) | none | animation-stable EC; overwrites timing/alignment |
| qrframe | TS (Solid), MIT | No | C1 Halftone preset, C3 (16 presets) | F-S dither in Halftone | user | none | schema-driven param UI, worker-isolated user render functions |
| qrcode-toolkit | TS (Vue), MIT | No | C3, E conditioning, **F** compare | luminance sampling diff | user | **yes** (grid diff, correction export, WeChat WASM scanner) | mask/version/rotate/invert exposed; crystalize/liquidify effects |
| qr-code-styling | TS, MIT | No | C3, B (naive logo hole) | none | Q default | none | most-used styling lib; SVG + canvas |
| kciter/qart.js | JS, GPLv3 | No | C1 (centre pixel) | Rec.709 threshold or colour | H fixed | none | not a QArt port; format/timing unprotected |
| amazing-qr | Python, GPLv3 | No | C1 (centre pixel), GIF | contrast/brightness, 1-bit | H default | none | mask protects finders/timing/alignment |
| LIU9293/art-qr (Awesome-qr fork) | JS, no licence file | No | C2 dots over photo | average colour | M default | none | GIF output; abandoned 2017 |
| shadowmoose/Q-Art-Codes | TS, MIT | No | C2 dots over photo | none | user | none | SVG/Node/browser backends; stale |
| Chenyating/art-qrcode | JS, Apache/ISC/MIT mixed | No | C3 sprite substitution | none | H fixed | none | random run direction; nothing but finders protected |

---

## 4. Compatibility: which techniques combine

Think of the pipeline as **choose bits → spend budget → render → verify**.
Techniques in different layers compose; techniques in the same layer are
alternatives.

| | A1 QArt bits | A2 pad-solve | B1/B2 budget | C1 halftone | C2 dots on photo | C3 styling | D colour | E diffusion | F verify |
|---|---|---|---|---|---|---|---|---|---|
| **A1 QArt bits** | – | alt (same slot; hybrid possible: bit-level solve with pad bytes as the free source) | **yes, strong**: flip worst residuals within budget | **yes** (pyqart proves it): centres already match, halftone fills the rest | yes, but pointless unless dots are tiny | yes | yes | yes (better conditioning image) | yes |
| **A2 pad-solve** | alt | – | built in (breaks) | yes | yes | yes | yes | yes | yes |
| **B budget** | yes | yes | – | yes (paper does exactly this) | yes | yes (logo holes) | yes | implicitly how E works | essential |
| **C1 halftone** | yes | yes | yes | – | alt (both decide what fills the outer 8/9) | partial (dot shapes yes; region tracing no) | yes | yes | yes |
| **C2 dots on photo** | weak | weak | yes | alt | – | yes | yes | yes | yes |
| **C3 styling** | yes | yes | yes | partial | yes | – | yes | yes | yes |

Key compatibility facts:

- **A + C1 is the headline combination.** Bit steering makes module centres
  agree with the picture at module resolution; the halftone/sub-module layer
  restores detail at 3× resolution; the two never fight because the centre
  third is the only thing both care about. Error diffusion should run once,
  over the combined output, not separately per layer.
- **A + B is the second big win.** Bit steering is error-free; the budget
  then goes to the residual mismatches, preferring bits that share an
  already-damaged byte. Needs the per-block ledger and a reserve for
  real-world damage.
- **A conflicts with "clean decoded text" only if you use QArt's numeric
  fragment.** Padding solving fixes that; a hybrid (QArt's bit-level
  elimination using pad bytes as the free source) gets both the finer
  granularity and the clean URL.
- **A requires choosing the mask before the data**, which rules out
  spec-standard mask scoring. Harmless in practice.
- **C2 and C1 are alternatives**, not complements: both decide how the outer
  region of each module looks.
- **Region-tracing styles (Camo, Neon, Glass, Mondrian) and sprite
  substitution assume the module pattern is arbitrary**; with image-steered
  bits they will trace the image's shapes, which may be a feature.
- **Everything is compatible with F**, and F should be mandatory in the
  loop.
- **Fixed patterns are the hard limit.** Finders (3 × 8×8 incl. separator),
  format (31 modules), version info (36 modules at v7+), the dark module.
  Timing and alignment are negotiable per decoder evidence (fuqr), but
  overwriting them costs robustness on skewed scans. Image placement,
  rotation and version choice are the levers; the tool should show the
  uncontrollable regions (QArt's control view) and let the user drag the
  image around them.

---

## 5. Design implications (to discuss)

1. **Core data model: a per-module plan** with role, bit offset, block id,
   codeword index, mask bit, controllability and a target (desired
   luminance, weight). Both `rsc/qr` (`Pixel` bitfield with role + offset)
   and fuqr (`blueprint` with block/offset key) are good prior art. Add a
   per-block **error ledger**.
2. **Solver**: bit-level GF(2) elimination (QArt) generalised to take free
   bits from either a numeric fragment or pad bytes, with weights and a
   priority policy (contrast-first either direction, random, user mask),
   plus fuqr's break budget. Fast enough in Rust/WASM or TS for v ≤ 15
   interactively; QArt's clamp at v8 was a Go-WASM-in-2012 choice.
3. **Targets come from a separate image stage**: resample to module grid
   (or 3× grid for halftone), luma, optional contrast stretch, optional
   dither, alpha → don't-care, per-pixel weights from saliency/variance or a
   user-painted mask. Colour is kept aside for the renderer.
4. **Renderer as a pluggable function over the plan** (qrframe's model):
   receives modules with roles, the solved bits, the per-module residual
   error, and the source image; emits SVG or canvas. Presets: plain,
   halftone 3×3, dots-on-photo, styled pixels/finders, region-traced,
   conditioning-image-for-diffusion (roles on grey).
5. **Verification is a first-class stage**: decode with ≥ 2 real decoders at
   several degradations; module-diff overlay; per-block budget readout;
   export correction mask.
6. **Auto-search** over mask × rotation × offset × version for best predicted
   fit; expose the choice but default to automatic.
7. **Open questions**: default free-bit strategy (pad vs fragment); whether
   to allow overwriting timing/alignment (make it a "risky" toggle); how
   much reserve EC to insist on; target platform (web/WASM like the three
   best references, or a Python/Rust CLI with a web front end).

---

## 6. Sources

Primary articles and papers
- Russ Cox, "QArt Codes" (2012): https://research.swtch.com/qart
- Russ Cox, QArt Coder (Go + WASM tool): https://research.swtch.com/qr/draw/
- Russ Cox, "Finite Field Arithmetic and Reed-Solomon Coding": https://research.swtch.com/field
- Chu, Chang, Lee, Mitra, "Halftone QR Codes", ACM TOG 32(6), SIGGRAPH Asia 2013: https://dl.acm.org/doi/10.1145/2508363.2508408
- Xu et al., "Stylize Aesthetic QR Code" (2018): https://arxiv.org/abs/1803.01146
- Xu et al., "ART-UP: scanning-robust aesthetic QR codes" (2018): https://arxiv.org/abs/1803.02280
- Garateguy et al., "QR images: optimized image embedding in QR codes" (luminance embedding, bounded error probability)
- Anthony Fu, "Stable Diffusion QR Code 101": https://antfu.me/posts/ai-qrcode-101
- Anthony Fu, "Refining AI Generated QR Code": https://antfu.me/posts/ai-qrcode-refine
- Monster Labs, ControlNet QR Code Monster model card: https://huggingface.co/monster-labs/control_v1p_sd15_qrcode_monster

Repositories (cloned under `research/repos/`)
- rsc/qr (incl. `qart/`): https://github.com/rsc/qr
- 7sDream/pyqart: https://github.com/7sDream/pyqart
- dieforfree/qart4j: https://github.com/dieforfree/qart4j
- zhengkyl/fuqr (furious-qr): https://github.com/zhengkyl/fuqr
- zhengkyl/qrframe: https://github.com/zhengkyl/qrframe
- antfu/qrcode-toolkit: https://github.com/antfu/qrcode-toolkit
- kozakdenys/qr-code-styling: https://github.com/kozakdenys/qr-code-styling
- kciter/qart.js: https://github.com/kciter/qart.js
- x-hw/amazing-qr: https://github.com/x-hw/amazing-qr
- LIU9293/art-qr (fork of SumiMakito/Awesome-qr.js): https://github.com/LIU9293/art-qr
- shadowmoose/Q-Art-Codes: https://github.com/shadowmoose/Q-Art-Codes
- Chenyating/art-qrcode: https://github.com/Chenyating/art-qrcode
