"""Development only: python dev/render_rank_previews.py --output /path/to/previews"""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from rank_card import render_card, WIDTH, HEIGHT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    dest = parser.parse_args().output
    dest.mkdir(parents=True, exist_ok=True)
    cases = [('renegade', 'GazLagom', 0), ('recruit', 'GazLagom', 143),
             ('corporal', 'GARETH', 347), ('sergeant', 'GazLagom', 450),
             ('major', 'GazLagom', 700), ('long-name', 'An exceptionally long OYB player display name with extra words', 347),
             ('unicode', 'Gáréth · Ελληνικά · Игрок', 600)]
    sheet = Image.new('RGB', (WIDTH, HEIGHT*len(cases)), '#080c09')
    for i, (slug, name, xp) in enumerate(cases):
        audit = []
        path = dest / f'{slug}.png'
        path.write_bytes(render_card(name, xp, audit=audit))
        for text, box, limit in audit:
            assert box[0] >= limit[0]-2 and box[1] >= limit[1]-2 and box[2] <= limit[2]+2 and box[3] <= limit[3]+2, (text, box, limit)
            assert 0 <= box[0] < box[2] <= WIDTH and 0 <= box[1] < box[3] <= HEIGHT, (text, box)
        with Image.open(path) as image:
            assert image.size == (WIDTH, HEIGHT)
            sheet.paste(image, (0, HEIGHT*i))
    sheet.save(dest / 'contact-sheet.png')
    print(f'Rendered and checked {len(cases)} cards in {dest}')


if __name__ == '__main__':
    main()
