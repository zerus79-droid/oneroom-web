"""Build a crisp multi-resolution ICO (16..256) for desktop shortcuts."""
from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

OUT_ICO = Path(r"C:\Users\someb\oneroom-web\static\app.ico")
OUT_PNG = Path(r"C:\Users\someb\oneroom-web\static\app.png")
SIZES = [16, 24, 32, 48, 64, 128, 256]


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    m = max(1, s // 16)

    # blue rounded tile
    d.rounded_rectangle(
        [m, m, s - m - 1, s - m - 1],
        radius=max(2, s // 6),
        fill=(37, 99, 235, 255),
    )

    left, right = int(s * 0.22), int(s * 0.78)
    top, bottom = int(s * 0.30), int(s * 0.82)
    d.rectangle([left, top, right, bottom], fill=(255, 255, 255, 255))

    # roof
    d.polygon(
        [
            (int(s * 0.14), top + 1),
            (s // 2, int(s * 0.16)),
            (int(s * 0.86), top + 1),
        ],
        fill=(253, 224, 71, 255),
    )

    # windows
    gap = max(1, s // 28)
    win_w = max(2, (right - left - gap * 5) // 2)
    win_h = max(2, (bottom - top - gap * 6) // 2)
    wx0 = left + gap * 2
    wy0 = top + gap * 2
    for row in range(2):
        for col in range(2):
            x1 = wx0 + col * (win_w + gap * 2)
            y1 = wy0 + row * (win_h + gap * 2)
            d.rectangle([x1, y1, x1 + win_w, y1 + win_h], fill=(147, 197, 253, 255))

    # door
    door_w = max(2, int(s * 0.16))
    door_h = max(3, int(s * 0.20))
    dx = s // 2 - door_w // 2
    d.rectangle([dx, bottom - door_h, dx + door_w, bottom], fill=(30, 64, 175, 255))

    return img


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def write_ico(path: Path, images: list[Image.Image]) -> None:
    """ICO container with PNG payloads (sharp on modern Windows)."""
    pngs = [to_png_bytes(im) for im in images]
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    entries = []
    offset = 6 + 16 * count
    body = b""
    for im, blob in zip(images, pngs):
        s = im.size[0]
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        entries.append(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
        body += blob
    path.write_bytes(header + b"".join(entries) + body)


def main() -> None:
    images = [make_icon(s) for s in SIZES]
    write_ico(OUT_ICO, images)
    make_icon(256).save(OUT_PNG, format="PNG")
    print("ico", OUT_ICO, OUT_ICO.stat().st_size)
    print("png", OUT_PNG, OUT_PNG.stat().st_size)


if __name__ == "__main__":
    main()
