"""Message bitstream construction and the layout of fixed versus free bits."""
from __future__ import annotations

from dataclasses import dataclass, field

from .tables import VERSIONS, block_structure

ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


class BitBuffer:
    def __init__(self) -> None:
        self.bits: list[int] = []

    def append(self, value: int, nbits: int) -> None:
        for i in range(nbits - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def __len__(self) -> int:
        return len(self.bits)

    def to_bytes(self) -> bytes:
        bits = self.bits + [0] * (-len(self.bits) % 8)
        out = bytearray()
        for i in range(0, len(bits), 8):
            v = 0
            for b in bits[i:i + 8]:
                v = (v << 1) | b
            out.append(v)
        return bytes(out)


def count_bits(mode: str, version: int) -> int:
    cls = 0 if version <= 9 else 1 if version <= 26 else 2
    return {"num": (10, 12, 14), "alnum": (9, 11, 13), "byte": (8, 16, 16)}[mode][cls]


def byte_segment(buf: BitBuffer, data: bytes, version: int) -> None:
    buf.append(0b0100, 4)
    buf.append(len(data), count_bits("byte", version))
    for b in data:
        buf.append(b, 8)


def numeric_segment(buf: BitBuffer, digits: str, version: int) -> None:
    buf.append(0b0001, 4)
    buf.append(len(digits), count_bits("num", version))
    i = 0
    while i + 3 <= len(digits):
        buf.append(int(digits[i:i + 3]), 10)
        i += 3
    rest = len(digits) - i
    if rest == 1:
        buf.append(int(digits[i:]), 4)
    elif rest == 2:
        buf.append(int(digits[i:]), 7)


def byte_segment_bits(n: int, version: int) -> int:
    return 4 + count_bits("byte", version) + 8 * n


def min_version(payload: str, ecl: int, free: str = "pad", min_free_bytes: int = 0) -> int:
    """Smallest version whose data capacity holds the payload (byte mode) plus any free bytes wanted."""
    n = len(payload.encode("utf-8"))
    for v in range(1, 41):
        total, levels = VERSIONS[v]
        blocks, check = levels[ecl]
        cap = (total - blocks * check) * 8
        need = byte_segment_bits(n, v) + 4
        if free == "num":
            need += 4 + count_bits("num", v)
        if need + 8 * min_free_bytes <= cap:
            return v
    raise ValueError("payload too long for any version")


@dataclass
class Message:
    """The data codeword stream (before interleaving) and what is fixed in it."""
    version: int
    ecl: int
    free: str
    payload: str
    data: bytearray  # initial data codewords, sequential (block 0, block 1, ...)
    fixed: list[bool]  # per bit of data stream, True if the bit is determined by the message
    digit_groups: list[int] = field(default_factory=list)  # bit offsets of 10-bit numeric groups (num mode)
    nbits: int = 0

    @property
    def free_bits(self) -> int:
        return sum(1 for f in self.fixed if not f)

    def expected_text(self, data: bytes) -> str:
        """What a decoder should print for the given final data codewords."""
        if self.free != "num":
            return self.payload
        digits = []
        for off in self.digit_groups:
            v = 0
            for j in range(10):
                v = (v << 1) | ((data[(off + j) >> 3] >> (7 - ((off + j) & 7))) & 1)
            digits.append(f"{v:03d}")
        return self.payload + "".join(digits)

    def digits_of(self, data: bytes) -> list[int]:
        out = []
        for off in self.digit_groups:
            v = 0
            for j in range(10):
                v = (v << 1) | ((data[(off + j) >> 3] >> (7 - ((off + j) & 7))) & 1)
            out.append(v)
        return out


def layout_message(payload: str, version: int, ecl: int, free: str = "pad") -> Message:
    """Build the data codeword stream.

    free == "none": standard encoding, 0xEC/0x11 padding, everything fixed.
    free == "pad":  standard encoding, pad bytes left free (decoders ignore them).
    free == "num":  payload then a numeric segment whose digit groups are free (QArt).
    """
    blocks, check, sizes = block_structure(version, ecl)
    D = sum(sizes)
    cap = D * 8
    raw = payload.encode("utf-8")
    buf = BitBuffer()
    byte_segment(buf, raw, version)
    if len(buf) > cap:
        raise ValueError(f"payload needs {len(buf)} bits, version {version}-{ecl} has {cap}")
    fixed = [True] * len(buf)
    groups: list[int] = []
    if free == "num":
        head = 4 + count_bits("num", version)
        avail = cap - len(buf) - head
        if avail < 10:
            raise ValueError("no room for a numeric segment")
        ngroups = avail // 10
        digits = "0" * (3 * ngroups)
        start = len(buf) + head
        numeric_segment(buf, digits, version)
        fixed += [True] * head
        for g in range(ngroups):
            groups.append(start + 10 * g)
            fixed += [False] * 10
        # tail bits: terminator / padding, keep zero
        tail = cap - len(buf)
        buf.append(0, tail)
        fixed += [True] * tail
    else:
        term = min(4, cap - len(buf))
        buf.append(0, term)
        fixed += [True] * term
        align = -len(buf) % 8
        buf.append(0, align)
        fixed += [True] * align
        npad = (cap - len(buf)) // 8
        for i in range(npad):
            buf.append(0xEC if i % 2 == 0 else 0x11, 8)
        fixed += [free == "none"] * (8 * npad)
    assert len(buf) == cap and len(fixed) == cap
    return Message(version, ecl, free, payload, bytearray(buf.to_bytes()), fixed, groups, cap)
