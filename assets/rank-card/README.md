# OYB card artwork

Original geometric SVG artwork created for this repository. No game screenshots,
copied logos or downloaded insignias. The template is 960 × 320 pixels.

- `background.svg`: charcoal panels, fine grid and contour lines.
- `frame.svg`: thin borders and corner accents.
- `overlay.svg`: avatar ring and insignia frame.
- `ranks/*.svg`: eight original 128 × 128 insignias, rendered at 100 × 100.
- `fonts/Display.ttf`: OYB Display, a renamed Barlow Condensed SemiBold subset.
- `fonts/Body.ttf`: OYB Body, renamed Noto Sans Medium, normal width, subset.

Both fonts are licensed under SIL OFL 1.1. Their complete copyright notices and
licenses ship beside the fonts. Sources from Google Fonts:
https://github.com/google/fonts/tree/main/ofl/barlowcondensed
https://github.com/google/fonts/tree/main/ofl/notosans
The build instantiated Noto's width=100 and weight=500 axes and retained Latin,
Greek, Cyrillic and general punctuation. Unsupported scripts/emoji may display
the font's missing-glyph symbol; names remain bounded and never crash rendering.
No proprietary/system font is required. Pillow's default is an emergency fallback.

Edit the SVGs to change the artwork. `dev/generate_rank_assets.py` reconstructs
the original SVG masters (it overwrites these files). Layout, colours, text sizing
and PNG composition live in `bot/ranks/rank_card.py`; progression lives in `bot/ranks/rank_rules.py`.
Nothing here fetches external assets at runtime. The Discord command attempts to
fetch the caller's Discord avatar, falling back to a local silhouette on failure.

Preview all states with:
```
python dev/render_rank_previews.py --output /tmp/oyb-rank-previews
```
These outputs are development artifacts, not production player state.
