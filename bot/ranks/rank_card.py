"""Offline, cached vector template with in-memory PNG composition."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import unicodedata

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
import resvg_py
from bot.ranks.rank_rules import rank_for_xp

ASSETS = Path(__file__).resolve().parents[2] / 'assets/rank-card'
WIDTH, HEIGHT = 960, 320
INK, MUTED, ACCENT = '#e9eee5', '#9a927f', '#D9A441'


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


@lru_cache(maxsize=8)
def template(layers):
    image = Image.new('RGBA', (WIDTH, HEIGHT), '#101513')
    for name in layers:
        try:
            image.alpha_composite(vector(ASSETS / name, WIDTH, HEIGHT))
        except (OSError, ValueError):
            pass
    return image


# accent, crest, the artwork layers that make each side's card its own.
FACTION_THEMES = {
    'US':   ('#8FB4E6', 'us.svg',   ('background-us.svg',)),
    'USSR': ('#E5675E', 'ussr.png', ('background-ussr.svg',)),
    'FIA':  ('#8FBF5E', 'fia.png',  ('background-fia.svg',)),
}
DEFAULT_LAYERS = ('background.svg', 'frame.svg')


def shade(colour, factor):
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return tuple(min(255, round(channel * factor)) for channel in (red, green, blue))


def furniture(draw, accent):
    """The portrait ring and the insignia plate, drawn rather than baked into a
    layer, so every faction gets them in its own colour."""
    draw.ellipse((47, 72, 187, 212), fill='#0c100e', outline=shade(accent, .42))
    draw.ellipse((52, 77, 182, 207), outline=accent, width=2)
    for line in ((117, 66, 117, 72), (117, 212, 117, 218), (41, 142, 47, 142), (187, 142, 193, 142)):
        draw.line(line, fill=accent, width=2)
    plate = [(790, 84), (840, 65), (890, 84), (890, 156), (840, 188), (790, 156)]
    draw.polygon(plate, fill=shade(accent, .13), outline=shade(accent, .5))
    draw.polygon([(798, 89), (840, 73), (882, 89), (882, 151), (840, 178), (798, 151)],
                 outline=shade(accent, .3))


def served(milliseconds):
    minutes = max(0, int((milliseconds or 0) // 60000))
    hours, minutes = divmod(minutes, 60)
    return f'{hours:,}h {minutes:02d}m' if hours else f'{minutes}m'


@lru_cache(maxsize=8)
def crest(name, size):
    path = ASSETS / 'factions' / name
    if name.endswith('.svg'):
        return vector(path, size, size)
    with Image.open(path) as image:
        rgba = image.convert('RGBA')
    return rgba.resize((round(rgba.width * size / rgba.height), size), Image.Resampling.LANCZOS)


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


def render_card(name, xp, avatar=None, *, audit=None, faction=None, playtime_ms=0):
    progress = rank_for_xp(xp, faction)
    accent, crest_file, layers = FACTION_THEMES.get(faction, (ACCENT, None, DEFAULT_LAYERS))
    image = template(layers).copy()
    draw = ImageDraw.Draw(image)
    furniture(draw, accent)
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
        draw.polygon([(840, 97), (865, 127), (840, 157), (815, 127)], outline=accent, width=3)
    fitted(draw, 'O.Y.B', (30, 16), 90, 28, 28, display=True, audit=audit)
    fitted(draw, 'COMMUNITY RANK', (148, 20), 300, 13, 13, fill=MUTED, audit=audit)
    fitted(draw, 'SERVICE RECORD', (774, 20), 162, 12, 12, fill=MUTED, audit=audit)
    fitted(draw, name, (244, 65), 460, 23, 15, audit=audit)
    fitted(draw, progress.current.name.upper(), (241, 98), 466, 61, 44, fill=accent, display=True, audit=audit)
    fitted(draw, 'SERVICE TIME', (46, 220), 150, 12, 11, fill=MUTED, audit=audit)
    fitted(draw, served(playtime_ms), (45, 238), 152, 26, 17, fill=accent, display=True, audit=audit)
    fitted(draw, 'O.Y.B  /  REFORGER', (45, 274), 150, 12, 11, fill=MUTED, audit=audit)
    draw.text((840, 194), 'FACTION', font=font(12), fill=MUTED, anchor='mt')
    if crest_file:
        emblem = crest(crest_file, 48)
        image.alpha_composite(emblem, (840 - emblem.width // 2, 232 - emblem.height // 2))
        draw.text((840, 262), faction, font=font(22, True), fill=accent, anchor='mt')
    else:
        draw.text((840, 220), 'O.Y.B', font=font(32, True), fill=accent, anchor='mt')
        draw.text((840, 264), 'UNALIGNED', font=font(15), fill=MUTED, anchor='mt')
    # A Renegade is held there by having no faction, not by being short of XP,
    # so their card counts no threshold down and says what actually unblocks it.
    blocked = not faction
    xp_text = (f'{progress.xp:,} XP' if blocked or not progress.next
               else f'{progress.xp:,} / {progress.next.threshold:,} XP')
    fitted(draw, xp_text, (244, 188), 460, 27, 16, display=True, audit=audit)
    draw.rounded_rectangle((244, 228, 704, 239), radius=2, fill=shade(accent, .26))
    filled = round(460 * progress.fraction)
    if filled:
        draw.rectangle((244, 228, 243+filled, 239), fill=accent)
    for x in range(267, 704, 23):
        draw.line((x, 228, x, 239), fill=shade(accent, .1), width=2)
    if progress.next:
        fitted(draw, 'NEXT RANK', (244, 260), 220, 11, 11, fill=MUTED, audit=audit)
        fitted(draw, progress.next.name.upper(), (244, 279), 210, 24, 20, display=True, audit=audit)
        remaining = 'PICK A FACTION' if blocked else f'{progress.remaining} XP REMAINING'
        right = 704 - draw.textlength(remaining, font=font(14))
        fitted(draw, remaining, (right, 276), 235, 14, 14, fill=MUTED, audit=audit)
    else:
        fitted(draw, 'MAX RANK', (244, 270), 460, 26, 22, fill=accent, display=True, audit=audit)
    buffer = BytesIO()
    image.convert('RGB').save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()
