"""Decode rendered codes with real decoders under simulated degradations."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

MODELS = Path(__file__).resolve().parents[1] / "models"


class Decoders:
    """Lazy wrappers around zxing-cpp, OpenCV WeChat, OpenCV QRCodeDetector and zbar."""

    def __init__(self) -> None:
        self._wechat = None
        self._wechat_note = ""
        self._cv = None

    @property
    def names(self) -> list[str]:
        return ["zxing", "wechat", "opencv", "zbar"]

    def _init_wechat(self):
        import cv2

        paths = [str(MODELS / f) for f in ("detect.prototxt", "detect.caffemodel", "sr.prototxt", "sr.caffemodel")]
        onnx = [str(MODELS / f) for f in ("detect.onnx", "sr.onnx")]
        attempts = (
            ("onnx", lambda: cv2.wechat_qrcode_WeChatQRCode(detector_model_path=onnx[0], super_resolution_model_path=onnx[1]) if all(Path(p).exists() for p in onnx) else (_ for _ in ()).throw(FileNotFoundError())),
            ("4-arg", lambda: cv2.wechat_qrcode_WeChatQRCode(*paths)),
            ("kw", lambda: cv2.wechat_qrcode_WeChatQRCode(detector_prototxt_path=paths[0], detector_caffe_model_path=paths[1], super_resolution_prototxt_path=paths[2], super_resolution_caffe_model_path=paths[3])),
            ("module", lambda: cv2.wechat_qrcode.WeChatQRCode(*paths)),
            ("no-models", lambda: cv2.wechat_qrcode_WeChatQRCode()),
        )
        for note, fn in attempts:
            try:
                self._wechat = fn()
                self._wechat_note = note
                return
            except Exception:
                continue
        self._wechat = False

    def decode(self, name: str, gray: np.ndarray) -> str | None:
        try:
            if name == "zxing":
                import zxingcpp

                res = zxingcpp.read_barcodes(gray, formats=zxingcpp.BarcodeFormat.QRCode, try_rotate=True, try_downscale=True)
                return res[0].text if res else None
            if name == "wechat":
                if self._wechat is None:
                    self._init_wechat()
                if not self._wechat:
                    return None
                texts, _ = self._wechat.detectAndDecode(gray)
                return texts[0] if len(texts) and texts[0] else None
            if name == "opencv":
                import cv2

                if self._cv is None:
                    self._cv = cv2.QRCodeDetector()
                text, _, _ = self._cv.detectAndDecode(gray)
                return text or None
            if name == "zbar":
                from pyzbar import pyzbar

                res = pyzbar.decode(Image.fromarray(gray), symbols=[pyzbar.ZBarSymbol.QRCODE])
                return res[0].data.decode("utf-8", "replace") if res else None
        except Exception:
            return None
        return None


DECODERS = Decoders()


def to_gray(img: Image.Image) -> np.ndarray:
    if img.mode == "RGBA":
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        img = bg
    return np.asarray(img.convert("L"))


def degrade(img: Image.Image, kind: str) -> Image.Image:
    """Return a degraded copy. The input is the clean render with quiet zone."""
    w, h = img.size
    if kind == "clean":
        return img
    if kind == "scale50":
        return img.resize((w // 2, h // 2), Image.LANCZOS)
    if kind == "scale30":
        return img.resize((max(21, int(w * 0.3)), max(21, int(h * 0.3))), Image.LANCZOS)
    if kind == "blur":
        small = img.resize((w // 2, h // 2), Image.LANCZOS)
        return small.filter(ImageFilter.GaussianBlur(radius=1.2))
    if kind == "jpeg40":
        small = img.resize((w // 2, h // 2), Image.LANCZOS).convert("RGB")
        bio = io.BytesIO()
        small.save(bio, "JPEG", quality=40)
        bio.seek(0)
        return Image.open(bio).convert("RGB")
    if kind == "skew":
        import cv2

        src = np.asarray(img.convert("RGB"))
        pts1 = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dx, dy = 0.12 * w, 0.08 * h
        pts2 = np.float32([[dx, dy], [w - dx * 0.3, 0], [w, h - dy], [dx * 0.4, h]])
        M = cv2.getPerspectiveTransform(pts1, pts2)
        out = cv2.warpPerspective(src, M, (w, h), borderValue=(255, 255, 255))
        return Image.fromarray(out).resize((int(w * 0.6), int(h * 0.6)), Image.LANCZOS)
    raise ValueError(kind)


DEGRADATIONS = ["clean", "scale50", "scale30", "blur", "jpeg40", "skew"]


def verify(img: Image.Image, expected: str, degradations: list[str] | None = None,
           decoders: list[str] | None = None) -> dict:
    """Decode under each degradation with each decoder. Returns a result dict."""
    degradations = degradations or DEGRADATIONS
    decoders = decoders or DECODERS.names
    table: dict[str, dict[str, bool]] = {}
    texts: dict[str, dict[str, str | None]] = {}
    for d in degradations:
        gray = to_gray(degrade(img, d))
        table[d] = {}
        texts[d] = {}
        for name in decoders:
            t = DECODERS.decode(name, gray)
            texts[d][name] = t
            table[d][name] = t == expected
    total = sum(len(r) for r in table.values())
    passed = sum(v for r in table.values() for v in r.values())
    by_decoder = {n: all(table[d][n] for d in degradations) for n in decoders}
    return {
        "table": table,
        "texts": texts,
        "pass_rate": passed / total if total else 0.0,
        "decoders_all_pass": sum(by_decoder.values()),
        "clean_pass": sum(table["clean"].values()) if "clean" in table else None,
        "wechat_mode": DECODERS._wechat_note,
    }
