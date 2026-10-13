"""The Investigate report: one player and every account that has shared an IP
with them, gone through kill by kill.

Everything here works on rows the panel already keeps (kills, connections,
per-game totals, Sus lines, incidents, bans) and says what it found in plain
words, with the kills behind each finding. It is evidence to look at, not a
ruling: a strong finding is something a person can't do, a moderate one is
something a very good player might.
"""
from collections import Counter

STRONG, MODERATE, NOTE = "strong", "moderate", "note"
# Two bullet kills this close together in time...
SWITCH_SECONDS = 2
# ...this far apart in range can't both be one person aiming.
SWITCH_METRES = 400
BURST_SECONDS = 10
BURST_KILLS = 5
LONG = 500
TOGETHER_SECONDS = 300


def own_name(row, identity):
    if row["killer"] == identity:
        return row["killer_name"]
    return row["victim_name"] if row["victim"] == identity else ""


def kill_stats(identity, rows):
    """Totals and the hard checks for one account's kills and deaths."""
    kills = [r for r in rows if r["killer"] == identity and r["victim"] != identity]
    bullets = [r for r in kills if r["damage"] == "KINETIC" and r["kind"] == "kill"]
    measured = [r for r in bullets if r["distance"]]
    deaths = [r for r in rows if r["victim"] == identity and r["killer"] != identity]
    heads = sum(r["zone"] == "Head" for r in bullets)

    switches = []
    ordered = sorted(bullets, key=lambda r: r["at"])
    for first, second in zip(ordered, ordered[1:]):
        if (second["at"] - first["at"] <= SWITCH_SECONDS and first["distance"] and second["distance"]
                and abs(first["distance"] - second["distance"]) >= SWITCH_METRES):
            switches.append((first, second))

    burst, burst_rows = 0, []
    for i, row in enumerate(ordered):
        window = [r for r in ordered[i:] if r["at"] - row["at"] <= BURST_SECONDS]
        if len(window) > burst:
            burst, burst_rows = len(window), window

    names = Counter(own_name(r, identity) for r in rows if own_name(r, identity) not in ("", "?"))
    return {
        "kills": len(kills), "deaths": len(deaths),
        "teamkills": sum(r["kind"] == "teamkill" for r in kills),
        "kd": len(kills) / max(len(deaths), 1),
        "bullets": len(bullets), "heads": heads,
        "head_share": heads / len(bullets) if bullets else 0.0,
        "long": sum((r["distance"] or 0) >= LONG for r in measured),
        "longest": max((r["distance"] for r in measured), default=0),
        "long_heads": sum((r["distance"] or 0) >= LONG and r["zone"] == "Head" for r in measured),
        "switches": switches, "burst": burst, "burst_rows": burst_rows,
        "names": [n for n, _ in names.most_common()],
        "first": min((r["at"] for r in rows), default=None), "last": max((r["at"] for r in rows), default=None),
    }


def together(rows_a, rows_b, a, b):
    """Times both accounts did something within a few minutes of each other: two
    people at once, so not one person swapping accounts."""
    times_b = sorted(r["at"] for r in rows_b if a not in (r["killer"], r["victim"]))
    found, j = [], 0
    for at in sorted(r["at"] for r in rows_a if b not in (r["killer"], r["victim"])):
        while j < len(times_b) and times_b[j] < at - TOGETHER_SECONDS:
            j += 1
        if j < len(times_b) and abs(times_b[j] - at) <= TOGETHER_SECONDS:
            if not found or at - found[-1] > 3600:
                found.append(at)
    return found


def findings(subject, accounts, unusual, sus_lines, incidents):
    """What stands out, strongest first."""
    out = []
    for acct in accounts:
        s, who = acct["stats"], acct["label"]
        if len(s["switches"]) >= 1:
            out.append({"level": STRONG if len(s["switches"]) >= 2 else MODERATE, "account": who,
                        "title": f"{len(s['switches'])} bullet kill{'s' if len(s['switches']) != 1 else ''} at very different "
                                 f"ranges within {SWITCH_SECONDS} seconds of each other",
                        "why": "A close kill and a long-range kill a second apart means aiming at two places at once. "
                               "Players can't do this; aimbots that snap between targets do.",
                        "pairs": s["switches"]})
        if s["burst"] >= BURST_KILLS:
            out.append({"level": STRONG if s["burst"] >= BURST_KILLS + 2 else MODERATE, "account": who,
                        "title": f"{s['burst']} kills inside {BURST_SECONDS} seconds",
                        "why": "Several separate targets dropped faster than most players can find them.",
                        "rows": s["burst_rows"]})
        high = unusual.get(acct["identity"])
        if high and high["high"]:
            labels = ", ".join(high["labels"])
            out.append({"level": MODERATE if len(high["high"]) >= 2 else NOTE, "account": who,
                        "title": f"Top 5% of all players for {labels}",
                        "why": "Compared with everyone with enough kills on these servers in this period. Good "
                               "players land here too; several at once is what stands out.",
                        "compare": high})
        if s["long_heads"] >= 3:
            out.append({"level": MODERATE, "account": who,
                        "title": f"{s['long_heads']} headshot kills from {LONG} m or more",
                        "why": "Possible with a scope and patience; a lot of them alongside close-range sprees is not usual."})
        if (acct.get("relation") or {}).get("steam"):
            out.append({"level": MODERATE, "account": who, "title": "Same Steam account as this player",
                        "why": "Proof it's the same person on two Reforger accounts, not just someone in the same house."})
        if acct["banned"] and acct["identity"] != subject:
            out.append({"level": MODERATE, "account": who, "title": "This linked account is banned",
                        "why": "If they're the same person, playing on this account is ban evasion."})
    if sus_lines:
        out.append({"level": NOTE, "account": "", "title": f"{len(sus_lines)} Sus alert{'s' if len(sus_lines) != 1 else ''} "
                    "name these accounts", "why": "The live checks fired on them; listed below.", "lines": sus_lines})
    if incidents:
        out.append({"level": NOTE, "account": "", "title": f"Online at {len(incidents)} suspicious incident"
                    f"{'s' if len(incidents) != 1 else ''}",
                    "why": "Bursts of explosions credited to AI or script-error spikes while they were on."})
    order = {STRONG: 0, MODERATE: 1, NOTE: 2}
    out.sort(key=lambda f: order[f["level"]])
    return out


def verdict(found):
    strong = sum(f["level"] == STRONG for f in found)
    moderate = sum(f["level"] == MODERATE for f in found)
    if strong:
        return STRONG, "Strong signs of cheating", ("At least one thing in here a person can't do. Look at the kills "
                                                    "under each finding before you ban.")
    if moderate >= 2:
        return MODERATE, "Suspicious: worth spectating", "Several things stand out, none impossible on its own."
    if moderate:
        return MODERATE, "One thing stands out", "Could be a good player. Spectate if you're unsure."
    return NOTE, "Nothing stands out", "Nothing in these kills is beyond a good player."


def relationship(subject, other, rows, shared, mine=None, theirs=None):
    """How a linked account relates to the subject: what they share and whether
    they were ever on together."""
    overlap = together(rows[subject], rows[other], subject, other)
    steam = mine and theirs and mine["steam_id"] and mine["steam_id"] == theirs["steam_id"]
    if steam:
        reading = (f"Same Steam account ({mine['steam_id']}): the same person, on more than one "
                   "Reforger account.")
    elif overlap:
        reading = (f"Active at the same time on {len(overlap)} occasion{'s' if len(overlap) != 1 else ''}: "
                   "two people, likely the same house.")
    elif rows[other] and rows[subject]:
        reading = "Never active at the same time: fits one person swapping accounts."
    else:
        reading = "Not enough kills on one side to tell whether they're ever on together."
    return {"shared": shared, "overlap": overlap, "reading": reading, "steam": bool(steam)}


def report_text(subject_name, level_title, found, accounts):
    lines = [f"Investigation: {subject_name}", f"Verdict: {level_title}", ""]
    for f in found:
        lines.append(f"[{f['level'].upper()}] {f['account'] + ': ' if f['account'] else ''}{f['title']}")
    lines.append("")
    for a in accounts:
        s = a["stats"]
        lines.append(f"{a['label']} ({a['identity']}): {plural(s['kills'], 'kill')}, {plural(s['deaths'], 'death')}, "
                     f"{round(100 * s['head_share'])}% headshots, longest {s['longest']:.0f} m"
                     + (f", names: {', '.join(s['names'])}" if s["names"] else ""))
    return "\n".join(lines)


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"
