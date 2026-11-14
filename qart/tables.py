"""Static QR tables: capacities, block structure, alignment positions, masks."""
from __future__ import annotations

# version -> (total codewords, ((blocks, check bytes per block) for L, M, Q, H))
# Transcribed from rsc.io/qr/coding (ISO/IEC 18004 Table 9).
VERSIONS = {
    1: (26, ((1, 7), (1, 10), (1, 13), (1, 17))),
    2: (44, ((1, 10), (1, 16), (1, 22), (1, 28))),
    3: (70, ((1, 15), (1, 26), (2, 18), (2, 22))),
    4: (100, ((1, 20), (2, 18), (2, 26), (4, 16))),
    5: (134, ((1, 26), (2, 24), (4, 18), (4, 22))),
    6: (172, ((2, 18), (4, 16), (4, 24), (4, 28))),
    7: (196, ((2, 20), (4, 18), (6, 18), (5, 26))),
    8: (242, ((2, 24), (4, 22), (6, 22), (6, 26))),
    9: (292, ((2, 30), (5, 22), (8, 20), (8, 24))),
    10: (346, ((4, 18), (5, 26), (8, 24), (8, 28))),
    11: (404, ((4, 20), (5, 30), (8, 28), (11, 24))),
    12: (466, ((4, 24), (8, 22), (10, 26), (11, 28))),
    13: (532, ((4, 26), (9, 22), (12, 24), (16, 22))),
    14: (581, ((4, 30), (9, 24), (16, 20), (16, 24))),
    15: (655, ((6, 22), (10, 24), (12, 30), (18, 24))),
    16: (733, ((6, 24), (10, 28), (17, 24), (16, 30))),
    17: (815, ((6, 28), (11, 28), (16, 28), (19, 28))),
    18: (901, ((6, 30), (13, 26), (18, 28), (21, 28))),
    19: (991, ((7, 28), (14, 26), (21, 26), (25, 26))),
    20: (1085, ((8, 28), (16, 26), (20, 30), (25, 28))),
    21: (1156, ((8, 28), (17, 26), (23, 28), (25, 30))),
    22: (1258, ((9, 28), (17, 28), (23, 30), (34, 24))),
    23: (1364, ((9, 30), (18, 28), (25, 30), (30, 30))),
    24: (1474, ((10, 30), (20, 28), (27, 30), (32, 30))),
    25: (1588, ((12, 26), (21, 28), (29, 30), (35, 30))),
    26: (1706, ((12, 28), (23, 28), (34, 28), (37, 30))),
    27: (1828, ((12, 30), (25, 28), (34, 30), (40, 30))),
    28: (1921, ((13, 30), (26, 28), (35, 30), (42, 30))),
    29: (2051, ((14, 30), (28, 28), (38, 30), (45, 30))),
    30: (2185, ((15, 30), (29, 28), (40, 30), (48, 30))),
    31: (2323, ((16, 30), (31, 28), (43, 30), (51, 30))),
    32: (2465, ((17, 30), (33, 28), (45, 30), (54, 30))),
    33: (2611, ((18, 30), (35, 28), (48, 30), (57, 30))),
    34: (2761, ((19, 30), (37, 28), (51, 30), (60, 30))),
    35: (2876, ((19, 30), (38, 28), (53, 30), (63, 30))),
    36: (3034, ((20, 30), (40, 28), (56, 30), (66, 30))),
    37: (3196, ((21, 30), (43, 28), (59, 30), (70, 30))),
    38: (3362, ((22, 30), (45, 28), (62, 30), (74, 30))),
    39: (3532, ((24, 30), (47, 28), (65, 30), (77, 30))),
    40: (3706, ((25, 30), (49, 28), (68, 30), (81, 30))),
}

ECL_NAMES = ("L", "M", "Q", "H")
# Two-bit code written into the format information for each level.
ECL_FORMAT_CODE = (1, 0, 3, 2)


def ecl_index(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return ECL_NAMES.index(level.upper())


def size_for(version: int) -> int:
    return 4 * version + 17


def alignment_positions(version: int) -> list[int]:
    """Centre coordinates of alignment patterns (Nayuki's formula)."""
    if version == 1:
        return []
    numalign = version // 7 + 2
    step = 26 if version == 32 else (version * 4 + numalign * 2 + 1) // (numalign * 2 - 2) * 2
    result = [6]
    pos = version * 4 + 10
    for _ in range(numalign - 1):
        result.insert(1, pos)
        pos -= step
    return result


MASK_FUNCS = (
    lambda y, x: (y + x) % 2 == 0,
    lambda y, x: y % 2 == 0,
    lambda y, x: x % 3 == 0,
    lambda y, x: (y + x) % 3 == 0,
    lambda y, x: (y // 2 + x // 3) % 2 == 0,
    lambda y, x: (y * x) % 2 + (y * x) % 3 == 0,
    lambda y, x: ((y * x) % 2 + (y * x) % 3) % 2 == 0,
    lambda y, x: ((y * x) % 3 + (y + x) % 2) % 2 == 0,
)


def format_bits(ecl: int, mask: int) -> int:
    data = (ECL_FORMAT_CODE[ecl] << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ (((rem >> 9) & 1) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def version_bits(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ (((rem >> 11) & 1) * 0x1F25)
    return (version << 12) | rem


def block_structure(version: int, ecl: int) -> tuple[int, int, list[int]]:
    """Return (blocks, check bytes per block, data bytes per block list)."""
    total, levels = VERSIONS[version]
    blocks, check = levels[ecl]
    data_bytes = total - blocks * check
    nd, extra = divmod(data_bytes, blocks)
    sizes = [nd + (1 if b >= blocks - extra else 0) for b in range(blocks)]
    return blocks, check, sizes
