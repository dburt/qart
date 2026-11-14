"""Spend Reed-Solomon error capacity on the residual mismatches, counted per codeword byte."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .plan import DATA, Plan
from .target import Target


@dataclass
class Ledger:
    capacity: int
    limit: int
    used: list[int]
    candidates: list[int]
    flipped_modules: int = 0
    damaged: np.ndarray | None = None
    per_block: list[dict] = field(default_factory=list)


def spend_budget(plan: Plan, matrix: np.ndarray, target: Target, spend: int | str = 0,
                 reserve: int = 3, min_weight: float = 0.5) -> tuple[np.ndarray, Ledger]:
    """Flip residual modules to their wanted value, choosing whole codeword bytes.

    spend: bytes per block to allow, or "max" for capacity - reserve.
    """
    cap = plan.capacity
    limit = max(0, cap - reserve) if spend == "max" else max(0, min(int(spend), cap - reserve))
    is_data = plan.role == DATA
    resid = is_data & (target.weight >= min_weight) & (matrix != target.want_dark)
    ys, xs = np.nonzero(resid)
    groups: dict[int, list[tuple[int, int]]] = {}
    for y, x in zip(ys.tolist(), xs.tolist()):
        groups.setdefault(int(plan.bit_index[y, x]) >> 3, []).append((y, x))
    per_block: list[list[tuple[float, int]]] = [[] for _ in range(plan.blocks)]
    for byte, mods in groups.items():
        score = sum(float(target.weight[y, x]) * (1.0 + float(target.variance[y, x]) ** 0.5 / 64.0) for y, x in mods)
        per_block[plan.byte_info[byte].block].append((score, byte))
    out = matrix.copy()
    damaged = np.zeros_like(matrix)
    used = [0] * plan.blocks
    flipped = 0
    for b in range(plan.blocks):
        per_block[b].sort(reverse=True)
        for score, byte in per_block[b][:limit]:
            used[b] += 1
            for y, x in groups[byte]:
                out[y, x] = target.want_dark[y, x]
                damaged[y, x] = True
                flipped += 1
    ledger = Ledger(cap, limit, used, [len(p) for p in per_block], flipped, damaged)
    ledger.per_block = [{"block": b, "used": used[b], "limit": limit, "capacity": cap, "candidates": len(per_block[b])} for b in range(plan.blocks)]
    return out, ledger
