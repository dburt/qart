"""Turn a data codeword stream into the interleaved final codewords, and plain encoding."""
from __future__ import annotations

import numpy as np

from .bits import layout_message
from .gf256 import rs_check
from .plan import Plan, build_plan, matrix_from_codewords


def split_blocks(plan: Plan, data: bytes) -> list[bytes]:
    out, off = [], 0
    for s in plan.sizes:
        out.append(bytes(data[off:off + s]))
        off += s
    return out


def interleave(plan: Plan, data: bytes, checks: list[bytes] | None = None) -> bytes:
    """Interleave data blocks and check blocks into the final codeword sequence."""
    blocks = split_blocks(plan, data)
    if checks is None:
        checks = [rs_check(b, plan.check) for b in blocks]
    out = bytearray(plan.total_bytes)
    for i, bi in enumerate(plan.byte_info):
        out[i] = checks[bi.block][bi.pos] if bi.is_check else blocks[bi.block][bi.pos]
    return bytes(out)


def encode_plain(payload: str, version: int, ecl: int, mask: int = 2, rotation: int = 0) -> tuple[Plan, np.ndarray, bytes]:
    """Standard QR encoding (byte mode, spec padding). Returns (plan, matrix, codewords)."""
    plan = build_plan(version, ecl, mask, rotation)
    msg = layout_message(payload, version, ecl, free="none")
    cw = interleave(plan, bytes(msg.data))
    return plan, matrix_from_codewords(plan, cw), cw
