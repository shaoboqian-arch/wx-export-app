# -*- coding: utf-8 -*-
"""生成应用图标（极简：圆角蓝底 + 白色向下箭头 + 底座），输出到 src/app.ico。

只依赖 Pillow（构建机系统 Python 已有）。可复跑；已存在则跳过。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "src", "app.ico")
BLUE = (37, 99, 235, 255)
WHITE = (255, 255, 255, 255)
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def draw(size):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=BLUE)
    c = size / 2
    w = size * 0.105
    d.rectangle([c - w / 2, size * 0.22, c + w / 2, size * 0.55], fill=WHITE)
    d.polygon([(c - w * 1.5, size * 0.50), (c + w * 1.5, size * 0.50),
               (c, size * 0.74)], fill=WHITE)
    d.rounded_rectangle([size * 0.25, size * 0.80, size * 0.75, size * 0.855],
                        radius=max(1, int(size * 0.025)), fill=WHITE)
    return im


def ensure(force=False):
    if os.path.isfile(OUT) and not force:
        return OUT
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    draw(256).save(OUT, format="ICO", sizes=SIZES)
    return OUT


if __name__ == "__main__":
    print(ensure(force="--force" in os.sys.argv))
