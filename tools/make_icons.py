#!/usr/bin/env python3
"""Ícones e imagem de compartilhamento do datahidro — reproduzível (Pillow).

  python3 tools/make_icons.py
  → icon.svg, icon-192.png, icon-512.png, icon-512-maskable.png,
    apple-touch-icon.png, favicon.ico, og.png

Motivo: o anel da paleta cmocean.phase (cíclica, emenda sem costura — a do
relevo do Câmera Topográfica), o mesmo do selo "eu participei", com um ✓.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PALETTE = [
    (168, 120, 13), (190, 104, 40), (207, 86, 67), (219, 64, 102),
    (223, 42, 147), (213, 41, 196), (192, 65, 229), (162, 92, 243),
    (125, 115, 240), (82, 133, 220), (44, 144, 188), (25, 149, 156),
    (12, 152, 124), (36, 154, 82), (94, 148, 32), (139, 134, 13),
]
PAGE = (247, 245, 239)
INK = (18, 18, 16)
MUTED = (111, 109, 102)


def color_at(t: float) -> tuple[int, int, int]:
    """t ∈ [0,1) → cor interpolada no ciclo (a última âncora volta na primeira)."""
    f = (t % 1.0) * len(PALETTE)
    k = int(f)
    a, b = PALETTE[k % len(PALETTE)], PALETTE[(k + 1) % len(PALETTE)]
    return tuple(round(a[i] + (b[i] - a[i]) * (f - k)) for i in range(3))


def ring(diameter: int, thickness: float) -> Image.Image:
    """RGBA diameter×diameter: gradiente cônico (0 = topo) mascarado num anel
    com borda suavizada (máscara desenhada 4× maior e reduzida)."""
    gradient = Image.new("RGB", (diameter, diameter))
    px = gradient.load()
    c = diameter / 2
    for y in range(diameter):
        for x in range(diameter):
            px[x, y] = color_at((math.atan2(y + 0.5 - c, x + 0.5 - c) + math.pi / 2) / (2 * math.pi))
    ss = 4
    mask = Image.new("L", (diameter * ss, diameter * ss), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, 0, diameter * ss - 1, diameter * ss - 1), fill=255)
    e = thickness * ss
    d.ellipse((e, e, diameter * ss - 1 - e, diameter * ss - 1 - e), fill=0)
    out = gradient.convert("RGBA")
    out.putalpha(mask.resize((diameter, diameter), Image.LANCZOS))
    return out


def check(side: int, stroke: float, color=INK) -> Image.Image:
    ss = 4
    im = Image.new("L", (side * ss, side * ss), 0)
    points = [(0.30, 0.52), (0.44, 0.66), (0.71, 0.37)]
    w = int(stroke * ss)
    draw = ImageDraw.Draw(im)
    draw.line([(x * side * ss, y * side * ss) for x, y in points], fill=255, width=w, joint="curve")
    r = w / 2
    for x, y in (points[0], points[-1]):  # pontas arredondadas
        cx, cy = x * side * ss, y * side * ss
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    out = Image.new("RGBA", (side, side), color + (0,))
    out.putalpha(im.resize((side, side), Image.LANCZOS))
    return out


def icon(side: int, full_bleed: bool, scale: float = 1.0) -> Image.Image:
    im = Image.new("RGBA", (side, side), PAGE + (255,) if full_bleed else (0, 0, 0, 0))
    d = round(side * scale)
    off = (side - d) // 2
    if not full_bleed:
        disc = Image.new("L", (d * 4, d * 4), 0)
        ImageDraw.Draw(disc).ellipse((0, 0, d * 4 - 1, d * 4 - 1), fill=255)
        base = Image.new("RGBA", (d, d), PAGE + (255,))
        base.putalpha(disc.resize((d, d), Image.LANCZOS))
        im.alpha_composite(base, (off, off))
    im.alpha_composite(ring(d, d * 0.16), (off, off))
    im.alpha_composite(check(d, d * 0.085), (off, off))
    return im


def svg() -> str:
    n, r, w = 72, 41, 16
    arcs = []
    for i in range(n):
        a0 = 2 * math.pi * i / n - math.pi / 2
        a1 = 2 * math.pi * (i + 1.35) / n - math.pi / 2  # sobreposição evita frestas
        x0, y0 = 50 + r * math.cos(a0), 50 + r * math.sin(a0)
        x1, y1 = 50 + r * math.cos(a1), 50 + r * math.sin(a1)
        hex_color = "#%02x%02x%02x" % color_at((i + 0.5) / n)
        arcs.append(f'<path d="M{x0:.2f} {y0:.2f}A{r} {r} 0 0 1 {x1:.2f} {y1:.2f}" stroke="{hex_color}"/>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<circle cx="50" cy="50" r="49" fill="#f7f5ef"/>'
        f'<g fill="none" stroke-width="{w}">{"".join(arcs)}</g>'
        '<path d="M30 52 44 66 71 37" fill="none" stroke="#121210" stroke-width="8.5" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>\n'
    )


def font(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(ROOT / "lib" / "fonts" / f"ibm-plex-mono-{weight}.woff2"), size)
    except OSError:  # FreeType sem suporte a WOFF2
        return ImageFont.load_default(size)


def share_image() -> Image.Image:
    W, H = 1200, 630
    im = Image.new("RGBA", (W, H), PAGE + (255,))
    im.alpha_composite(icon(440, full_bleed=False), (70, 95))
    d = ImageDraw.Draw(im)
    x = 580
    d.text((x, 150), "datahidro", font=font(92), fill=INK)
    d.text((x, 252), "2026", font=font(92), fill=MUTED)
    y = 390
    for line in ("Levantamento Pedal Hidrográfico", "de candidatas às eleições de", "2026 em São Paulo"):
        d.text((x, y), line, font=font(30, 600), fill=INK)
        y += 42
    d.text((x, y + 26), "pesquisa.pedalhidrografi.co", font=font(26, 400), fill=MUTED)
    return im.convert("RGB")


def main() -> None:
    (ROOT / "icon.svg").write_text(svg(), encoding="utf-8")
    icon(192, full_bleed=False).save(ROOT / "icon-192.png", optimize=True)
    icon(512, full_bleed=False).save(ROOT / "icon-512.png", optimize=True)
    icon(512, full_bleed=True, scale=0.72).save(ROOT / "icon-512-maskable.png", optimize=True)
    icon(180, full_bleed=True, scale=0.86).convert("RGB").save(ROOT / "apple-touch-icon.png", optimize=True)
    icon(256, full_bleed=False).save(ROOT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    share_image().save(ROOT / "og.png", optimize=True)
    print("ok: icon.svg, icon-192/512(+maskable).png, apple-touch-icon.png, favicon.ico, og.png")


if __name__ == "__main__":
    main()
