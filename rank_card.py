"""Offline, cached vector template with in-memory PNG composition."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import unicodedata

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
import resvg_py
from rank_rules import rank_for_xp

ASSETS = Path(__file__).resolve().parent / 'assets/rank-card'
WIDTH, HEIGHT = 960, 320
INK, MUTED, ACCENT = '#e9eee5', '#8b9c8d', '#c1d49f'


@lru_cache(maxsize=64)
def font(size, display=False):
    try:
        return ImageFont.truetype(str(ASSETS / 'fonts' / ('Display.ttf' if display else 'Body.ttf')), size)
    except OSError:
        return ImageFont.load_default(size=size)


@lru_cache(maxsize=16)
def vector(path, width, height):
    data = resvg_py.svg_to_bytes(svg_path=str(path), width=width, height=height, skip_system_fonts=True)
    with Image.open(BytesIO(data)) as image:
        return image.convert('RGBA')


@lru_cache(maxsize=1)
def template():
    image = Image.new('RGBA', (WIDTH, HEIGHT), '#101513')
    for name in ('background.svg', 'frame.svg', 'overlay.svg'):
        try:
            image.alpha_composite(vector(ASSETS / name, WIDTH, HEIGHT))
        except (OSError, ValueError):
            pass
    return image


def clean_name(value):
    value = unicodedata.normalize('NFC', str(value))
    return ''.join(c for c in value if not unicodedata.category(c).startswith('C'))[:256].strip() or 'OYB PLAYER'


def fitted(draw, value, xy, width, size, minimum, fill=INK, display=False, audit=None):
    value = clean_name(value)
    face = font(size, display)
    while draw.textlength(value, font=face) > width and size > minimum:
        size -= 1
        face = font(size, display)
    if draw.textlength(value, font=face) > width:
        while value and draw.textlength(value + '…', font=face) > width:
            value = value[:-1]
        value += '…'
    draw.text(xy, value, font=face, fill=fill, anchor='lt')
    if audit is not None:
        audit.append((value, draw.textbbox(xy, value, font=face, anchor='lt'), (xy[0], xy[1], xy[0]+width, xy[1]+size+8)))


def render_card(name, xp, avatar=None, *, audit=None):
    progress = rank_for_xp(xp)
    image = template().copy()
    draw = ImageDraw.Draw(image)
    portrait = None
    if avatar and len(avatar) <= 4 * 1024 * 1024:
        try:
            with Image.open(BytesIO(avatar)) as original:
                if original.width * original.height <= 16_000_000:
                    original.seek(0)  # Static first frame for GIF avatars.
                    portrait = ImageOps.fit(original.convert('RGBA'), (120, 120), method=Image.Resampling.LANCZOS)
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            pass
    if portrait is None:
        portrait = Image.new('RGBA', (120, 120), '#27372b')
        pd = ImageDraw.Draw(portrait)
        pd.ellipse((40, 21, 80, 61), fill='#b3c09e')
        pd.rounded_rectangle((20, 69, 100, 123), radius=30, fill='#819575')
    mask = Image.new('L', (120, 120))
    ImageDraw.Draw(mask).ellipse((0, 0, 119, 119), fill=255)
    image.paste(portrait, (57, 82), mask)
    try:
        image.alpha_composite(vector(ASSETS / 'ranks' / (progress.current.slug + '.svg'), 100, 100), (790, 77))
    except (OSError, ValueError):
        draw.polygon([(840, 97), (865, 127), (840, 157), (815, 127)], outline=ACCENT, width=3)
    fitted(draw, 'O.Y.B', (30, 16), 90, 28, 28, display=True, audit=audit)
    fitted(draw, 'COMMUNITY RANK', (148, 20), 300, 13, 13, fill=MUTED, audit=audit)
    fitted(draw, 'SERVICE RECORD', (774, 20), 162, 12, 12, fill=MUTED, audit=audit)
    fitted(draw, name, (244, 65), 460, 23, 15, audit=audit)
    fitted(draw, progress.current.name.upper(), (241, 98), 466, 61, 44, fill=ACCENT, display=True, audit=audit)
    fitted(draw, 'VERIFIED PLAYER', (46, 238), 150, 13, 12, fill=MUTED, audit=audit)
    fitted(draw, 'O.Y.B  /  REFORGER', (45, 268), 150, 12, 11, fill=MUTED, audit=audit)
    fitted(draw, 'FIELD INSIGNIA', (779, 219), 134, 13, 12, fill=MUTED, audit=audit)
    fitted(draw, 'O.Y.B', (806, 246), 100, 35, 35, fill=ACCENT, display=True, audit=audit)
    xp_text = f'{progress.xp:,} / {progress.next.threshold:,} XP' if progress.next else f'{progress.xp:,} XP'
    fitted(draw, xp_text, (244, 188), 460, 27, 16, display=True, audit=audit)
    draw.rounded_rectangle((244, 228, 704, 239), radius=2, fill='#354137')
    filled = round(460 * progress.fraction)
    if filled:
        draw.rectangle((244, 228, 243+filled, 239), fill=ACCENT)
    for x in range(267, 704, 23):
        draw.line((x, 228, x, 239), fill='#18201b', width=2)
    if progress.next:
        fitted(draw, 'NEXT RANK', (244, 260), 220, 11, 11, fill=MUTED, audit=audit)
        fitted(draw, progress.next.name.upper(), (244, 279), 210, 24, 20, display=True, audit=audit)
        remaining = f'{progress.remaining} XP REMAINING'
        right = 704 - draw.textlength(remaining, font=font(14))
        fitted(draw, remaining, (right, 276), 235, 14, 14, fill=MUTED, audit=audit)
    else:
        fitted(draw, 'MAX RANK', (244, 270), 460, 26, 22, fill=ACCENT, display=True, audit=audit)
    buffer = BytesIO()
    image.convert('RGB').save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()
