"""Rotations: game setups kept as autosaves, loaded one after another at each game over.

A rotation is a copy of one autosave taken right after an admin set the game up with GM: points moved, kill feed
and other GM settings on, and the MOBs it picked. The game restores the newest save in its folder when it
starts, so loading a rotation is: stop the server, leave that copy as the only save, start it again.

The game deletes every save of a game when it ends, so the copies live in the panel's own folder."""
import json
import shutil
from pathlib import Path

from . import campaign_save, mobs
from .save_history import military_base, parse
from .save_world import faction, records, split

# A place this far from a spot is still named as near it.
NEAR = 1500
# The postgame reset script on the live box: at game over it stops the server and starts it again. With the rotation
# line added it puts the profile's rotation-next folder in as the game's save folder first.
RESET_SCRIPT = Path("/usr/local/sbin/reforger-postgame-reset.sh")
STAGE = "rotation-next"


def game_folder(log_dir):
    """The game's own save folder for a server: .save/game, one folder per mission inside."""
    return Path(log_dir).parent / "profile" / ".save" / "game"


def stage_folder(log_dir):
    """Where the reset script picks up the next rotation: rotation-next beside the save folder, laid out like it."""
    return Path(log_dir).parent / STAGE


def reset_script(path=None):
    """"" when there's no postgame reset script, "ready" when it copies rotation-next in, "old" when it doesn't yet."""
    path = Path(path or RESET_SCRIPT)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "ready" if STAGE in text else "old"


def staged(log_dir):
    """The id of the rotation waiting in rotation-next, or None."""
    try:
        return int(json.loads((stage_folder(log_dir) / "rotation.json").read_text(encoding="utf-8"))["id"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def stage(log_dir, rotation_id, folder, mission):
    """Put a rotation in rotation-next, for the reset script to copy in at the next game over."""
    points = [p for p in Path(folder).iterdir() if p.is_dir() and p.name.startswith("savepoint")]
    if not points:
        raise FileNotFoundError(f"The rotation's copy is missing from {folder}.")
    target = stage_folder(log_dir)
    unstage(log_dir)
    shutil.copytree(points[0], target / "game" / mission / "playthrough000" / points[0].name)
    (target / "rotation.json").write_text(json.dumps({"id": rotation_id}), encoding="utf-8")


def unstage(log_dir):
    shutil.rmtree(stage_folder(log_dir), ignore_errors=True)


def loaded(log_dir, folder, mission):
    """Whether the game's save folder now holds the rotation's save, so the reset script copied it in."""
    points = [p.name for p in Path(folder).iterdir() if p.is_dir() and p.name.startswith("savepoint")]
    return bool(points) and (game_folder(log_dir) / mission / "playthrough000" / points[0]).is_dir()


def newest_savepoint(log_dir):
    """(savepoint folder, mission folder name) of the newest autosave, or None."""
    found = campaign_save.newest(log_dir)
    if not found:
        return None
    point = found[0].parent.parent
    return point, point.parent.parent.name


def nearest(x, z, places):
    best = min(places, key=lambda p: (p["x"] - x) ** 2 + (p["z"] - z) ** 2, default=None)
    if best and (best["x"] - x) ** 2 + (best["z"] - z) ** 2 <= NEAR ** 2:
        return best["name"]
    return f"{round(x)}, {round(z)}"


def describe(blob):
    """{"mobs": [...], "moved": [...]} for a save: where each side's MOB is and the points moved off their spot,
    each named by the nearest place on the map."""
    data = Path(blob).read_bytes()
    bases, where = [], {}
    for section, record in records(data):
        if section not in ("MilitaryBase", "Structure"):
            continue
        rid, value, parts = split(record)
        got = parse(value, parts, (faction, military_base) if section == "MilitaryBase" else ())
        if section == "Structure":
            if got.get("pos"):
                where[rid] = got["pos"]
        else:
            bases.append(got)
    places = mobs.places()
    mob_list, moved = [], []
    for b in bases:
        if b.get("hq") and where.get(b.get("building")):
            pos = where[b["building"]]
            mob_list.append(f"{b.get('faction') or '?'} at {nearest(pos[0], pos[2], places)}")
        if b.get("pos"):
            moved.append(f"near {nearest(b['pos'][0], b['pos'][2], places)}")
    return {"mobs": sorted(mob_list), "moved": sorted(moved)}


def capture(log_dir, dest):
    """Copy the newest autosave into `dest`. Returns (mission folder name, description)."""
    found = newest_savepoint(log_dir)
    if not found:
        raise FileNotFoundError("The server has no autosave yet. Wait for one, then try again.")
    point, mission = found
    blobs = sorted((point / "WorldState").glob("*.blob"))
    if not blobs:
        raise FileNotFoundError("The newest autosave has no world file.")
    about = describe(blobs[0])
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(point, dest / point.name)
    (dest / "about.json").write_text(json.dumps({"mission": mission, **about}), encoding="utf-8")
    return mission, about


def install(log_dir, folder, mission):
    """Leave the rotation's copy as the only save of its mission, so the game restores it when it starts.
    Only call this with the server stopped."""
    points = [p for p in Path(folder).iterdir() if p.is_dir() and p.name.startswith("savepoint")]
    if not points:
        raise FileNotFoundError(f"The rotation's copy is missing from {folder}.")
    target = game_folder(log_dir) / mission
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(points[0], target / "playthrough000" / points[0].name)

