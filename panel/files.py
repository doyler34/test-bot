"""The servers' folders for the file manager and the mods page.

Every path a person sends is resolved and checked to sit inside one AMP
instance's folder before anything is read or written, so "..", absolute
paths and symlinks can't reach anywhere else. The panel runs as root and AMP
as amp, so anything new is handed to whoever owns the folder it lands in.
"""

import json
import os
import re
import shutil
import time
from pathlib import Path

DEFAULT_ROOT = "/home/amp/.ampdata/instances"
MAX_EDIT = 2 * 1024 * 1024
MAX_UPLOAD = 500 * 1024 * 1024
MOD_FOLDER = re.compile(r"^(.+)_([0-9A-F]{16})$")
MOD_ID = re.compile(r"^[0-9A-F]{16}$")


class FileError(Exception):
    pass


def instances_root(servers):
    """The folder AMP keeps every instance in, read off any server's log folder."""
    for server in servers:
        parts = [p for p in str(server.log_dir).split("/") if p]
        if "instances" in parts:
            return Path("/" + "/".join(parts[:parts.index("instances") + 1]))
    return Path(DEFAULT_ROOT)


def is_game(folder):
    """A game server's instance, not AMP's own controller (ADS), which has no game settings."""
    return (folder / KVP).is_file()


def instances(root):
    try:
        return sorted(p.name for p in Path(root).iterdir() if p.is_dir() and not p.name.startswith(".") and is_game(p))
    except OSError:
        return []


def resolve(root, instance, rel=""):
    """(instance folder, the path asked for) once it's known to be inside that folder."""
    if not instance or "/" in instance or instance.startswith("."):
        raise FileError("No such server folder.")
    base = (Path(root) / instance).resolve()
    if base.parent != Path(root).resolve() or not base.is_dir() or not is_game(base):
        raise FileError("No such server folder.")
    target = (base / rel.strip("/")).resolve()
    if target != base and not target.is_relative_to(base):
        raise FileError("That's outside this server's folder.")
    return base, target


def relative(base, path):
    return "" if path == base else str(path.relative_to(base))


def listing(path):
    entries = []
    for child in path.iterdir():
        try:
            info = child.stat()
        except OSError:
            continue
        entries.append({"name": child.name, "dir": child.is_dir(), "size": info.st_size, "modified": int(info.st_mtime)})
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    return entries


def read_text(path):
    """The file as text, or None when it's too big or not text."""
    if path.stat().st_size > MAX_EDIT:
        return None
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def hand_over(path):
    """Give a new file or folder to whoever owns the folder it's in."""
    parent = path.parent.stat()
    try:
        os.chown(path, parent.st_uid, parent.st_gid)
    except PermissionError:
        pass


def write_text(path, text):
    new = not path.exists()
    # Written in place so an existing file keeps its owner and permissions.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if new:
        hand_over(path)


def make_folder(path):
    if path.exists():
        raise FileError(f"{path.name} already exists.")
    path.mkdir()
    hand_over(path)


def rename(path, name):
    if not name or "/" in name or name in (".", ".."):
        raise FileError("Pick a name without slashes.")
    target = path.with_name(name)
    if target.exists():
        raise FileError(f"{name} already exists.")
    path.rename(target)
    return target


def delete(base, path):
    if path == base:
        raise FileError("The server's own folder can't be deleted here.")
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


async def save_upload(folder, name, chunks):
    """Stream an upload into the folder; returns its size."""
    if not name or "/" in name or name in (".", ".."):
        raise FileError("That upload has no usable file name.")
    target = folder / name
    partial = folder / f".{name}.uploading-{int(time.time())}"
    size = 0
    try:
        with open(partial, "wb") as f:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise FileError(f"Uploads stop at {MAX_UPLOAD // 1024 // 1024} MB.")
                f.write(chunk)
        existed = target.exists()
        if existed:
            # Keep the old file's owner on the new one.
            info = target.stat()
            os.replace(partial, target)
            try:
                os.chown(target, info.st_uid, info.st_gid)
            except PermissionError:
                pass
        else:
            os.replace(partial, target)
            hand_over(target)
    finally:
        if partial.exists():
            partial.unlink()
    return size


# mods

def parse_mods(value):
    """AMP keeps the mod list as the JSON objects that go between the brackets of
    serverconfig.json's "mods": [ ... ]; anything that isn't one is skipped."""
    mods = []
    for raw in re.findall(r"\{[^{}]*\}", value or ""):
        try:
            mod = json.loads(raw)
        except ValueError:
            continue
        if isinstance(mod, dict) and mod.get("modId"):
            mods.append({k: str(v) for k, v in mod.items()})
    return mods


def format_mods(mods):
    return ",\n".join(json.dumps(m) for m in mods)


def amp_running(base, proc="/proc"):
    """Whether this instance's own AMP is running: a program started from inside its folder."""
    for entry in Path(proc).iterdir():
        if not entry.name.isdigit():
            continue
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            continue
        if exe.startswith(f"{base}/") and Path(exe).name.startswith("AMP_"):
            return True
    return False


def provision_from(base):
    """How a server was made (its game template), read from its settings file, so a
    new one can be made the same way."""
    keep = ("ConfigManifest", "MetaConfigManifest", "ConfigRoot", "AppConfigId", "DisplayName",
            "DisplayImageSource", "OriginalSource")
    found = {}
    for line in (base / KVP).read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.startswith("Meta.") and key[5:] in keep and value:
            found[f"GenericModule.{key}"] = value
    if "GenericModule.Meta.ConfigManifest" not in found:
        raise FileError(f"{base.name} doesn't say which game template it uses.")
    return found


def used_ports(root):
    """AMP web ports the instances already have, from each one's AMPConfig.conf."""
    ports = set()
    for conf in Path(root).glob("*/AMPConfig.conf"):
        try:
            for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Webserver.Port="):
                    ports.add(int(line.split("=", 1)[1]))
        except (OSError, ValueError):
            continue
    return ports


INSTANCE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,30}$")
BACKUPS = "deleted-servers"
# What a deleted server's backup leaves out: the game itself and downloaded mods,
# which come back by downloading them again.
SKIP_IN_BACKUP = ("arma-reforger/1874900/addons", "AReforgerMaster/addons", "steamapps")


def backup(root, base):
    """Pack up everything worth keeping from a server before it's deleted.
    Returns the backup's path and size."""
    import tarfile
    folder = Path(root).parent / BACKUPS
    folder.mkdir(exist_ok=True)
    target = folder / f"{base.name}-{time.strftime('%Y-%m-%d_%H-%M-%S')}.tar.gz"

    def keep(info):
        rel = info.name.split("/", 1)[1] if "/" in info.name else ""
        if any(rel == s or rel.startswith(s + "/") or f"/{s}/" in f"/{rel}/" for s in SKIP_IN_BACKUP):
            return None
        if info.isfile() and info.size > 512 * 1024 * 1024:
            return None
        return info

    with tarfile.open(target, "w:gz") as tar:
        tar.add(base, arcname=base.name, filter=keep)
    hand_over(target)
    return target, target.stat().st_size


SAVE_BACKUPS = "save-backups"


def save_folder(base):
    """The game's .save folder for this server, or None when it has no save yet."""
    found = sorted(Path(base).glob("arma-reforger/*/*/profile/.save"))
    return found[0] if found else None


def new_game(root, base):
    """Put this server's save aside so its next start is a fresh game. The save is moved
    to a folder of its own, not deleted. Returns where it went, or None if there was none."""
    save = save_folder(base)
    if save is None:
        return None
    folder = Path(root).parent / SAVE_BACKUPS / Path(base).name / time.strftime("%Y-%m-%d_%H-%M-%S")
    folder.mkdir(parents=True)
    for made in (folder.parent.parent, folder.parent, folder):
        hand_over(made)
    target = folder / save.name
    shutil.move(str(save), str(target))
    return target


def shortcuts(base):
    """[(label, path)] to the files people open most, where this server has them."""
    found = []
    for label, pattern in (("Server config (serverconfig.json)", "*/*/Configs/serverconfig.json"),
                           ("Downloaded mods", "*/*/*/addons"), ("Game logs", "*/*/*/logs"),
                           ("AMP's settings for this server", "GenericModule.kvp")):
        hits = sorted(base.glob(pattern))
        if hits:
            found.append((label, relative(base, hits[0])))
    logs = list(base.glob("*/*/*/logs/logs_*"))
    if logs:
        found.insert(3, ("Newest game log folder", relative(base, max(logs, key=lambda p: p.stat().st_mtime))))
    return found


def latest_console(base, tail=64 * 1024):
    """(path, last lines) of the game's newest console.log, or (None, "")."""
    logs = list(base.glob("*/*/*/logs/logs_*/console.log"))
    if not logs:
        return None, ""
    path = max(logs, key=lambda p: p.stat().st_mtime)
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - tail))
        data = f.read()
    text = data.decode("utf-8", "replace")
    if len(data) == tail:
        text = text.split("\n", 1)[-1]
    return path, text


def game_process(base, proc="/proc"):
    """{pid, memory (bytes), up (seconds)} for this server's running game, or None."""
    try:
        boot = time.time() - float(Path(proc, "uptime").read_text().split()[0])
        ticks = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None
    for entry in Path(proc).iterdir():
        if not entry.name.isdigit():
            continue
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            continue
        if not (exe.startswith(f"{base}/") and Path(exe).name.startswith("ArmaReforgerServer")):
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            rss = next((int(line.split()[1]) * 1024 for line in (entry / "status").read_text().splitlines()
                        if line.startswith("VmRSS:")), 0)
        except (OSError, ValueError, IndexError):
            continue
        return {"pid": int(entry.name), "memory": rss, "up": max(0, int(time.time() - boot - int(fields[19]) / ticks))}
    return None


KVP = "GenericModule.kvp"
MANIFEST = "configmanifest.json"
SETTINGS_LINE = "App.AppSettings="


def game_fields(base):
    """Every game setting the instance has, by field name, from its own manifest."""
    try:
        found = json.loads((base / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f["FieldName"] for f in found if isinstance(f, dict) and f.get("FieldName")
            and str(f.get("InputType", "")).lower() != "hidden"]


def game_settings(base):
    """Every game setting this server has saved, by field name, from its settings file."""
    for line in (base / KVP).read_text(encoding="utf-8").splitlines():
        if line.startswith(SETTINGS_LINE):
            found = json.loads(line[len(SETTINGS_LINE):])
            return {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in found.items()}
    return {}


HEADER_KEYS = {"xp": "m_fXpMultiplier", "supplies": "m_iStartingHQSupplies", "points": "m_iControlPointsCap",
               "victory": "m_fVictoryTimeout"}


def header_get(header, key):
    """One value from a mission header ("m_fXpMultiplier": 3, ...), or ""."""
    found = re.search(r'"%s"\s*:\s*"?([^",\n]*)' % re.escape(key), header or "")
    return found.group(1).strip() if found else ""


def header_set(header, key, value):
    """The mission header with one value changed, or added at the top when it's not there."""
    header = header or ""
    pattern = r'("%s"\s*:\s*)"?[^",\n]*"?' % re.escape(key)
    if re.search(pattern, header):
        return re.sub(pattern, lambda m: m.group(1) + value, header, count=1)
    body = header.strip()
    return f'"{key}": {value}' + (",\n" + body if body else "")


def args_get(args):
    """(US, USSR, FIA player limits, XP multiplier) from the extra startup line."""
    limits = dict(re.findall(r"(US|USSR|FIA):(\d+)", (re.search(r"-playerLimits[=:\s]+(\S+)", args or "") or [None, ""])[1]))
    xp = re.search(r"-fXpMultiplier[=:\s]+([0-9.]+)", args or "")
    return limits.get("US", ""), limits.get("USSR", ""), limits.get("FIA", ""), xp.group(1) if xp else ""


def args_set(args, us, ussr, fia, xp):
    """The extra startup line with player limits and XP set, everything else kept."""
    args = args or ""
    if us or ussr or fia:
        limits = f"-playerLimits=US:{us or 0},USSR:{ussr or 0},FIA:{fia or 0}"
        args = re.sub(r"-playerLimits[=:\s]+\S+", limits, args) if "-playerLimits" in args else f"{limits} {args}".strip()
    if xp:
        if re.search(r"-fXpMultiplier[=:\s]+[0-9.]+", args):
            args = re.sub(r"-fXpMultiplier[=:\s]+[0-9.]+", f"-fXpMultiplier={xp}", args)
        else:
            args = f"{args} -fXpMultiplier={xp}".strip()
    return args


def scenarios(base):
    """{id: name} for the scenario dropdown, from the server's manifest."""
    try:
        for field in json.loads((base / MANIFEST).read_text(encoding="utf-8")):
            if isinstance(field, dict) and field.get("FieldName") == "scenarioId":
                return {str(k): str(v) for k, v in (field.get("EnumValues") or {}).items()}
    except (OSError, ValueError):
        pass
    return {}


_MAPS = {}


def map_name(base):
    """The island a server is set to play ("Conflict - Everon" gives "Everon"), from its
    saved scenario. Empty when it has none, or one the manifest doesn't name."""
    try:
        stamp = (Path(base) / KVP).stat().st_mtime
    except OSError:
        return ""
    hit = _MAPS.get(str(base))
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        name = scenarios(Path(base)).get(game_settings(Path(base)).get("scenarioId", ""), "")
    except (OSError, ValueError):
        name = ""
    island = name.rsplit(" - ", 1)[-1].strip() if name else ""
    _MAPS[str(base)] = (stamp, island)
    return island


def server_map(config):
    """The map for a panel server, found through the AMP instance its logs live in."""
    parts = [p for p in str(config.log_dir).split("/") if p]
    name = config.amp_instance
    if not name and "instances" in parts and parts.index("instances") + 1 < len(parts):
        name = parts[parts.index("instances") + 1]
    return map_name(instances_root([config]) / name) if name else ""


def server_listing(config):
    """Max players and the join address as the server's own serverconfig.json gives them."""
    parts = [p for p in str(config.log_dir).split("/") if p]
    name = config.amp_instance
    if not name and "instances" in parts and parts.index("instances") + 1 < len(parts):
        name = parts[parts.index("instances") + 1]
    if not name:
        return {}
    hits = sorted((instances_root([config]) / name).glob("*/*/Configs/serverconfig.json"))
    if not hits:
        return {}
    found = json.loads(hits[0].read_text(encoding="utf-8"))
    game = found.get("game") if isinstance(found.get("game"), dict) else {}
    slots = game.get("maxPlayers")
    host = str(found.get("publicAddress") or config.query_host or "").strip()
    port = found.get("publicPort") or found.get("bindPort")
    return {"slots": slots if isinstance(slots, int) and slots > 0 else None,
            "address": f"{host}:{port}" if host and isinstance(port, int) and port > 0 else ""}


def kvp_mods(base):
    """The mod list straight from the instance's settings file, for when its AMP is off."""
    for line in (base / KVP).read_text(encoding="utf-8").splitlines():
        if line.startswith(SETTINGS_LINE):
            return str(json.loads(line[len(SETTINGS_LINE):]).get("mods") or "")
    raise FileError(f"No game settings in {KVP}.")


def set_kvp_mods(base, change):
    """Change the mod list in the settings file. Only safe while that instance's AMP
    is stopped: a running one keeps its own copy and writes it back."""
    path = base / KVP
    lines = path.read_text(encoding="utf-8").split("\n")
    for n, line in enumerate(lines):
        if line.startswith(SETTINGS_LINE):
            settings = json.loads(line[len(SETTINGS_LINE):])
            settings["mods"] = change(str(settings.get("mods") or ""))
            lines[n] = SETTINGS_LINE + json.dumps(settings, separators=(",", ":"), ensure_ascii=False)
            write_text(path, "\n".join(lines))
            return settings["mods"]
    raise FileError(f"No game settings in {KVP}.")


def downloaded_mods(base):
    """Mods already on disk for this server: {mod ID: folder name}."""
    found = {}
    for addons in base.glob("*/*/*/addons"):
        try:
            children = list(addons.iterdir())
        except OSError:
            continue
        for child in children:
            match = MOD_FOLDER.match(child.name)
            if match and child.is_dir():
                found[match.group(2)] = {"name": match.group(1), "path": relative(base, child)}
    return found
