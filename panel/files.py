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


KVP = "GenericModule.kvp"
SETTINGS_LINE = "App.AppSettings="


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
