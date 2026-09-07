"""Interactive bot-only configuration; run as the actual systemd service user."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import getpass
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from notification_config import read_servers, load_notification_config
from config import ConfigError


@contextmanager
def environment(values):
    old = os.environ.copy()
    os.environ.clear()
    os.environ.update({k: str(v) for k, v in values.items() if v is not None})
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


def log_path(raw):
    """Only accept readable Reforger rotation structures, never arbitrary logs."""
    path = Path(raw).expanduser().resolve(strict=True)
    if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
        raise ValueError("Log directory is not readable by the bot service account")
    logs = [p for p in path.glob("logs_*/console.log")
            if re.fullmatch(r"logs_\d{4}-\d{2}-\d{2}[_-].+", p.parent.name)]
    if not logs:
        raise ValueError("Expected logs_DATE/console.log inside this directory; start Reforger once or select its logs folder")
    latest = max(logs, key=lambda p: p.stat().st_mtime)
    with latest.open("rb") as stream:
        sample = stream.read(131072)
        stream.seek(max(0, latest.stat().st_size - 131072))
        sample += stream.read(131072)
    if not any(marker in sample for marker in (b"Arma Reforger", b"ArmaReforger", b"BACKEND", b"ENGINE", b"WORLD", b"RESOURCES", b"Session state", b"FPS:")):
        raise ValueError("Log structure found, but no Reforger engine evidence in the recent console log")
    return path


def process_roots(proc=Path("/proc")):
    roots = []
    for entry in proc.glob("[0-9]*"):
        try:
            args = (entry / "cmdline").read_bytes().decode(errors="replace").split("\0")
            if not args or Path(args[0]).name != "ArmaReforgerServer":
                continue
            cwd = (entry / "cwd").resolve(strict=True)
            roots.append(cwd)
            for i, arg in enumerate(args):
                profile = args[i+1] if arg == "-profile" and i+1 < len(args) else (
                    arg.split("=", 1)[1] if arg.startswith("-profile=") else None)
                if profile:
                    p = Path(profile)
                    roots.append(p if p.is_absolute() else cwd / p)
        except (OSError, ValueError):
            continue
    return roots


def discover(roots=None, proc=Path("/proc"), max_dirs=15000):
    roots = list(roots) if roots is not None else process_roots(proc) + [Path(p) for p in ("/home", "/root", "/opt", "/srv", "/var/lib")]
    found, visited = set(), set()
    for root in roots:
        for directory, dirs, files in os.walk(root, followlinks=False):
            path = Path(directory)
            if path in visited:
                dirs[:] = []
                continue
            visited.add(path)
            if len(visited) > max_dirs:
                return sorted(found, key=str)
            dirs[:] = [d for d in dirs if d not in (".git", ".venv", "node_modules", "steamapps", "docker", "containers") and not d.startswith(".")]
            if len(path.relative_to(root).parts) >= 7:
                dirs[:] = []
            if any(d.startswith("logs_") for d in dirs):
                try:
                    found.add(log_path(path))
                except (OSError, ValueError):
                    pass
                dirs[:] = [d for d in dirs if not d.startswith("logs_")]
    return sorted(found, key=str)


def ask(label, default=""):
    value = input(f"{label}" + (f" [{default}]" if default != "" else "") + ": ").strip()
    return value or str(default)


def token_input(existing=""):
    if existing and ask("Reuse saved Discord token? Y/n", "Y").lower() == "y":
        return existing
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        token = getpass.getpass("Discord Bot Token (hidden): ").strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("A non-empty Discord token without whitespace is required")
    return token


def select_log(candidates, existing="", used=()):
    while True:
        for n, path in enumerate(candidates, 1):
            print(f"  {n}) {path}")
        print("  M) Enter path manually")
        if existing:
            print(f"  K) Keep {existing}")
        choice = ask("Select logs", "K" if existing else ("1" if len(candidates) == 1 else "M"))
        try:
            raw = existing if choice.upper() == "K" and existing else (
                ask("Local logs directory") if choice.upper() == "M" else candidates[int(choice)-1] if 1 <= int(choice) <= len(candidates) else "")
            path = log_path(raw)
            if path in used:
                raise ValueError("That log directory is already assigned to another active server")
            return str(path)
        except (OSError, ValueError, IndexError) as exc:
            print(f"Cannot use those logs: {exc}")


def make_servers(existing, defaults, count, paths, names):
    servers = deepcopy(existing or defaults)
    for i, server in enumerate(servers):
        server["enabled"] = i < count
        # Local log-only mode: never generate A2S configuration.
        server.pop("a2s_host", None)
        server.pop("a2s_port", None)
        if i < count:
            server["log_dir"], server["name"] = paths[i], names[i]
            if not existing or (not existing[i].get("enabled") and existing[i]["settings"] == defaults[i]["settings"] and existing[i]["rules"] == defaults[i]["rules"]):
                server["settings"] = defaults[0]["settings"]
                server["rules"] = defaults[0]["rules"]
        else:
            server.setdefault("log_dir", "")
    return servers


def env_text(values):
    # python-dotenv single-quoted syntax, preserving literal secrets safely.
    return "".join(k + "='" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'\n"
                   for k, v in values.items() if v is not None)


def render_unit(repo, user):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", user):
        raise ValueError("Unsupported systemd user name")
    def quote(value, executable=False):
        if any(c in str(value) for c in ("\n", "\r", "\0")):
            raise ValueError("Unsupported checkout path")
        value = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        if executable:
            value = value.replace("$", "$$")
        return '"' + value + '"'
    return (ROOT / "deploy/reforger-timer.service.tpl").read_text().replace("@USER@", user).replace("@REPO_DIR@", quote(repo)).replace("@PYTHON@", quote(PurePosixPath(repo) / ".venv/bin/python", True))


def render_cli(repo):
    import shlex
    return '''#!/usr/bin/env bash
# OYB management helper
set -euo pipefail
SUDO=()
if [ "$(id -u)" -ne 0 ]; then SUDO=(sudo); fi
case "${1:-status}" in
  status) exec "${SUDO[@]}" systemctl status reforger-timer --no-pager ;;
  logs) exec "${SUDO[@]}" journalctl -u reforger-timer -n 60 -f ;;
  restart) exec "${SUDO[@]}" systemctl restart reforger-timer ;;
  setup) exec bash ''' + shlex.quote(str(PurePosixPath(repo) / "deploy/setup.sh")) + ''' ;;
  *) echo "Usage: oyb {status|logs|restart|setup}" >&2; exit 2 ;;
esac
'''


def summary():
    values = dotenv_values(ROOT / ".env", interpolate=False)
    with environment(values):
        config = load_notification_config(load_env_file=False)
    print("\n================================\nOYB BOT SETUP COMPLETE\n================================")
    for name in ("Bot service: Running", "Playtime tracking", "XP / ranks", "Account linking", "Notifications"):
        print(f"  ✓ {name}")
    for server in config.servers:
        print(f"\n{server.name}\n  Status: " + ("Enabled\n  Logs: " + server.log_dir if server.enabled else "Disabled / Coming Soon"))
    print("\nUseful commands: oyb status | oyb logs | oyb restart | oyb setup")


def validate(values, config_path):
    with environment({**values, "SERVERS_CONFIG": str(config_path)}):
        config = load_notification_config(load_env_file=False)
        used = set()
        for server in config.servers:
            if server.enabled:
                path = log_path(server.log_dir)
                if path in used:
                    raise ValueError("Enabled servers must use distinct log directories")
                used.add(path)
    if config.guild_id <= 0:
        raise ValueError("Discord Guild/Server ID must be positive")
    return config


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=".oyb-", dir=path.parent)
    try:
        os.chmod(temp, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_configuration(repo, values, servers, destination, approved):
    """Explicit confirmation gates config replacement; SQLite files are untouched."""
    if not approved:
        return False
    paths = (repo / ".env", destination)
    originals = [(p, p.read_text(encoding="utf-8") if p.exists() else None) for p in paths]
    if any(data is not None for _, data in originals):
        backup = repo / ".oyb-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        backup.mkdir(parents=True, mode=0o700)
        os.chmod(backup.parent, 0o700)
        for i, (path, data) in enumerate(originals):
            if data is not None:
                atomic_write(backup / ("env" if i == 0 else "servers.json"), data)
    try:
        atomic_write(destination, json.dumps(servers, indent=2, ensure_ascii=False) + "\n")
        atomic_write(repo / ".env", env_text(values))
    except Exception:
        for path, data in originals:
            if data is not None:
                atomic_write(path, data)
            elif path.exists():
                path.unlink()
        raise
    return True


def run_tests():
    # Do not let exported production database paths reach fixture constructors.
    clean = {k: v for k, v in os.environ.items() if k in (
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")}
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                            cwd=ROOT, env=clean, capture_output=True, text=True)
    if result.returncode:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        result.check_returncode()
    print("Unit test suite passed.")


def main():
    os.chdir(ROOT)
    print("OYB BOT SETUP\n")
    values = dict(dotenv_values(ROOT / ".env", interpolate=False)) if (ROOT / ".env").exists() else {}
    source = ROOT / values.get("SERVERS_CONFIG", "servers.local.json")
    defaults = json.loads((ROOT / "servers.example.json").read_text(encoding="utf-8"))
    existing = None
    if source.exists():
        with environment(values):
            read_servers(str(source))  # Reuse application validation, fail rather than discard.
        existing = json.loads(source.read_text(encoding="utf-8"))
        print("Existing server configuration found; settings and rules will be retained.")
    elif values.get("SERVERS_CONFIG"):
        raise ValueError("Configured SERVERS_CONFIG file is missing; restore it before rerunning setup")
    values["DISCORD_BOT_TOKEN"] = token_input(values.get("DISCORD_BOT_TOKEN", ""))
    values["GUILD_ID"] = ask("Discord Guild/Server ID", values.get("GUILD_ID", ""))
    count = int(ask("How many Reforger servers are currently active? 1-3", sum(s["enabled"] for s in existing) if existing else 1))
    if count not in (1, 2, 3):
        raise ValueError("Active server count must be 1, 2 or 3")
    print("Searching for readable Reforger servers...")
    candidates = discover()
    names, paths = [], []
    for i in range(count):
        print(f"\nSERVER {i+1}")
        prior = (existing or defaults)[i]
        with environment(values):
            old = os.path.expandvars(prior.get("log_dir", ""))
        if "$" in old:
            old = ""
        paths.append(select_log(candidates, old, {Path(p) for p in paths}))
        names.append(ask("Display name", prior["name"]))
    servers = make_servers(existing, defaults, count, paths, names)
    # Reuse custom state locations; never redirect existing production state.
    for key, default in {"PLAYTIME_DB": "data/playtime.sqlite3", "NOTIFICATION_STATE_DB": "data/notifications.sqlite3", "ACCOUNT_LINKS_DB": str(Path(values.get("NOTIFICATION_STATE_DB", "data/notifications.sqlite3")).with_name("account_links.sqlite3"))}.items():
        values.setdefault(key, default)
    values.update(SERVERS_CONFIG=str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source), PLAYTIME_ENABLED="true", JOIN_VOICE_CHANNEL="false", VOICE_CHANNEL_ID="0")
    values.setdefault("SESSION_STALE_SECONDS", "120")
    values.setdefault("STATUS_REFRESH_SECONDS", "60")
    values.pop("A2S_HOST", None)
    values.pop("A2S_PORT", None)
    for key in ("PLAYTIME_DB", "NOTIFICATION_STATE_DB", "ACCOUNT_LINKS_DB"):
        path = ROOT / values[key]
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not os.access(path.parent, os.R_OK | os.W_OK | os.X_OK) or (path.exists() and not os.access(path, os.R_OK | os.W_OK)):
            raise ValueError(f"Bot service account cannot use {key}; fix file ownership without deleting state")
    with tempfile.TemporaryDirectory(prefix=".oyb-validate-", dir=ROOT) as folder:
        staged = Path(folder) / "servers.json"
        atomic_write(staged, json.dumps(servers))
        parsed = dict(dotenv_values(stream=io.StringIO(env_text(values)), interpolate=False))
        validate(parsed, staged)
    print("Running existing unit tests before changing configuration...")
    run_tests()
    print("\nSetup summary (Discord token hidden):")
    for s in servers:
        print(f"  {s['name']}: " + (s["log_dir"] if s["enabled"] else "Disabled / Coming Soon"))
    print("Full OYB mode; combined XP; no voice connection. Existing state is preserved.")
    approved = ask("Save configuration and start/restart the bot? y/N", "N").lower() == "y"
    if not save_configuration(ROOT, values, servers, source, approved):
        print("Cancelled; configuration unchanged.")
        return 2
    return 0


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--render-unit":
            print(render_unit(sys.argv[2], sys.argv[3]), end="")
        elif len(sys.argv) > 1 and sys.argv[1] == "--render-cli":
            print(render_cli(sys.argv[2]), end="")
        elif sys.argv[1:] == ["--summary"]:
            os.chdir(ROOT)
            summary()
        else:
            sys.exit(main())
    except (ValueError, OSError, ConfigError, subprocess.CalledProcessError, getpass.GetPassWarning) as exc:
        # No config/token dumps; command args never contain the token.
        print(f"Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
