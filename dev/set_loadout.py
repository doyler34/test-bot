"""Give one player a different saved arsenal loadout in a server's newest autosave, for testing.

    python3 dev/set_loadout.py INSTANCE IDENTITY --check     # read only: what it would do
    python3 dev/set_loadout.py INSTANCE IDENTITY             # do it (the server must be stopped)
    python3 dev/set_loadout.py INSTANCE IDENTITY --undo      # put the save back as it was

INSTANCE is the AMP instance name, e.g. Arland01. The new loadout is a random one from another player on
the same side in that save. The game only reads the save when the server starts, so: stop the server, run
this, start the server. The player must already have a saved loadout in that save for it to be swapped."""
import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panel import campaign_save, loadouts, save_edit  # noqa: E402

AMP = Path("/home/amp/.ampdata/instances")


def game_dir(instance):
    return AMP / instance / "arma-reforger" / "1874900"


def running(instance):
    """True while that instance's game server is running."""
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            exe = (proc / "exe").resolve()
        except OSError:
            continue
        if exe.name == "ArmaReforgerServer" and str(exe).startswith(str(AMP / instance) + "/"):
            return True
    return False


def describe(loadout, names):
    items = loadouts.tree(loadout, names)
    return f"{loadouts.title(items)} ({sum(1 for _ in loadouts.flat(items))} items)"


def main(instance, identity, mode):
    folder = game_dir(instance)
    found = campaign_save.newest(str(folder / "AReforgerMaster" / "logs"))
    if not found:
        sys.exit(f"No autosave found for {instance}.")
    blob = found[0]
    backup = blob.with_name(blob.name + ".before-loadout")
    if mode == "undo":
        if running(instance):
            sys.exit("Stop the server first.")
        if not backup.exists():
            sys.exit(f"No backup next to {blob}.")
        shutil.copy2(backup, blob)
        print(f"Put back {blob} from {backup.name}.")
        return
    data = blob.read_bytes()
    people = loadouts.players(data)
    me = people.get(identity)
    print(f"Save: {blob}")
    if not me:
        sys.exit(f"{identity} isn't in this save. Join that server and play until the next autosave.")
    if not me["loadout"]:
        sys.exit("You have no saved loadout in this save. Save any loadout at an arsenal, wait for an autosave, try again.")
    names = loadouts.names(loadouts.pak_dir(folder / "AReforgerMaster" / "logs"), Path.home() / ".oyb-prefab-names.json")
    side = me["side"]
    others = [(who, p["loadout"]) for who, p in people.items()
              if who != identity and p["loadout"] and p["side"] == side and p["loadout"] != me["loadout"]]
    if not others:
        sys.exit(f"No other {side} player in this save has a saved loadout to copy.")
    # The same pick for --check and the real run on the same save.
    donor, loadout = random.Random(f"{identity}:{found[1]}").choice(sorted(others, key=lambda o: o[0]))
    loadout = dict(loadout, faction=me["loadout"].get("faction", side))
    print(f"You ({side}) have now: {describe(me['loadout'], names)}")
    print(f"You'd get, from player {donor}: {describe(loadout, names)}")
    if mode == "check":
        print("Read only: nothing changed.")
        return
    if running(instance):
        sys.exit("The server is running. Stop it in AMP first: the game would just overwrite the save.")
    changed = save_edit.set_loadout(data, identity, json.dumps(loadout, separators=(",", ":")).encode())
    if loadouts.players(changed)[identity]["loadout"] != loadout:
        sys.exit("The edited save didn't read back right; nothing written.")
    if not backup.exists():
        shutil.copy2(blob, backup)
    blob.write_bytes(changed)
    print(f"Done. Backup kept as {backup.name}. Start the server, join, and check your saved loadout at an arsenal.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    main(args[0], args[1].lower(), "check" if "--check" in sys.argv else "undo" if "--undo" in sys.argv else "do")
