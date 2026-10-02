"""Writes panel.local.json for a box whose Arma servers run under AMP.

Reads each server's RCON port and password from AMP's own serverconfig.json,
so nobody has to copy passwords around. Server ids come from the bot's
servers.local.json where a server's logs match, so the panel and the bot
agree on which server is which. AMP stays in charge of starting and stopping:
no systemd service is set and the RCON shutdown button is left out.

    python3 deploy/amp_panel_config.py [--bot /root/Arma-bot] [--port 8090]
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from panel.config import bot_log_dirs  # noqa: E402

INSTANCES = Path("/home/amp/.ampdata/instances")


def amp_servers(root):
    found = []
    for config in sorted(root.glob("*/arma-reforger/*/Configs/serverconfig.json")):
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"Skipping {config}: {exc}")
            continue
        rcon = data.get("rcon") or {}
        if not rcon.get("port") or not rcon.get("password"):
            print(f"Skipping {config}: no RCON set up")
            continue
        logs = config.parent.parent / "AReforgerMaster" / "logs"
        found.append({"instance": config.relative_to(root).parts[0], "logs": logs,
                      "port": int(rcon["port"]), "password": str(rcon["password"]),
                      "game_port": int(data.get("bindPort") or 0)})
    return sorted(found, key=lambda s: s["game_port"])


def bot_servers(bot):
    """The bot's own ids and names, by the instance folder its log_dir points into."""
    if not bot.is_dir():
        return {}
    here = Path.cwd()
    try:
        os.chdir(bot)
        dirs = bot_log_dirs()
        names = {str(e.get("id")): e.get("name") for e in json.loads(Path(os.getenv("SERVERS_CONFIG", "servers.local.json")).read_text())
                 if isinstance(e, dict)}
    except (OSError, ValueError):
        return {}
    finally:
        os.chdir(here)
    by_instance = {}
    for server_id, log_dir in dirs.items():
        parts = Path(log_dir).parts
        if "instances" in parts and parts.index("instances") + 1 < len(parts):
            by_instance[parts[parts.index("instances") + 1]] = (server_id, names.get(server_id) or server_id)
    return by_instance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", default="/root/Arma-bot")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--out", default=str(HERE.parent / "panel.local.json"))
    parser.add_argument("--instances", default=str(INSTANCES))
    args = parser.parse_args()

    servers = amp_servers(Path(args.instances))
    if not servers:
        sys.exit(f"No Arma Reforger servers with RCON found under {args.instances}.")
    known = bot_servers(Path(args.bot))
    out = Path(args.out)
    config = json.loads(out.read_text()) if out.is_file() else {}
    config.update({"listen": "127.0.0.1", "port": args.port, "cookie_secure": True,
                   "oyb_data": str(Path(args.bot) / "data")})
    config.setdefault("database", "data/panel.sqlite3")
    config.setdefault("site_hosts", [])
    taken, entries = set(), []
    for s in servers:
        server_id, name = known.get(s["instance"], (s["instance"].lower(), s["instance"]))
        if server_id in taken:
            server_id = s["instance"].lower()
        taken.add(server_id)
        entries.append({"id": server_id, "name": name, "rcon_host": "127.0.0.1", "rcon_port": s["port"],
                        "rcon_password": s["password"], "log_dir": str(s["logs"]),
                        "commands": {"shutdown": ""}})
        print(f"{server_id:12} {name:24} RCON {s['port']}  logs {'found' if s['logs'].is_dir() else 'MISSING'}  ({s['instance']})")
    config["servers"] = entries
    out.write_text(json.dumps(config, indent=2) + "\n")
    out.chmod(0o600)
    print(f"Wrote {out} for {len(entries)} servers. Start / Stop stay with AMP.")


if __name__ == "__main__":
    main()
