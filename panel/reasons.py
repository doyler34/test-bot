"""Ready-made kick and ban reasons. Every reason the panel saves points the
player at the Discord to appeal."""

APPEAL = "Appeal: discord.gg/oyb"

BAN_REASONS = (
    "Cheating or hacking",
    "Exploiting bugs or glitches",
    "Teamkilling on purpose",
    "Spamming AP mines on friendlies",
    "Racism or hate speech",
    "Harassing or abusing other players",
    "Griefing: destroying friendly vehicles or bases",
    "Ban evasion on another account",
    "Ignoring admin warnings",
)

KICK_REASONS = (
    "Teamkilling: warning",
    "Toxic chat or VON: warning",
    "Ignoring admin instructions",
    "Griefing friendly vehicles or bases",
    "AFK and holding a slot",
    "Trolling or blocking objectives",
)


def with_appeal(reason: str, limit: int = 200) -> str:
    if "discord.gg/oyb" in reason.lower():
        return reason[:limit]
    tail = f". {APPEAL}"
    return reason[:limit - len(tail)].rstrip(" .") + tail


def options(reasons):
    return [with_appeal(r) for r in reasons]
