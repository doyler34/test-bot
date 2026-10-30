from datetime import datetime, timezone

# A Steam account younger than this is called new.
NEW_ACCOUNT_DAYS = 90


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
    return {"text": "Steam age not checked yet", "new": False}


def describe(rows, at, since, online, alts, steam):
    """The card's rows: newest first, with whoever looks worth a second look at the top. A first-time
    player on a new Steam account, or one sharing an address with a banned account, gets a Watch tag."""
    shown = []
    for row in rows:
        pc = row["platform"] == "steam"
        mine = alts.get(row["identity"])
        first = row["first_seen"] >= since
        checked = account(row, at) if steam and pc else None
        watch = bool(first and checked and checked["new"]) or bool(mine and mine["banned"])
        shown.append({"identity": row["identity"], "name": row["name"], "ago": ago(at - row["last_seen"]),
                      "online": row["identity"] in online, "first": first,
                      "platform": "PC" if pc else "Console" if row["platform"] else "",
                      "account": checked, "alts": mine, "watch": watch, "at": row["last_seen"]})
    return sorted(shown, key=lambda r: (not r["watch"], -r["at"]))
