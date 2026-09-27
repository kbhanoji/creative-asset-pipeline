"""Small image helpers with no imaging-library dependency: real pixel size, resolution
class, and embedding the scoring payload into PNG (iTXt) or JPEG (COM segment) files."""
from __future__ import annotations

import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) for PNG, JPEG or WebP; None if the format isn't recognised."""
    if data.startswith(PNG_SIG) and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:   # standalone markers
                i += 2
                continue
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):  # SOFn
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + seg_len
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = data[12:16]
        if kind == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if kind == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if kind == b"VP8L":
            b = int.from_bytes(data[21:25], "little")
            return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
    return None


def resolution_class(width: int, height: int) -> str:
    """1K / 2K / 4K by the longest side (Gemini image sizes: 1K≈1024, 2K≈2048, 4K≈4096)."""
    side = max(width, height)
    return "1K" if side < 1536 else "2K" if side < 3072 else "4K"


def embed_png_text(png: bytes, key: str, text: str) -> bytes:
    """Insert an iTXt chunk after IHDR."""
    if not png.startswith(PNG_SIG):
        return png
    ihdr_len = struct.unpack(">I", png[8:12])[0]
    cut = 8 + 12 + ihdr_len
    body = key.encode("latin-1") + b"\x00\x00\x00\x00\x00" + text.encode("utf-8")
    chunk = struct.pack(">I", len(body)) + b"iTXt" + body + struct.pack(">I", zlib.crc32(b"iTXt" + body) & 0xFFFFFFFF)
    return png[:cut] + chunk + png[cut:]


def embed_jpeg_comment(jpeg: bytes, key: str, text: str) -> bytes:
    """Insert a COM (0xFFFE) segment '<key>=<text>' right after SOI (and after APP0/APP1 if present)."""
    if jpeg[:2] != b"\xff\xd8":
        return jpeg
    payload = f"{key}={text}".encode("utf-8")[:65533]
    segment = b"\xff\xfe" + struct.pack(">H", len(payload) + 2) + payload
    i = 2
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF and jpeg[i + 1] in (0xE0, 0xE1):   # keep JFIF/EXIF first
        i += 2 + struct.unpack(">H", jpeg[i + 2:i + 4])[0]
    return jpeg[:i] + segment + jpeg[i:]


def read_jpeg_comment(jpeg: bytes, key: str) -> str | None:
    i = 2
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF:
        marker, seg_len = jpeg[i + 1], struct.unpack(">H", jpeg[i + 2:i + 4])[0]
        if marker == 0xFE:
            body = jpeg[i + 4:i + 2 + seg_len].decode("utf-8", "replace")
            if body.startswith(key + "="):
                return body[len(key) + 1:]
        if marker == 0xDA:   # start of scan: no more headers
            break
        i += 2 + seg_len
    return None


def embed_payload(data: bytes, mime_type: str, key: str, text: str) -> bytes:
    if mime_type == "image/png":
        return embed_png_text(data, key, text)
    if mime_type == "image/jpeg":
        return embed_jpeg_comment(data, key, text)
    return data
