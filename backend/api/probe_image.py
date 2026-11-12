import base64
import struct
import zlib

QUESTION = "这张图片整体是什么颜色？只回答颜色名。"
EXPECTED_HINT = "红"


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def red_square_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(solid_png(64, 64, (220, 30, 30))).decode()
