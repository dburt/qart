"""Module map for a (version, ecl, mask): roles, fixed values, codeword bit layout."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .tables import (
    MASK_FUNCS,
    alignment_positions,
    block_structure,
    format_bits,
    size_for,
    version_bits,
)

# module roles
NONE, FINDER, TIMING, ALIGN, FORMAT, VERSION, DARK, DATA, EXTRA = range(9)
ROLE_NAMES = ["none", "finder", "timing", "align", "format", "version", "dark", "data", "extra"]


@dataclass
class ByteInfo:
    block: int
    is_check: bool
    pos: int  # position within the block (data: 0..nd-1, check: 0..nc-1)
    seq: int  # sequential data byte index before interleaving, -1 for check bytes


@dataclass
class Plan:
    version: int
    ecl: int
    mask: int
    rotation: int = 0
    N: int = 0
    blocks: int = 0
    check: int = 0
    sizes: list[int] = field(default_factory=list)
    data_bytes: int = 0
    total_bytes: int = 0
    role: np.ndarray = None  # uint8 N×N
    fixed: np.ndarray = None  # bool N×N, dark value of function modules
    bit_index: np.ndarray = None  # int32 N×N, index into the interleaved bitstream, -1 if none
    mask_bit: np.ndarray = None  # bool N×N, mask inverts this module
    byte_info: list[ByteInfo] = field(default_factory=list)
    block_byte_to_index: dict = field(default_factory=dict)  # (block, is_check, pos) -> interleaved byte index

    @property
    def capacity(self) -> int:
        """Correctable byte errors per block."""
        return self.check // 2

    def is_data(self) -> np.ndarray:
        return self.role == DATA


def build_plan(version: int, ecl: int, mask: int, rotation: int = 0) -> Plan:
    N = size_for(version)
    role = np.zeros((N, N), np.uint8)
    fixed = np.zeros((N, N), bool)

    def put(x: int, y: int, r: int, dark: bool) -> None:
        role[y, x] = r
        fixed[y, x] = dark

    # finders with separators
    for fx, fy in ((0, 0), (N - 7, 0), (0, N - 7)):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                x, y = fx + dx, fy + dy
                if 0 <= x < N and 0 <= y < N:
                    inner = 0 <= dx <= 6 and 0 <= dy <= 6
                    dark = inner and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
                    put(x, y, FINDER, dark)
    # timing
    for i in range(8, N - 8):
        put(i, 6, TIMING, i % 2 == 0)
        put(6, i, TIMING, i % 2 == 0)
    # alignment
    ap = alignment_positions(version)
    for cx in ap:
        for cy in ap:
            if role[cy, cx] == FINDER:  # the three corner positions overlap finders
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(cx + dx, cy + dy, ALIGN, max(abs(dx), abs(dy)) != 1)
    # format info (two copies)
    fb = format_bits(ecl, mask)
    for i in range(15):
        b = bool((fb >> i) & 1)
        if i < 6:
            put(8, i, FORMAT, b)
        elif i < 8:
            put(8, i + 1, FORMAT, b)
        elif i < 9:
            put(7, 8, FORMAT, b)
        else:
            put(14 - i, 8, FORMAT, b)
        if i < 8:
            put(N - 1 - i, 8, FORMAT, b)
        else:
            put(8, N - 15 + i, FORMAT, b)
    put(8, N - 8, DARK, True)
    # version info
    if version >= 7:
        vb = version_bits(version)
        for i in range(18):
            b = bool((vb >> i) & 1)
            a, c = i // 3, N - 11 + (i % 3)
            put(a, c, VERSION, b)
            put(c, a, VERSION, b)

    # codeword placement (two-column boustrophedon, skipping column 6)
    order: list[tuple[int, int]] = []
    right = N - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vert in range(N):
            y = N - 1 - vert if upward else vert
            for j in range(2):
                x = right - j
                if role[y, x] == NONE:
                    order.append((x, y))
        right -= 2

    blocks, check, sizes = block_structure(version, ecl)
    total = sum(sizes) + blocks * check
    data_bytes = sum(sizes)
    nd = min(sizes)
    seq_start = np.cumsum([0] + sizes[:-1]).tolist()
    byte_info: list[ByteInfo] = []
    for r in range(nd + 1):
        for b in range(blocks):
            if r < sizes[b]:
                byte_info.append(ByteInfo(b, False, r, seq_start[b] + r))
    for r in range(check):
        for b in range(blocks):
            byte_info.append(ByteInfo(b, True, r, -1))
    assert len(byte_info) == total
    index = {(bi.block, bi.is_check, bi.pos): i for i, bi in enumerate(byte_info)}

    bit_index = np.full((N, N), -1, np.int32)
    for i, (x, y) in enumerate(order):
        if i < total * 8:
            role[y, x] = DATA
            bit_index[y, x] = i
        else:
            role[y, x] = EXTRA
    ys, xs = np.mgrid[0:N, 0:N]
    mask_bit = np.vectorize(MASK_FUNCS[mask])(ys, xs) & ((role == DATA) | (role == EXTRA))

    plan = Plan(version, ecl, mask, rotation, N, blocks, check, sizes, data_bytes, total,
                role, fixed, bit_index, mask_bit, byte_info, index)
    if rotation % 4:
        k = rotation % 4
        # rotate clockwise k times so the fixed strips move around the code
        plan.role = np.rot90(plan.role, -k).copy()
        plan.fixed = np.rot90(plan.fixed, -k).copy()
        plan.bit_index = np.rot90(plan.bit_index, -k).copy()
        plan.mask_bit = np.rot90(plan.mask_bit, -k).copy()
    return plan


def matrix_from_codewords(plan: Plan, codewords: bytes) -> np.ndarray:
    """Final module matrix (bool, True = dark) from the interleaved codeword bytes."""
    bits = np.unpackbits(np.frombuffer(codewords, np.uint8))
    m = plan.fixed.copy()
    data = plan.role == DATA
    idx = plan.bit_index[data]
    m[data] = bits[idx].astype(bool) ^ plan.mask_bit[data]
    extra = plan.role == EXTRA
    m[extra] = plan.mask_bit[extra]  # remainder bits are zero before masking
    return m
