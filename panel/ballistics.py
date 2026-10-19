"""The bullet drop guide, as Discord posts: one to explain it, then one per kind of gun."""

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "assets" / "ballistics" / "rounds.json"
# Fixed ids so posting the guide again edits the same messages.
POST_IDS = ["ba11e700", "ba11e701", "ba11e702", "ba11e703", "ba11e704", "ba11e705"]
COLOUR = "#D9A441"

HOW_TO = [
    {"heading": "Set your zero first",
     "text": "Most guns let you set the sight for a distance with **Page Up / Page Down**. "
             "Set it to how far away they are and aim straight at them. That beats any table."},
    {"heading": "When you can't (or there's no time)",
     "text": "The tables below are for a sight set to **100 m**. At each distance, aim that many "
             "metres **above** the target. A person is about 1.8 m tall, so +0.9 means aim at the top of "
             "their head to hit them in the middle. A minus means aim lower."},
    {"heading": "Flight time",
     "text": "How long the round is in the air. The longer it is, the further ahead of someone "
             "running you need to aim."},
    {"heading": "Where it's from",
     "text": "Worked out from the game's own ballistic data, at the normal muzzle speed for each round. "
             "Short barrels shoot a little lower than this."},
]


def rounds():
    return json.loads(DATA.read_text())["rounds"]


def metres(value):
    if value is None:
        return ""
    if abs(value) < 0.05:
        return "0"
    if abs(value) >= 10:
        return f"{value:+.0f}"
    return f"{value:+.1f}"


def block(item):
    lines = [f"{item['round']} · {item['used_by']}"]
    if item["lobbed"]:
        lines.append("Range  Flight")
        lines += [f"{r:>4}m  {t:.1f}s" for r, _, t in item["rows"]]
    else:
        lines.append("Range  Aim up  Flight")
        lines += [f"{r:>4}m  {metres(aim):>6}  {t:.2f}s" for r, aim, t in item["rows"]]
    note = "Use the range marks on the sight." if item["lobbed"] else ""
    return "```\n" + "\n".join(lines) + "\n```" + (f"\n{note}" if note else "")


def posts(channel_id):
    """[(post id, doc)] in the order they should appear in the channel."""
    groups = {}
    for item in rounds():
        groups.setdefault(item["group"], []).append(item)
    docs = [{"title": "Bullet drop guide", "sections": HOW_TO}]
    for group, items in groups.items():
        docs.append({"title": group, "sections": [{"heading": "", "text": block(i)} for i in items]})
    return [(post_id, {"channel_id": channel_id, "colour": COLOUR, "buttons": [], **doc})
            for post_id, doc in zip(POST_IDS, docs)]
