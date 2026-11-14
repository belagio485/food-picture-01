# food-picture-01 entry: a gourmet burger, drawn procedurally.
#
# The program generates one 1024x1024 PNG of a stacked gourmet burger on a
# wooden board against a warm glowing backdrop, using only the Python
# standard library.  It is fully deterministic: same run -> byte-identical
# PNG.  No network, no files read, no randomness, no wall clock.
#
#   MAKEFUN_OUTPUT_DIR=out python3 main.py   # writes out/entry.png
#   (without MAKEFUN_OUTPUT_DIR it writes /output/entry.png)

import math
import os
import struct
import zlib

W = 1024
H = 1024

# ---------------------------------------------------------------------------
# Framebuffer and tiny software rasterizer
# ---------------------------------------------------------------------------

fb = [bytearray(W * 3) for _ in range(H)]


def _clamp01(t):
    if t < 0.0:
        return 0.0
    if t > 1.0:
        return 1.0
    return t


def lerp3(c1, c2, t):
    t = _clamp01(t)
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def _blend_px(row, i, r, g, b, a):
    """Alpha-blend one pixel (i = pixel index) into row bytearray."""
    o = i * 3
    ia = 255 - a
    row[o] = (r * a + row[o] * ia) // 255
    row[o + 1] = (g * a + row[o + 1] * ia) // 255
    row[o + 2] = (b * a + row[o + 2] * ia) // 255


def span(y, x0, x1, r, g, b, a=255):
    """Fill the horizontal span [x0, x1) on row y with 1px edge AA."""
    if y < 0 or y >= H or x1 <= 0.0 or x0 >= W:
        return
    row = fb[y]
    if x0 < 0.0:
        x0 = 0.0
    if x1 > W:
        x1 = float(W)
    i0 = int(math.ceil(x0))
    i1 = int(math.floor(x1))
    if a == 255:
        if i1 > i0:
            row[i0 * 3:i1 * 3] = bytes((r, g, b)) * (i1 - i0)
        if i0 > 0 and x0 < i0:  # left edge pixel, partially covered
            _blend_px(row, i0 - 1, r, g, b, int((i0 - x0) * 255))
        if i1 < W and x1 > i1:  # right edge pixel, partially covered
            _blend_px(row, i1, r, g, b, int((x1 - i1) * 255))
    else:
        lo = int(math.floor(x0))
        hi = int(math.ceil(x1))
        if lo < 0:
            lo = 0
        if hi > W:
            hi = W
        for i in range(lo, hi):
            cov = min(x1, i + 1.0) - max(x0, float(i))
            if cov <= 0.0:
                continue
            _blend_px(row, i, r, g, b, int(a * cov))


def ellipse_vgrad(cx, cy, rx, ry, c_top, c_bot, y_top=None, y_bot=None, a=255):
    """Filled ellipse, colour a vertical gradient between c_top and c_bot."""
    if y_top is None:
        y_top = cy - ry
    if y_bot is None:
        y_bot = cy + ry
    y_start = max(0, int(math.ceil(cy - ry)))
    y_end = min(H - 1, int(math.floor(cy + ry)))
    span_y = y_bot - y_top
    for y in range(y_start, y_end + 1):
        t = (y + 0.5 - cy) / ry
        rem = 1.0 - t * t
        if rem <= 0.0:
            continue
        dx = rx * math.sqrt(rem)
        gt = (y - y_top) / span_y if span_y else 0.0
        col = lerp3(c_top, c_bot, gt)
        span(y, cx - dx, cx + dx, col[0], col[1], col[2], a)


def ellipse_solid(cx, cy, rx, ry, color, a=255):
    ellipse_vgrad(cx, cy, rx, ry, color, color, a=a)


def disc_radial(cx, cy, r, color, a_max):
    """Soft disc whose alpha falls off quadratically to 0 at the rim."""
    y_start = max(0, int(math.ceil(cy - r)))
    y_end = min(H - 1, int(math.floor(cy + r)))
    r2 = r * r
    for y in range(y_start, y_end + 1):
        dy = (y + 0.5 - cy)
        rem = r2 - dy * dy
        if rem <= 0.0:
            continue
        dx = math.sqrt(rem)
        row = fb[y]
        x_start = max(0, int(math.floor(cx - dx)))
        x_end = min(W - 1, int(math.ceil(cx + dx)))
        cr, cg, cb = color
        for x in range(x_start, x_end + 1):
            ddx = (x + 0.5 - cx)
            d2 = (ddx * ddx + dy * dy) / r2
            if d2 >= 1.0:
                continue
            f = 1.0 - d2
            _blend_px(row, x, cr, cg, cb, int(a_max * f * f))


def rrect_vgrad(x0, y0, x1, y1, rad, c_top, c_bot):
    """Filled rounded rectangle, vertical gradient."""
    y_start = max(0, int(math.ceil(y0)))
    y_end = min(H - 1, int(math.floor(y1)))
    span_y = y1 - y0
    for y in range(y_start, y_end + 1):
        yc = y + 0.5
        if yc < y0 + rad:
            t = (yc - (y0 + rad)) / rad
            dx = rad * math.sqrt(max(0.0, 1.0 - t * t))
            xa, xb = x0 + rad - dx, x1 - rad + dx
        elif yc > y1 - rad:
            t = (yc - (y1 - rad)) / rad
            dx = rad * math.sqrt(max(0.0, 1.0 - t * t))
            xa, xb = x0 + rad - dx, x1 - rad + dx
        else:
            xa, xb = x0, x1
        col = lerp3(c_top, c_bot, (y - y0) / span_y if span_y else 0.0)
        span(y, xa, xb, col[0], col[1], col[2])


def poly(pts, c_top, c_bot, y0, y1):
    """Filled polygon (scanline), vertical gradient."""
    n = len(pts)
    ys = [p[1] for p in pts]
    y_start = max(0, int(math.ceil(min(ys))))
    y_end = min(H - 1, int(math.floor(max(ys))))
    span_y = y1 - y0
    for y in range(y_start, y_end + 1):
        yc = y + 0.5
        xs = []
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]
            if (ay <= yc < by) or (by <= yc < ay):
                t = (yc - ay) / (by - ay)
                xs.append(ax + t * (bx - ax))
        xs.sort()
        col = lerp3(c_top, c_bot, (y - y0) / span_y if span_y else 0.0)
        for k in range(0, len(xs) - 1, 2):
            span(y, xs[k], xs[k + 1], col[0], col[1], col[2])


def poly_solid(pts, color):
    ys = [p[1] for p in pts]
    poly(pts, color, color, min(ys), max(ys))


def rot_rect(cx, cy, hw, hh, deg, c_top, c_bot):
    """Rotated rectangle (hw/hh are half sizes), vertical gradient."""
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        dx, dy = sx * hw, sy * hh
        pts.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
    poly(pts, c_top, c_bot, cy - hh, cy + hh)


# Deterministic pseudo-random stream (fixed seeds -> same output every run).
def lcg(seed):
    s = seed & 0x7fffffff
    while True:
        s = (s * 1103515245 + 12345) & 0x7fffffff
        yield s / 2147483648.0


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

def draw_background():
    # Warm vertical gradient: deep brown top, glowing amber middle, dark base.
    stops = [(0.0, (46, 20, 8)), (0.42, (112, 58, 18)),
             (0.68, (168, 102, 34)), (1.0, (34, 15, 7))]
    for y in range(H):
        t = y / (H - 1)
        for i in range(len(stops) - 1):
            t0, c0 = stops[i]
            t1, c1 = stops[i + 1]
            if t0 <= t <= t1:
                col = lerp3(c0, c1, (t - t0) / (t1 - t0))
                break
        else:
            col = stops[-1][1]
        span(y, 0.0, float(W), col[0], col[1], col[2])
    # Warm radial glow behind the burger.
    disc_radial(512, 470, 560, (255, 186, 92), 96)
    disc_radial(512, 470, 330, (255, 214, 140), 70)
    # Soft bokeh dots, fixed positions.
    bokeh = [
        (150, 180, 46, 26, (255, 205, 120)), (880, 150, 34, 30, (255, 220, 150)),
        (920, 420, 58, 20, (255, 190, 100)), (90, 430, 40, 24, (255, 200, 120)),
        (210, 640, 26, 28, (255, 215, 140)), (830, 660, 30, 26, (255, 205, 130)),
        (700, 120, 22, 30, (255, 225, 160)), (330, 110, 30, 26, (255, 200, 110)),
        (960, 250, 20, 28, (255, 215, 145)), (60, 260, 24, 26, (255, 195, 110)),
        (760, 880, 34, 18, (255, 200, 120)), (180, 900, 28, 20, (255, 205, 125)),
    ]
    for (x, y, r, a, c) in bokeh:
        disc_radial(x, y, r, c, a)


def draw_board():
    # Wooden board: rounded plank with a lighter top face and grain lines.
    rrect_vgrad(70, 712, 954, 928, 26, (196, 134, 72), (122, 74, 34))
    rrect_vgrad(70, 712, 954, 748, 26, (214, 152, 88), (178, 118, 60))
    # Grain: thin wavy lines across the board, kept subtle.
    rnd = lcg(777)
    for gy in (782, 812, 848, 884, 912):
        xs = [float(x) for x in range(86, 940, 28)]
        offs = [(next(rnd) - 0.5) * 5.0 for _ in xs]
        for yy, alpha in ((gy, 70), (gy + 1, 45)):
            for i in range(len(xs) - 1):
                span(int(round(yy + offs[i])), xs[i], xs[i + 1], 96, 56, 24, alpha)
    # Front edge shadow line.
    span(926, 78.0, 946.0, 70, 40, 16, 120)


def draw_shadow():
    disc_radial(512, 742, 330, (30, 14, 6), 110)
    disc_radial(512, 742, 220, (24, 11, 5), 90)


def draw_bottom_bun():
    rrect_vgrad(222, 638, 802, 726, 58, (222, 158, 66), (172, 106, 36))
    # Toasted rim along the top edge.
    ellipse_vgrad(512, 648, 288, 26, (240, 178, 84), (222, 158, 66),
                  y_top=622, y_bot=674)
    # Warm highlight on the left.
    disc_radial(330, 690, 110, (255, 205, 120), 42)


def draw_patty():
    # Hand-formed patty: rough-edged polygon with deterministic jitter.
    rnd = lcg(4242)
    top, bot, xl, xr = 588, 656, 196, 828
    pts = []
    for i in range(17):
        t = i / 16.0
        pts.append((xl + (xr - xl) * t, top + (next(rnd) - 0.5) * 10.0))
    for i in range(17):
        t = i / 16.0
        pts.append((xr - (xr - xl) * t, bot + (next(rnd) - 0.5) * 10.0))
    poly(pts, (102, 62, 30), (66, 36, 18), top, bot)
    # Char marks: dark seared streaks.
    for (x, y, rx, ry, a) in [(300, 612, 46, 9, 120), (470, 622, 60, 10, 130),
                              (650, 610, 52, 9, 120), (760, 628, 34, 8, 110),
                              (380, 636, 40, 8, 100), (560, 640, 44, 8, 110)]:
        ellipse_solid(x, y, rx, ry, (38, 20, 10), a)
    # Speckles: toasted crumbs on the patty face.
    rnd = lcg(99)
    for _ in range(46):
        x = 220 + next(rnd) * 584
        y = 596 + next(rnd) * 52
        r = 1.5 + next(rnd) * 2.5
        ellipse_solid(x, y, r, r * 0.7,
                      (140, 92, 46) if next(rnd) < 0.5 else (52, 28, 14),
                      150)


def draw_cheese():
    # Cheese slice, corners poking out past the patty, with melting drips.
    rot_rect(512, 578, 302, 34, 3.5, (255, 178, 42), (238, 138, 18))
    # Drips hanging off the lower edge: narrow stems, rounded ends.
    for (x, y, r, ln) in [(306, 606, 11, 30), (512, 612, 13, 38), (716, 606, 11, 28)]:
        rrect_vgrad(x - r, y - 10, x + r, y + ln, r, (250, 168, 34), (236, 136, 18))
        ellipse_solid(x, y + ln, r, r * 0.9, (236, 136, 18))
    # Glossy sheen on the cheese face.
    disc_radial(430, 566, 90, (255, 225, 130), 40)


def draw_tomato():
    rrect_vgrad(232, 520, 792, 586, 30, (222, 58, 36), (180, 34, 22))
    # Juicy inner flesh.
    ellipse_vgrad(512, 553, 258, 20, (238, 96, 62), (214, 62, 38),
                  y_top=533, y_bot=573, a=200)
    # Seed pockets: pale, glossy.
    for (x, y, rx, ry) in [(330, 552, 34, 11), (470, 556, 30, 10),
                           (600, 550, 32, 11), (712, 554, 26, 9)]:
        ellipse_solid(x, y, rx, ry, (246, 178, 132), 210)
        ellipse_solid(x, y, rx * 0.55, ry * 0.5, (250, 214, 172), 220)
    # Wet gloss highlight.
    disc_radial(360, 534, 80, (255, 200, 170), 55)


def draw_lettuce():
    # Ruffled lettuce: a wavy band topped by overlapping scallop leaves.
    rrect_vgrad(206, 492, 818, 528, 18, (110, 172, 66), (74, 132, 44))
    rnd = lcg(2024)
    x = 212
    while x < 816:
        r = 30 + next(rnd) * 12
        cy = 528 + next(rnd) * 8
        col = (126, 186, 74) if next(rnd) < 0.5 else (88, 152, 56)
        ellipse_vgrad(x, cy, r, r * 0.72, col, (66, 122, 40),
                      y_top=cy - r * 0.7, y_bot=cy + r * 0.7)
        x += int(r * 1.35)
    # Leaf veins: thin lighter strokes.
    for (x, y, rx, ry) in [(300, 540, 40, 5), (470, 546, 46, 5), (640, 540, 40, 5)]:
        ellipse_solid(x, y, rx, ry, (150, 205, 96), 90)


def draw_top_bun():
    cx, cy, rx, ry = 512, 542, 310, 238
    # Dome: upper half of an ellipse with a golden baked gradient.
    y_start = max(0, int(math.ceil(cy - ry)))
    y_end = min(H - 1, int(math.floor(cy)))
    for y in range(y_start, y_end + 1):
        t = (y + 0.5 - cy) / ry
        rem = 1.0 - t * t
        if rem <= 0.0:
            continue
        dx = rx * math.sqrt(rem)
        col = lerp3((238, 170, 78), (198, 122, 44), (y - (cy - ry)) / ry)
        span(y, cx - dx, cx + dx, col[0], col[1], col[2])
    # Toasted bottom rim of the dome.
    ellipse_vgrad(512, 538, 306, 16, (214, 140, 56), (188, 110, 38),
                  y_top=522, y_bot=554, a=255)
    # Baked highlight, upper left.
    disc_radial(398, 384, 150, (255, 224, 150), 66)
    disc_radial(398, 384, 80, (255, 238, 180), 55)
    # Warm blush on the lower right.
    disc_radial(660, 480, 120, (226, 140, 60), 40)
    # Sesame seeds scattered on the dome.
    seeds = [(330, 420, 11, 6), (420, 356, 12, 6), (520, 336, 11, 6),
             (620, 352, 12, 6), (700, 408, 11, 6), (370, 470, 10, 6),
             (470, 420, 11, 6), (580, 428, 11, 6), (660, 468, 10, 6),
             (300, 480, 10, 5), (740, 470, 10, 5), (430, 480, 10, 5),
             (560, 486, 10, 5), (500, 380, 11, 6)]
    for (x, y, a_, b_) in seeds:
        ellipse_solid(x, y, a_, b_, (246, 222, 164))
        ellipse_solid(x - 2, y - 2, a_ * 0.6, b_ * 0.55, (252, 236, 190), 200)
    # Small basil leaf on top, for freshness.
    ellipse_solid(600, 340, 26, 12, (52, 128, 52))
    ellipse_solid(600, 340, 26, 12, (72, 152, 66), 90)


def draw_garnish():
    # Cherry-tomato half on the board, left.
    ellipse_vgrad(252, 806, 54, 50, (232, 64, 40), (186, 36, 22),
                  y_top=756, y_bot=856)
    ellipse_solid(252, 806, 40, 37, (240, 104, 74), 220)
    ellipse_solid(232, 800, 13, 9, (250, 200, 160), 230)
    ellipse_solid(272, 812, 12, 8, (250, 200, 160), 230)
    disc_radial(232, 786, 26, (255, 210, 180), 70)
    # Parsley sprig on the board, right.
    poly_solid([(742, 838), (748, 836), (782, 786), (788, 790)], (46, 110, 44))
    for (x, y, rx, ry) in [(786, 782, 22, 10), (806, 800, 20, 9),
                           (764, 800, 18, 9), (800, 826, 18, 9),
                           (772, 824, 16, 8)]:
        ellipse_solid(x, y, rx, ry, (58, 138, 58))
        ellipse_solid(x, y, rx * 0.6, ry * 0.6, (84, 168, 78), 160)
    # Crumbs and stray sesame on the board.
    rnd = lcg(31337)
    for _ in range(18):
        x = 130 + next(rnd) * 760
        y = 770 + next(rnd) * 130
        r = 1.5 + next(rnd) * 2.5
        ellipse_solid(x, y, r, r * 0.7, (120, 76, 36), 170)
    for (x, y) in [(170, 780), (880, 770), (620, 870), (360, 880), (850, 850)]:
        ellipse_solid(x, y, 8, 4.5, (246, 222, 164))


def draw_vignette():
    # Darken the corners so the eye stays on the burger.
    cx, cy = 512.0, 512.0
    for y in range(H):
        dy = (y + 0.5 - cy) / 760.0
        row = fb[y]
        dy2 = dy * dy
        for x in range(W):
            dx = (x + 0.5 - cx) / 760.0
            d2 = dx * dx + dy2
            if d2 <= 0.42:
                continue
            f = (d2 - 0.42) / 0.58
            dark = int(88 * f * f)
            if dark <= 0:
                continue
            o = x * 3
            ia = 255 - dark
            row[o] = (row[o] * ia) // 255
            row[o + 1] = (row[o + 1] * ia) // 255
            row[o + 2] = (row[o + 2] * ia) // 255


# ---------------------------------------------------------------------------
# PNG encoding (deterministic: fixed filter + zlib level)
# ---------------------------------------------------------------------------

def encode_png():
    raw = bytearray()
    for y in range(H):
        row = fb[y]
        raw.append(1)  # Sub filter: each byte minus the byte 3 places back
        for i in range(W * 3):
            left = row[i - 3] if i >= 3 else 0
            raw.append((row[i] - left) & 0xFF)
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


def main():
    draw_background()
    draw_board()
    draw_shadow()
    draw_bottom_bun()
    draw_patty()
    draw_cheese()
    draw_tomato()
    draw_lettuce()
    draw_top_bun()
    draw_garnish()
    draw_vignette()
    png = encode_png()
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "entry.png"), "wb") as f:
        f.write(png)


if __name__ == "__main__":
    main()
