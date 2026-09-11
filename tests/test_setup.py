import io
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values
from bot.config import ConfigError
from deploy.setup_config import (discover, env_text, log_path, make_servers,
    process_roots, render_cli, render_unit, save_configuration, select_log,
    token_input, validate, run_tests)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.defaults = json.loads((Path(__file__).parents[1] / "servers.example.json").read_text(encoding="utf-8"))
        self.values = {"DISCORD_BOT_TOKEN": "not-a-real-token", "GUILD_ID": "123"}

    def logs(self, name="server1"):
        path = self.root / name / "profile" / "logs"
        rotation = path / "logs_2026-09-07_13-00-00"
        rotation.mkdir(parents=True)
        (rotation / "console.log").write_text("13:00:00.000 ENGINE : Arma Reforger server\n")
        return path

    def config(self, count):
        paths = [str(self.logs(f"server{i}")) for i in range(count)]
        return make_servers(None, self.defaults, count, paths, [f"Server {i+1}" for i in range(count)])

    def check(self, servers):
        path = self.root / "servers.json"
        path.write_text(json.dumps(servers))
        return validate(self.values, path)

    def test_detect_valid_structure_and_reject_random_console(self):
        valid = self.logs()
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "console.log").write_text("hello")
        self.assertEqual(discover([self.root]), [valid.resolve()])
        with self.assertRaises(ValueError):
            log_path(unrelated)

    def test_structure_without_engine_evidence_rejected(self):
        path = self.logs()
        next(path.glob("logs_*/console.log")).write_text("unrelated application output")
        self.assertEqual(discover([self.root]), [])

    def test_manual_fallback(self):
        path = self.logs()
        with patch("builtins.input", side_effect=["M", str(path)]):
            self.assertEqual(select_log([]), str(path.resolve()))

    def test_invalid_and_unreadable_path(self):
        with self.assertRaises(OSError):
            log_path(self.root / "missing")
        path = self.logs()
        with patch("deploy.setup_config.os.access", return_value=False):
            with self.assertRaises(ValueError):
                log_path(path)

    def test_duplicates_rejected(self):
        servers = self.config(2)
        servers[1]["log_dir"] = servers[0]["log_dir"]
        with self.assertRaises(ConfigError):
            self.check(servers)

    def test_one_active_two_disabled_need_no_log_paths_or_voice(self):
        servers = self.config(1)
        self.assertEqual([s["enabled"] for s in servers], [True, False, False])
        self.assertEqual([s["log_dir"] for s in servers[1:]], ["", ""])
        loaded = self.check(servers)
        self.assertEqual(loaded.voice_channel_id, 0)
        self.assertFalse(loaded.join_voice_channel)
        self.assertTrue(all(s.a2s_host is None for s in loaded.servers))

    def test_two_independently_configured_servers(self):
        servers = self.config(2)
        loaded = self.check(servers)
        self.assertEqual([s.enabled for s in loaded.servers], [True, True, False])
        self.assertNotEqual(loaded.servers[0].log_dir, loaded.servers[1].log_dir)
        self.assertNotIn("Coming soon", loaded.servers[1].settings)

    def test_rerun_preserves_all_databases_and_backs_up_configuration(self):
        data = self.root / "data"
        data.mkdir()
        paths = [data / name for name in ("playtime.sqlite3", "notifications.sqlite3", "account_links.sqlite3")]
        for p in paths:
            db = sqlite3.connect(p)
            db.execute("CREATE TABLE production (value TEXT)")
            db.execute("INSERT INTO production VALUES ('keep me')")
            db.commit()
            db.close()
        before = [p.read_bytes() for p in paths]
        dest = self.root / "servers.local.json"
        servers = self.config(1)
        for _ in range(2):
            self.assertTrue(save_configuration(self.root, self.values, servers, dest, True))
        self.assertEqual(before, [p.read_bytes() for p in paths])
        self.assertEqual(len(list((self.root / ".oyb-backups").glob("*/env"))), 1)
        if os.name != "nt":
            self.assertEqual((self.root / ".env").stat().st_mode & 0o777, 0o600)

    def test_cancel_never_overwrites_existing_config(self):
        env = self.root / ".env"
        dest = self.root / "servers.local.json"
        env.write_text("keep-token")
        dest.write_text("keep-servers")
        self.assertFalse(save_configuration(self.root, self.values, [], dest, False))
        self.assertEqual(env.read_text(), "keep-token")
        self.assertEqual(dest.read_text(), "keep-servers")

    def test_hidden_token_input_and_reuse(self):
        output = io.StringIO()
        with patch("sys.stdout", output), patch("getpass.getpass", return_value="secret") as hidden:
            self.assertEqual(token_input(), "secret")
            hidden.assert_called_once()
        self.assertNotIn("secret", output.getvalue())
        with patch("builtins.input", return_value=""), patch("getpass.getpass") as hidden:
            self.assertEqual(token_input("existing"), "existing")
            hidden.assert_not_called()

    def test_config_escaping_round_trip(self):
        values = {"DISCORD_BOT_TOKEN": "abc\\def'xyz", "CUSTOM": "hello world"}
        self.assertEqual(dict(dotenv_values(stream=io.StringIO(env_text(values)), interpolate=False)), values)

    def test_keep_custom_settings_when_enabling_server(self):
        old = self.config(1)
        old[1]["settings"] = "Our custom configuration"
        old[1]["rules"] = "Our rules"
        updated = make_servers(old, self.defaults, 2, [old[0]["log_dir"], str(self.logs("other"))], ["One", "Two"])
        self.assertEqual(updated[1]["settings"], "Our custom configuration")
        self.assertEqual(updated[1]["rules"], "Our rules")
        self.assertFalse(old[1]["enabled"])

    def test_process_profile_roots(self):
        proc = self.root / "proc"
        process = proc / "123"
        process.mkdir(parents=True)
        # resolve(strict=True) also accepts a directory in this /proc fixture.
        (process / "cwd").mkdir()
        profile = self.root / "profile"
        (process / "cmdline").write_bytes(("/opt/ArmaReforgerServer\0-profile\0" + str(profile) + "\0").encode())
        self.assertIn(profile, process_roots(proc))

    def test_unit_and_cli_use_checkout_not_development_paths(self):
        unit = render_unit("/opt/oyb bot", "oyb")
        self.assertIn('WorkingDirectory=/opt/oyb bot\n', unit)
        self.assertIn('ExecStart="/opt/oyb bot/.venv/bin/python" main.py', unit)
        self.assertIn("UMask=0077", unit)
        self.assertNotIn("reforger-server", unit)
        cli = render_cli("/opt/oyb bot")
        self.assertIn("'/opt/oyb bot/deploy/setup.sh'", cli)

    @unittest.skipUnless(os.name == "posix" and shutil.which("systemd-analyze"), "Requires Linux systemd-analyze")
    def test_generated_service_passes_real_systemd_verification(self):
        import getpass
        repo = self.root / "bot with spaces"
        executable = repo / ".venv/bin/python"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        unit = self.root / "oyb-setup-regression.service"
        unit.write_text(render_unit(repo, getpass.getuser()))
        result = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_test_runner_does_not_inherit_production_secrets_or_database_paths(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "secret", "ACCOUNT_LINKS_DB": "/production/links.db"}), patch("subprocess.run") as runner:
            runner.return_value.returncode = 0
            run_tests()
        env = runner.call_args.kwargs["env"]
        self.assertNotIn("ACCOUNT_LINKS_DB", env)
        self.assertNotIn("DISCORD_BOT_TOKEN", env)

    def test_validation_ignores_existing_env_file(self):
        from dotenv import load_dotenv
        saved = self.root / ".env"
        saved.write_text("VOICE_CHANNEL_ID=1545897772442583141\nJOIN_VOICE_CHANNEL=true\nSESSION_STALE_SECONDS=999\n")
        original = saved.read_bytes()
        servers = self.config(1)
        with patch("bot.notification_config.load_dotenv", side_effect=lambda: load_dotenv(saved)) as loader:
            loaded = self.check(servers)
        loader.assert_not_called()
        self.assertEqual(loaded.voice_channel_id, 0)
        self.assertFalse(loaded.join_voice_channel)
        self.assertEqual(loaded.stale_seconds, 120)
        self.assertEqual(saved.read_bytes(), original)

    def test_normal_startup_still_loads_env_file(self):
        from dotenv import load_dotenv
        from bot.notification_config import load_notification_config
        from deploy.setup_config import environment
        servers = self.config(1)
        path = self.root / "servers.json"
        path.write_text(json.dumps(servers))
        saved = self.root / ".env"
        saved.write_text("VOICE_CHANNEL_ID=1234\n")
        with environment({**self.values, "SERVERS_CONFIG": str(path)}):
            with patch("bot.notification_config.load_dotenv", side_effect=lambda: load_dotenv(saved)) as loader:
                config = load_notification_config()
        loader.assert_called_once()
        self.assertEqual(config.voice_channel_id, 1234)
