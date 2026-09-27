import json
import struct

from cap import imaging


def _jpeg(w, h):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xda\x00\x02" + b"\x00" * 10 + b"\xff\xd9"


def _png(w, h):
    return imaging.PNG_SIG + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0) + b"\x00" * 4


def test_sizes_and_classes():
    assert imaging.image_size(_jpeg(1024, 1024)) == (1024, 1024)
    assert imaging.image_size(_png(2048, 1152)) == (2048, 1152)
    assert imaging.image_size(b"nope") is None
    assert [imaging.resolution_class(*s) for s in [(1024, 1024), (2048, 2048), (4096, 2304)]] == ["1K", "2K", "4K"]


def test_jpeg_payload_roundtrip():
    src = _jpeg(1024, 1024)
    out = imaging.embed_payload(src, "image/jpeg", "gcc_scoring_payload", json.dumps({"composite_score": 0.9}))
    assert imaging.image_size(out) == (1024, 1024)                         # still a valid header chain
    assert out.index(b"\xff\xfe") > out.index(b"JFIF")                      # after APP0
    assert json.loads(imaging.read_jpeg_comment(out, "gcc_scoring_payload"))["composite_score"] == 0.9
    assert imaging.embed_payload(b"xx", "image/webp", "k", "v") == b"xx"
