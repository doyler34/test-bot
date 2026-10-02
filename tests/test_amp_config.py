import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from panel.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def amp_server(instances, name, game_port, rcon_port, password="secret"):
    base = Path(instances, name, "arma-reforger", "1874900")
    (base / "Configs").mkdir(parents=True)
    (base / "AReforgerMaster" / "logs").mkdir(parents=True)
    (base / "Configs" / "serverconfig.json").write_text(json.dumps(
        {"bindPort": game_port, "rcon": {"port": rcon_port, "password": password}}))
    return base / "AReforgerMaster" / "logs"


class AmpConfigTests(unittest.TestCase):
    def test_builds_the_panel_config_from_amp(self):
        with tempfile.TemporaryDirectory() as tmp:
            instances = Path(tmp, "instances")
            logs = amp_server(instances, "OGEveron01", 2002, 20000, "pw-main")
            amp_server(instances, "A3x01", 2001, 19999)
            amp_server(instances, "Plus01", 2004, 20002)
            Path(instances, "ADS01").mkdir()
            bot = Path(tmp, "bot")
            (bot / "data").mkdir(parents=True)
            (bot / "servers.local.json").write_text(json.dumps([
                {"id": "server-1", "name": "Classic", "enabled": True, "log_dir": str(logs)}]))
            out = Path(tmp, "panel.local.json")
            result = subprocess.run([sys.executable, str(ROOT / "deploy" / "amp_panel_config.py"),
                                     "--instances", str(instances), "--bot", str(bot), "--out", str(out)],
                                    capture_output=True, text=True, cwd=tmp, env={k: v for k, v in os.environ.items() if k != "SERVERS_CONFIG"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")
            config = load_config(out)
            self.assertEqual([(s.id, s.name, s.rcon_port) for s in config.servers],
                             [("a3x01", "A3x01", 19999), ("server-1", "Classic", 20000), ("plus01", "Plus01", 20002)])
            main = config.server("server-1")
            self.assertEqual((main.rcon_password, main.log_dir, main.service), ("pw-main", str(logs), ""))
            self.assertEqual(main.commands["shutdown"], "")
            self.assertEqual((config.listen, config.port, config.cookie_secure), ("127.0.0.1", 8090, True))
            self.assertEqual(config.oyb_data, str(bot / "data"))
            self.assertIn("pw-main", out.read_text())
            self.assertNotIn("pw-main", result.stdout)


if __name__ == "__main__":
    unittest.main()
