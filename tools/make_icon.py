from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
BASE_ICON = ROOT / "tools" / "dkl_base.ico"


def load_base(size: int = 512) -> Image.Image:
    if not BASE_ICON.is_file():
        raise SystemExit(f"missing base icon: {BASE_ICON}")
    image = Image.open(BASE_ICON).convert("RGBA")
    if image.size != (size, size):
        image = image.resize((size, size), Image.Resampling.NEAREST)
    return image


def draw_pixel_hammer(image: Image.Image, origin: tuple[int, int]) -> None:
    """在启动器图标的右下角叠一把纯黑像素锤子。"""
    scale = max(1, round(image.width * 7 / 512))
    sprite = Image.new("RGBA", (24 * scale, 24 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)

    def block(x: int, y: int, width: int = 1, height: int = 1) -> None:
        draw.rectangle(
            [x * scale, y * scale, (x + width) * scale - 1, (y + height) * scale - 1],
            fill=(0, 0, 0, 255),
        )

    # 锤头：阶梯边缘，左侧留出羊角锤的凹口。
    block(7, 3, 12, 1)
    block(5, 4, 14, 1)
    block(4, 5, 16, 3)
    block(3, 8, 17, 2)
    block(4, 10, 15, 1)
    block(6, 11, 12, 1)
    for y, x1, x2 in ((8, 4, 8), (9, 5, 9), (10, 6, 10), (11, 7, 11)):
        draw.rectangle(
            [x1 * scale, y * scale, (x2 + 1) * scale - 1, (y + 1) * scale - 1],
            fill=(0, 0, 0, 0),
        )

    # 锤柄和底部收口。
    block(10, 10, 4, 1)
    block(10, 11, 4, 10)
    block(9, 21, 6, 1)
    block(10, 22, 4, 1)

    image.alpha_composite(sprite, origin)


def build(size: int = 512) -> Image.Image:
    image = load_base(size)
    scale = size / 512.0
    origin = (int(280 * scale), int(300 * scale))
    draw_pixel_hammer(image, origin)
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
