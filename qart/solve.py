"""Bit steering: choose codeword bits so data modules match the target image.

Per Reed-Solomon block we keep a basis of valid codewords (one per data bit, with its
check bits) as Python integers and do incremental Gauss-Jordan elimination over GF(2),
exactly as Russ Cox's QArt does. Isolating a bit consumes one degree of freedom; a bit
whose column is already empty is uncontrollable.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from .bits import Message
from .gf256 import rs_check
from .plan import DATA, Plan
from .target import Target


@lru_cache(maxsize=None)
def _basis(nd: int, nc: int) -> tuple[int, ...]:
    rows = []
    for i in range(nd * 8):
        u = bytearray(nd)
        u[i >> 3] = 1 << (7 - (i & 7))
        rows.append(int.from_bytes(bytes(u) + rs_check(u, nc), "big"))
    return tuple(rows)


class BitBlock:
    """One RS block: current codeword plus the remaining basis."""

    def __init__(self, nd: int, nc: int, data: bytes) -> None:
        self.nd, self.nc = nd, nc
        self.L = (nd + nc) * 8
        self.B = int.from_bytes(bytes(data) + rs_check(data, nc), "big")
        self.rows: list[int] = list(_basis(nd, nc))
        self.retired: dict[int, int] = {}  # bit -> pivot row index in self.pivots
        self.pivots: list[int] = []

    def _m(self, i: int) -> int:
        return 1 << (self.L - 1 - i)

    def get(self, i: int) -> int:
        return (self.B >> (self.L - 1 - i)) & 1

    def can_set(self, i: int, val: int) -> bool:
        m = self._m(i)
        rows = self.rows
        j = -1
        for k, r in enumerate(rows):
            if r & m:
                j = k
                break
        if j < 0:
            return False
        pivot = rows[j]
        rows[j] = rows[-1]
        rows.pop()
        for k, r in enumerate(rows):
            if r & m:
                rows[k] = r ^ pivot
        piv = self.pivots
        for k, r in enumerate(piv):
            if r & m:
                piv[k] = r ^ pivot
        if ((self.B & m) != 0) != bool(val):
            self.B ^= pivot
        self.retired[i] = len(piv)
        piv.append(pivot)
        return True

    def reset(self, i: int, val: int) -> None:
        """Flip an already-isolated bit without disturbing other isolated bits."""
        if self.get(i) == val:
            return
        self.B ^= self.pivots[self.retired[i]]

    def to_bytes(self) -> bytes:
        return self.B.to_bytes(self.L // 8, "big")

    def data_bytes(self) -> bytes:
        return self.to_bytes()[: self.nd]

    def check_bytes(self) -> bytes:
        return self.to_bytes()[self.nd:]


@dataclass
class SolveResult:
    codewords: bytes
    data: bytes
    checks: list[bytes]
    control: np.ndarray  # N×N uint8: 0 uncontrolled, 1 controlled, 2 fixed by message, 3 left free, 255 not data
    message: Message
    restarts: int = 0
    stats: dict = field(default_factory=dict)


def _block_module_maps(plan: Plan) -> list[dict]:
    """For each block: arrays mapping block bit -> (y, x) module, for data and check bits."""
    N = plan.N
    maps = []
    for b in range(plan.blocks):
        nd = plan.sizes[b]
        maps.append({"y": np.full((nd + plan.check) * 8, -1, np.int32), "x": np.full((nd + plan.check) * 8, -1, np.int32)})
    ys, xs = np.nonzero(plan.role == DATA)
    for y, x in zip(ys.tolist(), xs.tolist()):
        i = int(plan.bit_index[y, x])
        bi = plan.byte_info[i >> 3]
        nd = plan.sizes[bi.block]
        bb = (nd * 8 if bi.is_check else 0) + bi.pos * 8 + (i & 7)
        maps[bi.block]["y"][bb] = y
        maps[bi.block]["x"][bb] = x
    return maps


def solve(plan: Plan, message: Message, target: Target | None, *, policy: str = "hi",
          data_only: bool = False, dither: bool = False, outside: str = "white",
          seed: int = 0, max_restarts: int = 12) -> SolveResult:
    """Choose codewords. With target None this is a plain encode of the message."""
    N = plan.N
    rng = random.Random(seed)
    maps = _block_module_maps(plan)
    seq_start = np.cumsum([0] + plan.sizes[:-1]).tolist()
    hard_zero: set[int] = set()  # sequential data bit offsets forced to 0 (numeric overflow fix)
    restarts = 0
    while True:
        control = np.full((N, N), 255, np.uint8)
        control[plan.role == DATA] = 0
        blocks: list[BitBlock] = []
        for b in range(plan.blocks):
            nd, nc = plan.sizes[b], plan.check
            s0 = seq_start[b] * 8
            bb = BitBlock(nd, nc, bytes(message.data[seq_start[b]:seq_start[b] + nd]))
            blocks.append(bb)
            my, mx = maps[b]["y"], maps[b]["x"]
            # 1. force message bits
            for i in range(nd * 8):
                if message.fixed[s0 + i] or (s0 + i) in hard_zero:
                    want = 0 if (s0 + i) in hard_zero else (message.data[(s0 + i) >> 3] >> (7 - ((s0 + i) & 7))) & 1
                    if not bb.can_set(i, want):
                        raise RuntimeError("cannot preserve a message bit")
                    y, x = int(my[i]), int(mx[i])
                    if y >= 0:
                        control[y, x] = 2
            if target is None:
                continue
            # 2. candidates in priority order
            cands = [i for i in range(nd * 8) if not message.fixed[s0 + i] and (s0 + i) not in hard_zero]
            if not data_only:
                cands += list(range(nd * 8, (nd + nc) * 8))
            keyed = []
            for i in cands:
                y, x = int(my[i]), int(mx[i])
                if y < 0:
                    continue
                w = float(target.weight[y, x])
                inside = w > 0.05
                if not inside and outside == "free":
                    control[y, x] = 3
                    continue
                var = float(target.variance[y, x])
                if policy == "hi":
                    score = var
                elif policy == "lo":
                    score = -var
                elif policy == "rand":
                    score = rng.random()
                else:
                    raise ValueError(policy)
                keyed.append(((1 if inside else 0), score, rng.random(), i, y, x, inside))
            keyed.sort(reverse=True)
            for _, _, _, i, y, x, inside in keyed:
                want_dark = bool(target.want_dark[y, x]) if inside else False
                bit = int(want_dark) ^ int(plan.mask_bit[y, x])
                control[y, x] = 1 if bb.can_set(i, bit) else 0
        if target is not None and dither:
            _dither(plan, blocks, maps, control, target)
        data = bytearray()
        checks = []
        for bb in blocks:
            data += bb.data_bytes()
            checks.append(bb.check_bytes())
        # numeric overflow fix-up (QArt): any 10-bit group >= 1000 gets one of its high bits forced to 0
        if message.free == "num":
            bad = [off for off, v in zip(message.digit_groups, message.digits_of(bytes(data))) if v >= 1000]
            if bad and restarts < max_restarts:
                for off in bad:
                    hard_zero.add(off + rng.randrange(0, 5))
                restarts += 1
                continue
            if bad:
                raise RuntimeError("numeric overflow could not be fixed")
        break
    from .encode import interleave

    cw = interleave(plan, bytes(data), checks)
    is_data = plan.role == DATA
    stats = {
        "controlled": int((control == 1).sum()),
        "uncontrolled": int((control == 0).sum()),
        "fixed": int((control == 2).sum()),
        "free_left": int((control == 3).sum()),
        "data_modules": int(is_data.sum()),
    }
    return SolveResult(cw, bytes(data), checks, control, message, restarts, stats)


def _dither(plan: Plan, blocks: list[BitBlock], maps: list[dict], control: np.ndarray, target: Target) -> None:
    """Error diffusion over controlled modules (Floyd-Steinberg weights, raster order)."""
    N = plan.N
    err = np.zeros((N, N), np.float32)
    # module -> (block, block bit)
    loc = {}
    for b, mp in enumerate(maps):
        for i, (y, x) in enumerate(zip(mp["y"].tolist(), mp["x"].tolist())):
            if y >= 0:
                loc[(y, x)] = (b, i)
    thr = target.threshold
    for y in range(N):
        for x in range(N):
            if control[y, x] != 1 or target.weight[y, x] <= 0.05:
                continue
            v = float(target.luma[y, x]) + err[y, x]
            dark = v < thr
            b, i = loc[(y, x)]
            blocks[b].reset(i, int(dark) ^ int(plan.mask_bit[y, x]))
            e = v - (0.0 if dark else 255.0)
            if x + 1 < N:
                err[y, x + 1] += e * 7 / 16
            if y + 1 < N:
                if x > 0:
                    err[y + 1, x - 1] += e * 3 / 16
                err[y + 1, x] += e * 5 / 16
                if x + 1 < N:
                    err[y + 1, x + 1] += e * 1 / 16
