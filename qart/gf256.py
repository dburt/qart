"""GF(256) arithmetic with the QR polynomial 0x11d, and the Reed-Solomon encoder."""
from __future__ import annotations

from functools import lru_cache

EXP = [0] * 512
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


@lru_cache(maxsize=None)
def generator(nc: int) -> tuple[int, ...]:
    """Coefficients (highest degree first, leading 1 omitted) of prod_{i<nc} (x - a^i)."""
    g = [1]
    for i in range(nc):
        g = [0] + g  # multiply by x
        root = EXP[i]
        for j in range(len(g) - 1):
            g[j] ^= mul(g[j + 1], root)
    # g now has degree nc with g[-1] == 1 (constant term first)
    g = g[::-1]  # highest degree first
    assert g[0] == 1
    return tuple(g[1:])


def rs_check(data: bytes | bytearray | list[int], nc: int) -> bytes:
    """Reed-Solomon check bytes for the data block."""
    g = generator(nc)
    rem = [0] * nc
    for d in data:
        factor = d ^ rem[0]
        rem = rem[1:] + [0]
        if factor:
            lf = LOG[factor]
            for j in range(nc):
                if g[j]:
                    rem[j] ^= EXP[lf + LOG[g[j]]]
    return bytes(rem)
