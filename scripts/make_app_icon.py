#!/usr/bin/env python3
"""Rasterise the FanCtl launcher icon from the OLED cat geometry.

The shapes below are the same primitives as drawPet() in
firmware/src/display.cpp (Idle mood, no blink, gaze 0), expressed in the
sprite's units, so the icon and the panel cat stay in sync:

    head    rounded box x[-12, 13] y[-9, 11] r5
    ears    (-11,-8)(-12,-17)(-3,-9) and mirror
    whisker (-13,1)-(-20,-1), (-13,4)-(-20,5) and mirror
    eyes    discs (-5,-2) r2, (5,-2) r2          (punched out)
    mouth   (-2,3)-(0,5), (0,5)-(2,3)            (punched out)

Writes the Expo assets and the Android mipmaps. Monochrome only: the glyph is
white on transparency (or on the ink background for the legacy icon), which is
also what the Android 13 themed-icon layer needs.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "app" / "assets"
RES = ROOT / "app" / "android" / "app" / "src" / "main" / "res"

INK = (6, 8, 13, 255)
WHITE = (255, 255, 255, 255)
SS = 4  # supersampling for smooth edges

# Sprite-space bounding box of the drawing (whisker tips included).
X0, X1 = -20.0, 20.0
Y0, Y1 = -17.0, 11.0
MID_Y = (Y0 + Y1) / 2  # centres the glyph vertically


def cat_mask(width: int, height: int, unit: float) -> Image.Image:
    """White-on-transparent glyph, `unit` pixels per sprite unit."""
    img = Image.new("L", (width * SS, height * SS), 0)
    d = ImageDraw.Draw(img)
    u = unit * SS
    cx, cy = width * SS / 2, height * SS / 2

    def p(x: float, y: float) -> tuple[float, float]:
        return cx + x * u, cy + (y - MID_Y) * u

    def box(x: float, y: float, w: float, h: float) -> list[float]:
        return [*p(x, y), *p(x + w, y + h)]

    stroke = max(1, round(u))

    # Ears sit behind the head's silhouette but draw first; same fill.
    d.polygon([p(-11, -8), p(-12, -17), p(-3, -9)], fill=255)
    d.polygon([p(11, -8), p(12, -17), p(3, -9)], fill=255)
    d.rounded_rectangle(box(-12, -9, 25, 20), radius=5 * u, fill=255)

    for ax, ay, bx, by in (
        (-13, 1, -20, -1),
        (-13, 4, -20, 5),
        (13, 1, 20, -1),
        (13, 4, 20, 5),
    ):
        d.line([p(ax, ay), p(bx, by)], fill=255, width=stroke)

    # Features punched out of the fill, matching the Idle mood.
    for ex in (-5, 5):
        ex0, ey0 = p(ex - 2, -4)
        ex1, ey1 = p(ex + 2, 0)
        d.ellipse([ex0, ey0, ex1, ey1], fill=0)
    d.line([p(-2, 3), p(0, 5), p(2, 3)], fill=0, width=stroke, joint="curve")

    img = img.resize((width, height), Image.LANCZOS)
    glyph = Image.new("RGBA", (width, height), WHITE)
    glyph.putalpha(img)
    return glyph


def glyph_for(size: int, fill_fraction: float) -> Image.Image:
    """Glyph scaled so the drawing spans `fill_fraction` of the canvas."""
    unit = size * fill_fraction / (X1 - X0)
    return cat_mask(size, size, unit)


def flat_icon(size: int) -> Image.Image:
    base = Image.new("RGBA", (size, size), INK)
    base.alpha_composite(glyph_for(size, 0.86))
    return base


def round_icon(size: int) -> Image.Image:
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    circle = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(circle).ellipse([0, 0, size * SS - 1, size * SS - 1], fill=255)
    circle = circle.resize((size, size), Image.LANCZOS)

    disc = Image.new("RGBA", (size, size), INK)
    disc.putalpha(circle)
    disc.alpha_composite(glyph_for(size, 0.74))
    return disc


def save_webp(image: Image.Image, path: Path) -> None:
    image.save(path, format="WEBP", lossless=True, method=6)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)

    flat_icon(1024).save(ASSETS / "icon.png")
    glyph_for(1024, 0.60).save(ASSETS / "adaptive-icon.png")
    glyph_for(1024, 0.60).save(ASSETS / "monochrome-icon.png")

    densities = {
        "mdpi": 48,
        "hdpi": 72,
        "xhdpi": 96,
        "xxhdpi": 144,
        "xxxhdpi": 192,
    }
    foreground = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}

    for name, size in densities.items():
        folder = RES / f"mipmap-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        save_webp(flat_icon(size), folder / "ic_launcher.webp")
        save_webp(round_icon(size), folder / "ic_launcher_round.webp")

        fg = foreground[name]
        save_webp(glyph_for(fg, 0.60), folder / "ic_launcher_foreground.webp")
        save_webp(glyph_for(fg, 0.60), folder / "ic_launcher_monochrome.webp")

    print("icon: cat glyph written to app/assets and mipmap-* (monochrome)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
