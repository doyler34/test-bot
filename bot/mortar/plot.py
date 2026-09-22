"""A north-up plot of the gun, the target and the line between them.

Not a terrain map - the game gives us no imagery - but it shows the grid
squares, the bearing and the distance, which is what a gunner checks before
he drops one down the tube.
"""
from functools import lru_cache
from io import BytesIO
import math

from PIL import Image, ImageDraw, ImageFont

from bot.ranks.rank_card import ASSETS as RANK_ASSETS

SIZE = 720
MARGIN = 72
BACK, GRID, INK, MUTED = '#101513', '#1e2a24', '#e9eee5', '#9a927f'
GUN, TARGET = '#6f9ad3', '#D9A441'


@lru_cache(maxsize=8)
def font(size, display=False):
    try:
        return ImageFont.truetype(str(RANK_ASSETS / 'fonts' / ('Display.ttf' if display else 'Body.ttf')), size)
    except OSError:
        return ImageFont.load_default(size=size)


def frame(gun, target):
    """Metres-per-pixel and the world point at the middle of the picture, with
    both markers comfortably inside the margins however far apart they are."""
    span = max(abs(target[0] - gun[0]), abs(target[1] - gun[1]), 100) * 1.35
    centre = ((gun[0] + target[0]) / 2, (gun[1] + target[1]) / 2)
    return span / (SIZE - 2 * MARGIN), centre


def project(point, scale, centre):
    return (SIZE / 2 + (point[0] - centre[0]) / scale,
            SIZE / 2 - (point[1] - centre[1]) / scale)


def grid_lines(draw, scale, centre):
    """Grid squares at whatever spacing keeps the picture readable."""
    metres = next((m for m in (50, 100, 250, 500, 1000, 2000) if m / scale >= 90), 5000)
    left, bottom = centre[0] - scale * SIZE / 2, centre[1] - scale * SIZE / 2
    first = math.ceil(left / metres) * metres
    while first < left + scale * SIZE:
        x, _ = project((first, 0), scale, centre)
        draw.line([(x, 0), (x, SIZE)], fill=GRID)
        draw.text((x + 4, SIZE - 20), f'{first:.0f}', font=font(13), fill=MUTED)
        first += metres
    first = math.ceil(bottom / metres) * metres
    while first < bottom + scale * SIZE:
        _, y = project((0, first), scale, centre)
        draw.line([(0, y), (SIZE, y)], fill=GRID)
        draw.text((6, y - 18), f'{first:.0f}', font=font(13), fill=MUTED)
        first += metres
    return metres


def marker(draw, point, colour, label):
    x, y = point
    draw.ellipse([x - 9, y - 9, x + 9, y + 9], outline=colour, width=3)
    draw.line([(x - 14, y), (x + 14, y)], fill=colour, width=1)
    draw.line([(x, y - 14), (x, y + 14)], fill=colour, width=1)
    draw.text((x + 18, y - 9), label, font=font(17, True), fill=colour)


def north(draw):
    x, y = SIZE - 44, 44
    draw.line([(x, y + 22), (x, y - 20)], fill=MUTED, width=2)
    draw.polygon([(x, y - 28), (x - 7, y - 14), (x + 7, y - 14)], fill=MUTED)
    draw.text((x - 5, y + 26), 'N', font=font(15, True), fill=MUTED)


def render(gun, target, mils, distance, tube):
    """The plot as PNG bytes, ready to attach."""
    image = Image.new('RGB', (SIZE, SIZE), BACK)
    draw = ImageDraw.Draw(image)
    scale, centre = frame(gun, target)
    step = grid_lines(draw, scale, centre)
    start, end = project(gun, scale, centre), project(target, scale, centre)
    draw.line([start, end], fill=TARGET, width=2)
    marker(draw, start, GUN, 'GUN')
    marker(draw, end, TARGET, 'TGT')
    north(draw)
    # The header sits over the top row of grid labels; clear it first.
    draw.rectangle([0, 0, SIZE, 74], fill=BACK)
    draw.text((MARGIN / 3, 18), tube, font=font(20, True), fill=INK)
    draw.text((MARGIN / 3, 46), f'{mils:.0f} mils   {mils * 360 / 6400:.1f}°   {distance:.0f} m',
              font=font(20), fill=TARGET)
    draw.text((MARGIN / 3, SIZE - 42), f'Grid squares {step:.0f} m', font=font(14), fill=MUTED)
    buffer = BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()
