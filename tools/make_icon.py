"""生成应用图标 assets/downit.ico（无需外部素材）。"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
OUT = Path(__file__).resolve().parent.parent / "assets" / "downit.ico"


def make() -> Image.Image:
    # 渐变底
    gradient = Image.new("RGBA", (SIZE, SIZE))
    draw = ImageDraw.Draw(gradient)
    start = (0x45, 0xB4, 0x6A)
    end = (0x1C, 0x66, 0x3A)
    for y in range(SIZE):
        ratio = y / (SIZE - 1)
        color = tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3))
        draw.line([(0, y), (SIZE, y)], fill=(*color, 255))

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, SIZE - 1, SIZE - 1], radius=int(SIZE * 0.23), fill=255
    )
    canvas.paste(gradient, (0, 0), mask)

    pen = ImageDraw.Draw(canvas)
    # 下载箭头
    half = SIZE * 0.075
    pen.rounded_rectangle(
        [SIZE / 2 - half, SIZE * 0.22, SIZE / 2 + half, SIZE * 0.50],
        radius=half * 0.6,
        fill=(255, 255, 255, 255),
    )
    pen.polygon(
        [(SIZE * 0.30, SIZE * 0.47), (SIZE * 0.70, SIZE * 0.47), (SIZE * 0.5, SIZE * 0.73)],
        fill=(255, 255, 255, 255),
    )
    # 承托线
    pen.rounded_rectangle(
        [SIZE * 0.24, SIZE * 0.80, SIZE * 0.76, SIZE * 0.86],
        radius=SIZE * 0.03,
        fill=(255, 255, 255, 235),
    )
    return canvas


def main() -> None:
    icon = make()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    icon.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
