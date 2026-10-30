from datetime import datetime, timezone

# A Steam account younger than this is called new.
NEW_ACCOUNT_DAYS = 90
UNCHECKED = "Steam age not checked yet"


def window_text(minutes: int) -> str:
    """"30 minutes", "2 hours", "3 days": the time looked back over, in the biggest unit that divides it."""
    for size, word in ((1440, "day"), (60, "hour"), (1, "minute")):
        if minutes % size == 0:
            count = minutes // size
            return f"{count} {word}{'' if count == 1 else 's'}"


def ago(seconds: int) -> str:
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min ago"


def account(row, at):
    """What the card says about a player's Steam account, and whether it's a new one."""
    if row["created"]:
        days = (at - row["created"]) // 86400
        if days < NEW_ACCOUNT_DAYS:
            return {"text": "New Steam account, " + (f"{days} days" if days > 1 else "made today" if days < 1 else "1 day"), "new": True}
        return {"text": "Steam since " + datetime.fromtimestamp(row["created"], timezone.utc).strftime("%b %Y"), "new": False}
    if row["state"]:
        return {"text": "Steam profile private", "new": False}
    return {"text": UNCHECKED, "new": False}


def describe(rows, at, since, online, alts, steam, known=None):
    """The card's rows. Whoever looks worth a second look is at the top (a first-time player on a new
    Steam account, or one sharing an address with a banned account, gets a Watch tag), then everyone who has
    never been on OYB, then the rest, newest first. known is who OYB has play time for, or None when the
    panel has no play-time records to go by."""
    shown = []
    for row in rows:
        pc = row["platform"] == "steam"
        mine = alts.get(row["identity"])
        first = row["first_seen"] >= since and not (known and row["identity"] in known)
        checked = account(row, at) if steam and pc else None
        watch = bool(first and checked and checked["new"]) or bool(mine and mine["banned"])
        shown.append({"identity": row["identity"], "name": row["name"], "ago": ago(at - row["last_seen"]),
                      "online": row["identity"] in online, "first": first,
                      "platform": "PC" if pc else "Console" if row["platform"] else "",
                      "account": checked, "alts": mine, "watch": watch, "at": row["last_seen"]})
    return sorted(shown, key=lambda r: (not r["watch"], not r["first"], -r["at"]))
