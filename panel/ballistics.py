"""The bullet drop guide, as Discord posts: one to explain it, then one per kind of gun."""

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "assets" / "ballistics" / "rounds.json"
# Fixed ids so posting the guide again edits the same messages.
POST_IDS = ["ba11e700", "ba11e701", "ba11e702", "ba11e703", "ba11e704", "ba11e705"]
LOOKS = {
    "Rifles": ("🔫 Rifles", "#7FA36B"),
    "Machine guns and marksman rifles": ("🎯 Machine guns & marksman rifles", "#D9A441"),
    "Heavy machine guns": ("💥 Heavy machine guns", "#D9773F"),
    "Pistols": ("🔫 Pistols", "#5B8FC9"),
    "Launchers": ("🚀 Launchers", "#C9524A"),
}

HOW_TO = [
    {"heading": "1️⃣  Set your zero",
     "text": "Most guns let you set the sight for a distance with **Page Up / Page Down**. "
             "Set it to how far away they are and aim dead on. That beats any table."},
    {"heading": "2️⃣  No time? Aim above",
     "text": "The tables are for a sight set to **100 m**. **Aim above** is how many metres over the "
             "target to put your sight.\n"
             "• A person is about **1.8 m** tall: aim **+0.9 m** = top of the head to hit the middle\n"
             "• **low** means aim under them instead"},
    {"heading": "3️⃣  Lead runners",
     "text": "**Flight** is how long the round is in the air. The longer it flies, the further ahead "
             "of someone running you need to aim."},
    {"heading": "📖  Where it's from",
     "text": "The game's own ballistic data, at each round's normal muzzle speed. "
             "Short barrels shoot a little lower."},
]


def rounds():
    return json.loads(DATA.read_text())["rounds"]


def aim(value):
    if abs(value) < 0.05:
        return "zero"
    size = f"{abs(value):.0f}" if abs(value) >= 10 else f"{abs(value):.1f}"
    return f"{size} m" + (" low" if value < 0 else "")


def block(item):
    name = f"**{item['round']}**"
    if item["used_by"] != item["round"]:
        name += f"  ·  {item['used_by']}"
    if item["lobbed"]:
        lines = [" Range │ Flight", "───────┼───────"]
        lines += [f"{r:>4} m │ {t:.1f} s" for r, _, t in item["rows"]]
        return f"{name}\n```\n" + "\n".join(lines) + "\n```Use the range marks on the sight."
    lines = [" Range │ Aim above │ Flight", "───────┼───────────┼───────"]
    lines += [f"{r:>4} m │ {aim(up):>9} │ {t:.1f} s" for r, up, t in item["rows"]]
    return f"{name}\n```\n" + "\n".join(lines) + "\n```"


def posts(channel_id):
    """[(post id, doc)] in the order they should appear in the channel."""
    groups = {}
    for item in rounds():
        groups.setdefault(item["group"], []).append(item)
    docs = [{"title": "📏 Bullet drop guide", "colour": "#A9BC8C", "sections": HOW_TO}]
    for group, items in groups.items():
        title, colour = LOOKS[group]
        docs.append({"title": title, "colour": colour,
                     "sections": [{"heading": "", "text": block(i)} for i in items]})
    return [(post_id, {"channel_id": channel_id, "buttons": [], **doc})
            for post_id, doc in zip(POST_IDS, docs)]
