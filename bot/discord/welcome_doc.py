"""What the Start here message and the join greeting say, as plain data.

OYB Control edits these and the bot draws them in Discord, so both import
this: the defaults (today's wording), what each button type needs, and the
checks that keep a saved message inside Discord's limits.
"""
import re
import secrets

STYLES = ("green", "blurple", "grey", "red")
# What a button does when pressed. Link / progress / faction are the steps the
# bot has always had; role, pick and url are the customisable ones.
TYPES = {
    "link": "Link Reforger account",
    "progress": "My progress",
    "faction": "Pick a faction (US / USSR / FIA)",
    "no_faction": "No faction",
    "role": "Give or take a role",
    "pick": "Pick one role from a group",
    "url": "Open a web link",
    "feedback": "Give feedback",
}
FACTION_NAMES = ("US", "USSR", "FIA")
MAX_BUTTONS = 25
MAX_PER_LINE = 5
LINES = 5
TITLE_MAX = 256
TEXT_MAX = 4000
LABEL_MAX = 80
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def new_id():
    return secrets.token_hex(4)


def default_welcome():
    """Exactly what the Start here panel said before it could be edited."""
    return {
        "channel_id": None,
        "title": "Start here",
        "colour": "#A9BC8C",
        "sections": [
            {"heading": "1 — Link your Reforger account", "text": (
                "Play a round on any OYB server, then hit **Link Reforger account** and type your "
                "in-game name or player ID.\n\n"
                "Name yours and unclaimed? You are in straight away. Otherwise an admin checks it.\n\n"
                "Linking opens up the rest of the server and starts your kills, deaths and playtime "
                "counting towards your rank.")},
            {"heading": "2 — Pick your side (optional)", "text": (
                "⚠️ **THIS DOES NOT LOCK YOUR FACTION IN GAME.** Play US, USSR or FIA on the "
                "servers whenever you like. It is a Discord role and nothing else, and you can "
                "skip it entirely.\n\n"
                "If you want one: it colours your name, opens that side's channels and drops "
                "**OYB Renegade**. You cannot swap it yourself on Discord after, so go with your "
                "mates. Not fussed? Press **No faction**.")},
            {"heading": "", "text": "Stuck? **My progress** shows what you are missing."},
        ],
        "buttons": [
            {"id": "link", "type": "link", "label": "1. Link Reforger account", "emoji": "", "style": "green", "line": 1},
            {"id": "progress", "type": "progress", "label": "My progress", "emoji": "", "style": "grey", "line": 1},
            {"id": "us", "type": "faction", "faction": "US", "label": "US", "emoji": "🇺🇸", "style": "grey", "line": 2},
            {"id": "ussr", "type": "faction", "faction": "USSR", "label": "USSR", "emoji": "🇷🇺", "style": "grey", "line": 2},
            {"id": "fia", "type": "faction", "faction": "FIA", "label": "FIA", "emoji": "🏳️", "style": "grey", "line": 2},
            {"id": "nofaction", "type": "no_faction", "label": "No faction", "emoji": "", "style": "grey", "line": 2},
        ],
    }


def default_greeting():
    return {
        "enabled": False,
        "where": "channel",
        "channel_id": None,
        "text": "Welcome to **{server}**, {user}! Head to {start} to link your account and get going.",
    }


def description(doc):
    """The embed text: each section's heading in bold, then its text."""
    parts = []
    for section in doc.get("sections", []):
        heading, text = section.get("heading", "").strip(), section.get("text", "").strip()
        if heading and text:
            parts.append(f"**{heading}**\n{text}")
        elif heading or text:
            parts.append(f"**{heading}**" if heading else text)
    return "\n\n".join(parts)


def _id(value):
    if value in (None, ""):
        return None
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        return int(value)
    raise ValueError


def check_welcome(raw):
    """(clean doc, problems). Problems are sentences an owner can act on;
    a doc with any is not saved."""
    problems = []
    if not isinstance(raw, dict):
        return default_welcome(), ["That message couldn't be read. Reload and try again."]
    doc = {"title": str(raw.get("title", "")).strip()[:TITLE_MAX],
           "colour": str(raw.get("colour", "#A9BC8C")).strip()}
    try:
        doc["channel_id"] = _id(raw.get("channel_id"))
    except ValueError:
        problems.append("Pick the channel from the list.")
        doc["channel_id"] = None
    if not HEX.match(doc["colour"]):
        problems.append("The colour should look like #A9BC8C.")
        doc["colour"] = "#A9BC8C"
    doc["sections"] = []
    for section in raw.get("sections", []) if isinstance(raw.get("sections"), list) else []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading", "")).strip()[:TITLE_MAX]
        text = str(section.get("text", "")).replace("\r\n", "\n").strip()
        if heading or text:
            doc["sections"].append({"heading": heading, "text": text})
    if not doc["title"] and not doc["sections"]:
        problems.append("Give the message a title or some text.")
    if len(description(doc)) > TEXT_MAX:
        problems.append(f"The text is {len(description(doc))} characters; Discord allows {TEXT_MAX}.")
    doc["buttons"] = []
    seen = set()
    for n, button in enumerate(raw.get("buttons", []) if isinstance(raw.get("buttons"), list) else [], 1):
        if not isinstance(button, dict):
            continue
        clean, issues = _check_button(button, n)
        problems += issues
        if clean["id"] in seen:
            clean["id"] = new_id()
        seen.add(clean["id"])
        doc["buttons"].append(clean)
    if len(doc["buttons"]) > MAX_BUTTONS:
        problems.append(f"Discord allows {MAX_BUTTONS} buttons on one message.")
    for line in range(1, LINES + 1):
        if sum(b["line"] == line for b in doc["buttons"]) > MAX_PER_LINE:
            problems.append(f"Line {line} has more than {MAX_PER_LINE} buttons; move some to another line.")
    return doc, problems


def _check_button(raw, n):
    problems = []
    kind = raw.get("type") if raw.get("type") in TYPES else "role"
    label = str(raw.get("label", "")).strip()[:LABEL_MAX]
    emoji = str(raw.get("emoji", "")).strip()[:40]
    name = f"Button {n}" + (f" ({label})" if label else "")
    if not label and not emoji:
        problems.append(f"{name} needs a label or an emoji.")
    try:
        line = min(max(int(raw.get("line", 1)), 1), LINES)
    except (TypeError, ValueError):
        line = 1
    button_id = str(raw.get("id", "")).strip()
    if not re.fullmatch(r"[a-z0-9]{1,16}", button_id):
        button_id = new_id()
    button = {"id": button_id, "type": kind, "label": label, "emoji": emoji, "line": line,
              "style": raw.get("style") if raw.get("style") in STYLES else "grey"}
    if kind == "faction":
        button["faction"] = raw.get("faction") if raw.get("faction") in FACTION_NAMES else "US"
    elif kind in ("role", "pick"):
        try:
            button["role_id"] = _id(raw.get("role_id"))
        except ValueError:
            button["role_id"] = None
        button["role_name"] = str(raw.get("role_name", "")).strip()[:100]
        if not button["role_id"] and not button["role_name"]:
            problems.append(f"{name} needs a role: pick one, or type a name for a new one.")
        button["linked_only"] = bool(raw.get("linked_only"))
        if kind == "pick":
            button["group"] = re.sub(r"\s+", " ", str(raw.get("group", "")).strip())[:40] or "Group"
            button["locked"] = bool(raw.get("locked"))
    elif kind == "url":
        button["url"] = str(raw.get("url", "")).strip()[:512]
        if not re.match(r"^https?://\S+\.\S+", button["url"]):
            problems.append(f"{name} needs a web address starting with https://.")
    return button, problems


def check_greeting(raw):
    problems = []
    if not isinstance(raw, dict):
        return default_greeting(), ["That greeting couldn't be read. Reload and try again."]
    doc = {"enabled": bool(raw.get("enabled")),
           "where": raw.get("where") if raw.get("where") in ("channel", "dm") else "channel",
           "text": str(raw.get("text", "")).replace("\r\n", "\n").strip()[:2000]}
    try:
        doc["channel_id"] = _id(raw.get("channel_id"))
    except ValueError:
        doc["channel_id"] = None
        problems.append("Pick the channel from the list.")
    if doc["enabled"] and not doc["text"]:
        problems.append("Write the greeting, or turn it off.")
    if doc["enabled"] and doc["where"] == "channel" and not doc["channel_id"]:
        problems.append("Pick which channel the greeting goes in.")
    return doc, problems


PLACEHOLDERS = {"{user}": "mentions them", "{name}": "their name", "{server}": "the Discord's name",
                "{members}": "how many members there are", "{start}": "a link to the Start here channel"}


def fill_greeting(text, mention, name, server, members, start):
    values = {"{user}": mention, "{name}": name, "{server}": server, "{members}": str(members), "{start}": start}
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def default_names():
    return {"names": {}}


def check_names(raw):
    """Server id -> display name. Blank means the bot's default name."""
    if not isinstance(raw, dict) or not isinstance(raw.get("names"), dict):
        return default_names(), ["Those names couldn't be read. Reload and try again."]
    names, problems = {}, []
    for server_id, name in raw["names"].items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", str(server_id)):
            continue
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
        if "·" in name:
            # The stat channels read "Name · 21 min"; a dot in the name would split it wrongly.
            problems.append(f"{server_id}: names can't contain · (the bot uses it in the channel names).")
            name = name.replace("·", "").strip()
        if name:
            names[str(server_id)] = name
    taken = {}
    for server_id, name in names.items():
        if name.lower() in taken:
            problems.append(f"{taken[name.lower()]} and {server_id} are both called {name}; give each its own name.")
        taken[name.lower()] = server_id
    return {"names": names}, problems


def default_serverinfo():
    """Blank means the bot's own wording; the panel shows that wording in the boxes."""
    return {"title": "", "intro": "", "settings": {}, "rules_title": "", "rules": ""}


def check_serverinfo(raw):
    if not isinstance(raw, dict):
        return default_serverinfo(), ["That couldn't be read. Reload and try again."]
    text = lambda key, limit: str(raw.get(key, "") or "").replace("\r\n", "\n").strip()[:limit]
    doc = {"title": text("title", TITLE_MAX), "intro": text("intro", 1000),
           "rules_title": text("rules_title", TITLE_MAX), "rules": text("rules", TEXT_MAX), "settings": {}}
    problems = []
    for server_id, value in (raw.get("settings") or {}).items() if isinstance(raw.get("settings"), dict) else []:
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", str(server_id)):
            value = str(value or "").replace("\r\n", "\n").strip()
            if len(value) > 900:
                problems.append(f"{server_id}'s settings are {len(value)} characters; keep them under 900.")
            if value:
                doc["settings"][str(server_id)] = value[:900]
    return doc, problems


DM_TITLE = "You've been banned from {server}"
DM_TEXT = "{account} is banned from all our servers {length}."
TICKET_TITLE = "This player is banned"
BAN_PLACEHOLDERS = {"{server}": "the Discord's name", "{account}": "their game account's name",
                    "{length}": "\"for 7 days\" or \"permanently\""}


def default_bans():
    """Blank text means the built-in wording; the settings the bot's .env used to hold live here now."""
    return {"dm_enabled": True, "dm_title": "", "dm_text": "", "appeal": "",
            "tickets_enabled": True, "ticket_channel": None, "ticket_categories": [], "ticket_title": "",
            "panel_url": "", "ticket_private": False, "ticket_role": ""}


def check_bans(raw):
    if not isinstance(raw, dict):
        return default_bans(), ["That couldn't be read. Reload and try again."]
    problems = []
    text = lambda key, limit: str(raw.get(key, "") or "").replace("\r\n", "\n").strip()[:limit]
    doc = {"dm_enabled": bool(raw.get("dm_enabled")), "dm_title": text("dm_title", TITLE_MAX),
           "dm_text": text("dm_text", 2000), "appeal": text("appeal", 1000),
           "tickets_enabled": bool(raw.get("tickets_enabled")), "ticket_title": text("ticket_title", TITLE_MAX),
           "panel_url": text("panel_url", 200).rstrip("/"),
           "ticket_private": bool(raw.get("ticket_private")), "ticket_role": text("ticket_role", 100)}
    try:
        doc["ticket_channel"] = _id(raw.get("ticket_channel"))
    except ValueError:
        doc["ticket_channel"] = None
        problems.append("Pick the ticket panel channel from the list.")
    categories = []
    for value in raw.get("ticket_categories") or [] if isinstance(raw.get("ticket_categories"), list) else []:
        try:
            if _id(value):
                categories.append(_id(value))
        except ValueError:
            pass
    doc["ticket_categories"] = sorted(set(categories))[:10]
    if doc["panel_url"] and not re.match(r"^https?://\S+$", doc["panel_url"]):
        problems.append("The panel address should start with https://.")
    if doc["tickets_enabled"] and not doc["ticket_channel"] and not doc["ticket_categories"]:
        problems.append("Pick the ticket panel channel or a ticket category, or turn ban cards in tickets off.")
    if doc["tickets_enabled"] and doc["ticket_private"] and not doc["ticket_role"]:
        problems.append("Pick the role that should see the ban card in its private thread.")
    return doc, problems


POST_TYPES = {key: TYPES[key] for key in ("link", "progress", "role", "pick", "url", "feedback")}


def default_post():
    return {"channel_id": None, "title": "", "colour": "#D9A441",
            "sections": [{"heading": "", "text": ""}], "buttons": []}


def check_post(raw):
    """A post is a message like Start here, with only the buttons that make
    sense anywhere: linking, progress, roles, a pick-one group, and web links."""
    doc, problems = check_welcome(raw)
    for button in doc["buttons"]:
        if button["type"] not in POST_TYPES:
            problems.append(f"The {button['label'] or 'unnamed'} button can't be used on a post.")
    if not doc["channel_id"]:
        problems.append("Pick which channel to post it in.")
    return doc, problems


def default_matchping():
    """Blank wording keeps the bot's own; every server is on and pings by default."""
    return {"title": "", "text": "", "ping": True, "off": []}


def check_matchping(raw):
    if not isinstance(raw, dict):
        return default_matchping(), ["That couldn't be read. Reload and try again."]
    off = [str(s) for s in raw.get("off", []) if re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", str(s))] \
        if isinstance(raw.get("off"), list) else []
    doc = {"title": str(raw.get("title", "") or "").strip()[:200],
           "text": str(raw.get("text", "") or "").replace("\r\n", "\n").strip()[:1500],
           "ping": bool(raw.get("ping")), "off": sorted(set(off))}
    return doc, []


def default_weekly():
    """Blank wording keeps the bot's own."""
    return {"on": True, "title": "", "intro": "", "outro": ""}


def check_weekly(raw):
    if not isinstance(raw, dict):
        return default_weekly(), ["That couldn't be read. Reload and try again."]
    text = lambda key, limit: str(raw.get(key, "") or "").replace("\r\n", "\n").strip()[:limit]
    return {"on": bool(raw.get("on")), "title": text("title", 200), "intro": text("intro", 1500),
            "outro": text("outro", 1500)}, []


def default_staffalerts():
    return {"on": True, "count": 3, "seconds": 60, "ping_role": "", "mines": 5}


def check_staffalerts(raw):
    if not isinstance(raw, dict):
        return default_staffalerts(), ["That couldn't be read. Reload and try again."]
    problems = []

    def number(key, low, high, label):
        try:
            value = int(raw.get(key))
        except (TypeError, ValueError):
            value = default_staffalerts()[key]
        if not low <= value <= high:
            problems.append(f"{label} has to be between {low} and {high}.")
            value = min(max(value, low), high)
        return value
    doc = {"on": bool(raw.get("on")), "count": number("count", 2, 20, "Teamkills"),
           "seconds": number("seconds", 10, 600, "Seconds"),
           "ping_role": str(raw.get("ping_role", "") or "").strip()[:100]}
    doc["mines"] = number("mines", 0, 50, "AP mine kills") if raw.get("mines") not in (None, "") else 5
    return doc, problems


FEEDBACK_THANKS = "Thanks! Staff will read it."
FEEDBACK_DONE = "Thanks for your feedback about **{topic}**. Staff have read it and dealt with it."


def default_feedback():
    return {"on": True, "thanks": FEEDBACK_THANKS, "done_dm": FEEDBACK_DONE, "ping_role": "", "topics": []}


def check_feedback(raw):
    if not isinstance(raw, dict):
        return default_feedback(), ["That couldn't be read. Reload and try again."]
    doc = {"on": bool(raw.get("on")),
           "thanks": str(raw.get("thanks", "") or "").strip()[:500] or FEEDBACK_THANKS,
           "done_dm": str(raw.get("done_dm", "") or "").strip()[:1500],
           "ping_role": str(raw.get("ping_role", "") or "").strip()[:100], "topics": []}
    problems = []
    topics = raw.get("topics") or []
    for topic in topics.splitlines() if isinstance(topics, str) else topics:
        topic = " ".join(str(topic).split())[:100]
        if topic and topic.casefold() not in (t.casefold() for t in doc["topics"]):
            doc["topics"].append(topic)
    if len(doc["topics"]) > 25:
        problems.append("Discord allows up to 25 topics in the list.")
        doc["topics"] = doc["topics"][:25]
    return doc, problems


# (setting, what it is) for the Channels & roles page, in the order shown.
CHANNEL_SETTINGS = (
    ("LIVE_BOARD_CHANNEL_ID", "Live match board"),
    ("GAME_LEADERBOARD_CHANNEL_ID", "Match results"),
    ("LEADERBOARD_CHANNEL_ID", "Weekly leaderboard"),
    ("RANK_LOG_CHANNEL_ID", "Rank promotions"),
)
# (page, setting, what the bot does with no channel picked) for the channel
# picked on that page's own Discord page. Stored with the Channels & roles ones.
PAGE_CHANNELS = (
    ("serverinfo", "SERVERS_CHANNEL_ID", "The bot's own #servers channel"),
    ("factions", "FACTION_CHANNEL_ID", "The bot's built-in channel"),
    ("matchping", "MATCH_ALERT_CHANNEL_ID", "#announcements"),
    ("weekly", "WEEKLY_WINNERS_CHANNEL_ID", "Same as the weekly leaderboard"),
    ("staffalerts", "STAFF_ALERT_CHANNEL_ID", "None picked (alerts wait for one)"),
    ("feedback", "FEEDBACK_CHANNEL_ID", "None picked (the form says it isn't set up)"),
    ("links", "LINK_REVIEW_CHANNEL_ID", "The bot's own #oyb-link-requests"),
)
# Older publishes could set the Start here channel here; the Start here page picks it now.
CHANNEL_KEYS = tuple(k for k, _ in CHANNEL_SETTINGS) + tuple(k for _, k, _ in PAGE_CHANNELS) + ("ONBOARDING_CHANNEL_ID",)
ROLE_SETTINGS = (
    ("MEMBER_ROLE_NAME", "Member role (given when someone accepts the rules; point channel permissions at this)"),
    ("UNVERIFIED_ROLE_NAME", "Unverified label (on people who haven't linked yet)"),
)


def default_channels():
    """Blank means keep what the bot's .env says."""
    return {"channels": {}, "roles": {}}


def check_channels(raw):
    if not isinstance(raw, dict):
        return default_channels(), ["That couldn't be read. Reload and try again."]
    doc, problems = default_channels(), []
    for key in CHANNEL_KEYS:
        value = (raw.get("channels") or {}).get(key) if isinstance(raw.get("channels"), dict) else None
        try:
            if _id(value):
                doc["channels"][key] = str(_id(value))
        except ValueError:
            problems.append(f"Pick the {dict(CHANNEL_SETTINGS).get(key, 'channel')} channel from the list.")
    for key, _ in ROLE_SETTINGS:
        value = (raw.get("roles") or {}).get(key) if isinstance(raw.get("roles"), dict) else None
        value = str(value or "").strip()[:100]
        if value:
            doc["roles"][key] = value
    return doc, problems


# What each faction looks like in Discord. The key (US, USSR, FIA) never
# changes: rank ladders and the rank card artwork hang off it.
FACTION_LOOK = {"US": {"name": "US", "colour": "#3B5B8C", "emoji": "🇺🇸"},
                "USSR": {"name": "USSR", "colour": "#B23A32", "emoji": "🇷🇺"},
                "FIA": {"name": "FIA", "colour": "#8A7B3F", "emoji": "🏳️"}}


def default_factions():
    return {"factions": {key: dict(look) for key, look in FACTION_LOOK.items()}}


def check_factions(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("factions"), dict):
        return default_factions(), ["That couldn't be read. Reload and try again."]
    doc, problems = default_factions(), []
    for key, look in doc["factions"].items():
        given = raw["factions"].get(key)
        if not isinstance(given, dict):
            continue
        name = re.sub(r"\s+", " ", str(given.get("name", "") or "")).strip()[:100]
        colour = str(given.get("colour", "") or "").strip()
        emoji = str(given.get("emoji", "") or "").strip()[:40]
        if name:
            look["name"] = name
        else:
            problems.append(f"{key} needs a name.")
        if HEX.match(colour):
            look["colour"] = colour.upper()
        else:
            problems.append(f"{key}'s colour should look like #3B5B8C.")
        look["emoji"] = emoji
    taken = {}
    for key, look in doc["factions"].items():
        if look["name"].lower() in taken:
            problems.append(f"{taken[look['name'].lower()]} and {key} are both called {look['name']}; "
                            "each faction needs its own name.")
        taken[look["name"].lower()] = key
    return doc, problems
