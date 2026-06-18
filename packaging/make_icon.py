"""Generate the Burnmeter app icon — a gradient flame on a dark rounded tile.

Renders at 4x and downsamples (LANCZOS) for clean antialiasing, then writes a
multi-size .ico (16-256) plus a 256 PNG into burnmeter/assets/. Used for the
PyInstaller exe icon, the pywebview window icon, and the tray icon.

Run:  python packaging/make_icon.py
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

S = 1024                      # supersample canvas (4x of 256)
OUT = Path(__file__).resolve().parent.parent / "burnmeter" / "assets"


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def _vgrad(size, stops):
    """Vertical gradient image from (pos, rgb) stops (pos 0=top..1=bottom)."""
    w, h = size
    g = Image.new("RGB", (w, h))
    px = g.load()
    stops = sorted(stops)
    for y in range(h):
        t = y / (h - 1)
        # find bracketing stops
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                tt = 0 if p1 == p0 else (t - p0) / (p1 - p0)
                col = _lerp(c0, c1, tt)
                break
        else:
            col = stops[-1][1]
        for x in range(w):
            px[x, y] = col
    return g


def _flame_mask(size, pts_norm, blur=0):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    pts = [(x * size, y * size) for x, y in pts_norm]
    d.polygon(pts, fill=255)
    if blur:
        m = m.filter(ImageFilter.GaussianBlur(blur))
    return m


# Outer flame silhouette — clean symmetric teardrop, pointed top, rounded bulb.
OUTER = [
    (0.50, 0.045),                                  # tip
    (0.575, 0.20), (0.655, 0.39), (0.745, 0.57),
    (0.785, 0.70), (0.745, 0.835), (0.635, 0.925),
    (0.50, 0.96),                                   # bottom
    (0.365, 0.925), (0.255, 0.835), (0.215, 0.70),
    (0.255, 0.57), (0.345, 0.39), (0.425, 0.20),
]
# Inner flame — smaller, shifted up, gives depth.
INNER = [
    (0.50, 0.355),
    (0.595, 0.55), (0.625, 0.70), (0.555, 0.835),
    (0.50, 0.88),
    (0.445, 0.835), (0.375, 0.70), (0.405, 0.55),
]


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded dark tile with a vertical sheen.
    tile = _vgrad((S, S), [(0.0, (0x22, 0x25, 0x2e)), (0.5, (0x16, 0x18, 0x1f)),
                           (1.0, (0x0d, 0x0e, 0x13))]).convert("RGBA")
    tile_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle(
        [40, 40, S - 40, S - 40], radius=210, fill=255)
    img.paste(tile, (0, 0), tile_mask)
    # subtle top inner highlight
    hi = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(hi).rounded_rectangle([40, 40, S - 40, S - 40], radius=210,
                                         outline=(255, 255, 255, 26), width=6)
    img.alpha_composite(hi)

    # Outer-flame glow (soft).
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gm = _flame_mask(S, OUTER, blur=34)
    gcol = Image.new("RGBA", (S, S), (255, 110, 20, 150))
    glow.paste(gcol, (0, 0), gm)
    img.alpha_composite(glow)

    # Outer flame: orange→amber gradient masked by the flame shape.
    grad = _vgrad((S, S), [(0.05, (255, 232, 120)), (0.30, (255, 178, 32)),
                           (0.66, (255, 90, 20)), (1.0, (226, 54, 18))]).convert("RGBA")
    img.paste(grad, (0, 0), _flame_mask(S, OUTER, blur=2))

    # Inner flame: brighter core.
    igrad = _vgrad((S, S), [(0.3, (255, 248, 210)), (0.7, (255, 214, 110)),
                            (1.0, (255, 170, 60))]).convert("RGBA")
    img.paste(igrad, (0, 0), _flame_mask(S, INNER, blur=2))

    return img.resize((256, 256), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    icon = build()
    png = OUT / "burnmeter.png"
    ico = OUT / "burnmeter.ico"
    icon.save(png)
    icon.save(ico, format="ICO",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                     (128, 128), (256, 256)])
    print(f"wrote {png}")
    print(f"wrote {ico}")


if __name__ == "__main__":
    main()
