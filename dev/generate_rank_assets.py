"""Build the original OYB vector masters. Run only when redesigning the template."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'assets/rank-card'
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / 'ranks').mkdir(exist_ok=True)


def svg(body, width=960, height=320):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">{body}</svg>\n'


contours = ''.join(f'<path d="M{650+i*28} 0 L{820+i*28} 130 L{760+i*28} 220 L{850+i*28} 320"/>' for i in range(12))
(ROOT / 'background.svg').write_text(svg('''
<defs><pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#91a595" stroke-width=".5" opacity=".06"/></pattern></defs>
<path d="M16 0H944L960 16V304L944 320H16L0 304V16Z" fill="#101513"/>
<path d="M24 58H210V296H24Z" fill="#1b2420"/>
<path d="M744 58H936V296H744Z" fill="#19211d"/>
<path d="M24 58H936V296H24Z" fill="url(#grid)"/>
<g stroke="#9cb6a0" stroke-width=".8" opacity=".08" fill="none">''' + contours + '</g>'), encoding='utf-8')
(ROOT / 'frame.svg').write_text(svg('''
<path d="M17 1H943L959 17V303L943 319H17L1 303V17Z" stroke="#526054" fill="none"/>
<path d="M24 58H210V296H24ZM744 58H936V296H744Z" stroke="#344338" fill="none"/>
<path d="M24 78V58H44M916 58H936V78M24 276V296H44M916 296H936V276" stroke="#b4c892" stroke-width="2" fill="none"/>
<path d="M132 34H710M744 34H936M244 172H704" stroke="#38483b"/>
<path d="M220 82V272" stroke="#2a352e"/>
<path d="M232 99H238M232 109H238M232 119H238M232 129H238" stroke="#7d8b75"/>
<path d="M720 59L730 69V99M720 295L730 285V255" stroke="#536448" fill="none"/>
'''), encoding='utf-8')
(ROOT / 'overlay.svg').write_text(svg('''
<circle cx="117" cy="142" r="70" fill="#0c100e" stroke="#394d3c"/>
<circle cx="117" cy="142" r="65" fill="none" stroke="#acc48e" stroke-width="2"/>
<path d="M117 66V72M117 212V218M41 142H47M187 142H193" stroke="#acc48e" stroke-width="2"/>
<path d="M790 84L840 65L890 84V156L840 188L790 156Z" fill="#131a16" stroke="#56694b"/>
<path d="M798 89L840 73L882 89V151L840 178L798 151Z" fill="none" stroke="#2f3d32"/>
'''), encoding='utf-8')

def chevron(y, heavy=False):
    h = 10 if heavy else 6
    return f'<path d="M25 {y+18}L64 {y}L103 {y+18}V{y+18+h}L64 {y+h}L25 {y+18+h}Z"/>'

badges = {
    'renegade': '<path d="M25 64L55 49V58L25 74ZM73 49L103 64V74L73 58Z"/><path d="M60 76L64 72L68 76L64 80Z"/>',
    'recruit': chevron(49),
    'private': chevron(45, True) + '<path d="M54 78H74V85H54Z"/>',
    'corporal': chevron(37, True) + chevron(61, True),
    'sergeant': chevron(24, True) + chevron(48, True) + chevron(72, True),
    'lieutenant': '<path d="M64 24L82 49L64 74L46 49ZM33 87H95V95H33Z"/>',
    'captain': '<path d="M46 29L61 50L46 71L31 50ZM82 29L97 50L82 71L67 50ZM33 86H95V95H33Z"/>',
    'major': '<path d="M64 18L74 43L101 42L82 61L93 85L67 76L46 95L48 68L24 55L51 50Z"/><path d="M24 85L36 97H92L104 85V96L94 106H34L24 96Z"/>'
}
for rank, shape in badges.items():
    (ROOT / 'ranks' / (rank + '.svg')).write_text(svg('<g fill="#c1d49f">' + shape + '</g>', 128, 128), encoding='utf-8')
print('Created three reusable layers and eight original insignias.')
