from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def build(size: int = 512) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = size / 512.0
    draw.rounded_rectangle(
        [8 * s, 8 * s, 504 * s, 504 * s], radius=112 * s, fill=(20, 23, 34, 255)
    )
    draw.rounded_rectangle(
        [20 * s, 20 * s, 492 * s, 492 * s],
        radius=104 * s,
        outline=(139, 124, 255, 160),
        width=max(2, int(5 * s)),
    )
    # ears
    draw.polygon(
        [(132 * s, 196 * s), (156 * s, 82 * s), (238 * s, 158 * s)],
        fill=(139, 124, 255, 255),
    )
    draw.polygon(
        [(380 * s, 196 * s), (356 * s, 82 * s), (274 * s, 158 * s)],
        fill=(139, 124, 255, 255),
    )
    draw.polygon(
        [(158 * s, 178 * s), (172 * s, 122 * s), (214 * s, 164 * s)],
        fill=(60, 52, 120, 255),
    )
    draw.polygon(
        [(354 * s, 178 * s), (340 * s, 122 * s), (298 * s, 164 * s)],
        fill=(60, 52, 120, 255),
    )
    # head
    draw.ellipse([112 * s, 146 * s, 400 * s, 402 * s], fill=(232, 234, 242, 255))
    # eyes
    draw.ellipse([182 * s, 232 * s, 234 * s, 292 * s], fill=(139, 124, 255, 255))
    draw.ellipse([278 * s, 232 * s, 330 * s, 292 * s], fill=(139, 124, 255, 255))
    draw.ellipse([199 * s, 248 * s, 221 * s, 276 * s], fill=(20, 23, 34, 255))
    draw.ellipse([295 * s, 248 * s, 317 * s, 276 * s], fill=(20, 23, 34, 255))
    # nose + mouth
    draw.polygon(
        [(246 * s, 306 * s), (266 * s, 306 * s), (256 * s, 318 * s)],
        fill=(239, 107, 122, 255),
    )
    draw.arc([226 * s, 312 * s, 256 * s, 344 * s], 0, 90, fill=(90, 96, 114, 255), width=int(5 * s))
    draw.arc([256 * s, 312 * s, 286 * s, 344 * s], 90, 180, fill=(90, 96, 114, 255), width=int(5 * s))
    # code brackets
    draw.line(
        [(148 * s, 350 * s), (116 * s, 376 * s), (148 * s, 402 * s)],
        fill=(78, 201, 164, 255),
        width=int(10 * s),
        joint="curve",
    )
    draw.line(
        [(364 * s, 350 * s), (396 * s, 376 * s), (364 * s, 402 * s)],
        fill=(78, 201, 164, 255),
        width=int(10 * s),
        joint="curve",
    )
    draw.line([(286 * s, 344 * s), (226 * s, 408 * s)], fill=(78, 201, 164, 200), width=int(8 * s))
    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = build(512)
    image.save(ASSETS / "dma.png")
    image.save(
        ASSETS / "dma.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("icon written:", ASSETS / "dma.ico")


if __name__ == "__main__":
    main()
