import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from panel import auth, files
from panel.amp import AmpError
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB
from panel.servers import ServerManager
from panel.web import AMP, FILES_ROOT, create_app


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Plus01" / "logs").mkdir(parents=True)
        (self.root / "Plus01" / "GenericModule.kvp").write_text("")
        (self.root / "Other").mkdir()
        (self.root / "Other" / "GenericModule.kvp").write_text("")
        (self.root / "ADS01").mkdir()
        (self.root / "Other" / "secret.txt").write_text("not yours")
        os.symlink(self.root / "Other", self.root / "Plus01" / "sneaky")

    def test_nothing_outside_the_server_folder_can_be_reached(self):
        base, path = files.resolve(self.root, "Plus01", "logs")
        self.assertEqual(path, base / "logs")
        for rel in ("../Other/secret.txt", "sneaky/secret.txt", "logs/../../Other"):
            with self.assertRaises(files.FileError):
                files.resolve(self.root, "Plus01", rel)
        # A leading slash still means inside the server's folder.
        self.assertEqual(files.resolve(self.root, "Plus01", "/etc/passwd")[1], base / "etc" / "passwd")
        for inst in ("..", "../etc", "", "Plus01/logs"):
            with self.assertRaises(files.FileError):
                files.resolve(self.root, inst)

    def test_the_root_comes_from_a_server_log_folder(self):
        servers = [ServerConfig("one", "One", log_dir="/home/amp/.ampdata/instances/A3x01/arma-reforger/x/logs")]
        self.assertEqual(files.instances_root(servers), Path("/home/amp/.ampdata/instances"))
        self.assertEqual(files.instances_root([]), Path(files.DEFAULT_ROOT))
        # AMP's own controller has no game, so it isn't a server here.
        self.assertEqual(files.instances(self.root), ["Other", "Plus01"])
        with self.assertRaises(files.FileError):
            files.resolve(self.root, "ADS01")

    def test_mod_lists_read_and_write_the_way_amp_keeps_them(self):
        value = '{"modId": "60C4CE4888FF4621", "name": "ACE Core"},\n{"modId":"5DBD560C5148E1DA","name":"ACE Carrying","version":"1.2"}, junk'
        mods = files.parse_mods(value)
        self.assertEqual([m["modId"] for m in mods], ["60C4CE4888FF4621", "5DBD560C5148E1DA"])
        self.assertEqual(files.parse_mods(files.format_mods(mods)), mods)
        self.assertEqual(files.parse_mods(""), [])
        addons = self.root / "Plus01" / "arma-reforger" / "1874900" / "AReforgerMaster" / "addons"
        (addons / "ACECore_60C4CE4888FF4621").mkdir(parents=True)
        (addons / "not-a-mod").mkdir()
        found = files.downloaded_mods(self.root / "Plus01")
        self.assertEqual(list(found), ["60C4CE4888FF4621"])
        self.assertEqual(found["60C4CE4888FF4621"]["name"], "ACECore")


class FileManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Backups of deleted servers go next to the instances folder, so give it a parent of its own.
        self.root = Path(tempfile.mkdtemp()) / "instances"
        self.root.mkdir()
        (self.root / "Plus01" / "Configs").mkdir(parents=True)
        (self.root / "Plus01" / "GenericModule.kvp").write_text('App.AppSettings={"mods":""}')
        (self.root / "Plus01" / "Configs" / "serverconfig.json").write_text('{"a": 1}\n')
        (self.root / "Plus01" / "data.bin").write_bytes(b"\0\1\2")
        self.db = PanelDB(":memory:")
        self.db.add_user("chief", auth.hash_password("chief-password"), "manager")
        self.db.add_user("boss", auth.hash_password("boss-password"), "owner")
        config = PanelConfig(database=":memory:", cookie_secure=False, servers=[ServerConfig("one", "One")])
        self.app = create_app(config, self.db, ServerManager(config, self.db), start_manager=False)
        self.app[FILES_ROOT] = self.root
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def login(self, who):
        await self.client.post("/login", data={"username": who, "password": f"{who}-password"})
        page = await (await self.client.get("/settings")).text()
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    async def test_managers_browse_edit_upload_and_delete(self):
        token = await self.login("chief")
        self.assertIn("Plus01", await (await self.client.get("/files")).text())
        cfg = self.root / "Plus01" / "arma-reforger" / "1874900" / "Configs"
        cfg.mkdir(parents=True)
        (cfg / "serverconfig.json").write_text("{}")
        folder = await (await self.client.get("/files/Plus01")).text()
        self.assertIn("Configs", folder)
        self.assertIn('href="/files/Plus01/arma-reforger/1874900/Configs/serverconfig.json"', folder)
        self.assertIn("AMP writes this file", await (await self.client.get(
            "/files/Plus01/arma-reforger/1874900/Configs/serverconfig.json")).text())
        self.assertIn("data.bin", folder)
        editor = await (await self.client.get("/files/Plus01/Configs/serverconfig.json")).text()
        self.assertIn("&#34;a&#34;: 1", editor)
        self.assertIn("not plain text", await (await self.client.get("/files/Plus01/data.bin")).text())

        form = FormData()
        form.add_field("text", '{"a": 2}\n')
        await self.client.post(f"/files/Plus01/Configs/serverconfig.json?do=save&csrf={token}", data=form)
        self.assertEqual((self.root / "Plus01" / "Configs" / "serverconfig.json").read_text(), '{"a": 2}\n')

        upload = FormData()
        upload.add_field("file", b"hello", filename="note.txt")
        await self.client.post(f"/files/Plus01/Configs?do=upload&csrf={token}", data=upload)
        self.assertEqual((self.root / "Plus01" / "Configs" / "note.txt").read_bytes(), b"hello")
        self.assertEqual([p.name for p in (self.root / "Plus01" / "Configs").iterdir() if p.name.startswith(".")], [])

        await self.client.post("/files/Plus01", data={"csrf": token, "action": "folder", "name": "mods"})
        self.assertTrue((self.root / "Plus01" / "mods").is_dir())
        await self.client.post("/files/Plus01/Configs/note.txt", data={"csrf": token, "action": "rename", "name": "n.txt"})
        self.assertTrue((self.root / "Plus01" / "Configs" / "n.txt").exists())
        await self.client.post("/files/Plus01/Configs/n.txt", data={"csrf": token, "action": "delete"})
        self.assertFalse((self.root / "Plus01" / "Configs" / "n.txt").exists())
        await self.client.post("/files/Plus01", data={"csrf": token, "action": "delete"})
        self.assertTrue((self.root / "Plus01").is_dir())          # the server's own folder stays

        # A token that doesn't match stops a streamed save.
        bad = FormData()
        bad.add_field("text", "x")
        reply = await self.client.post("/files/Plus01/Configs/serverconfig.json?do=save&csrf=wrong", data=bad)
        self.assertEqual(reply.status, 403)
        self.assertEqual((await self.client.get("/files/Plus01/..%2fother")).status, 404)
        actions = {r["action"] for r in self.db.all("SELECT action FROM audit")}
        self.assertTrue({"edited file", "uploaded file", "made folder", "renamed file", "deleted file"} <= actions)

    async def test_the_server_manager_is_for_managers(self):
        await self.login("boss")
        self.assertEqual((await self.client.get("/files/Plus01")).status, 403)
        self.assertEqual((await self.client.get("/manage")).status, 403)
        self.assertNotIn('href="/manage"', await (await self.client.get("/settings")).text())
        await self.login("chief")
        page = await (await self.client.get("/manage")).text()
        self.assertIn("Plus01", page)
        self.assertIn('href="/manage"', page)
        tabs = await (await self.client.get("/files/Plus01")).text()
        for tab in ("Overview", "Console", "Settings", "Mods", "Files"):
            self.assertIn(f">{tab}</a>", tabs)

    async def test_mod_list_changes_go_through_amp(self):
        stored = {"value": '{"modId": "60C4CE4888FF4621", "name": "ACE Core"}'}
        amp = mock.Mock()
        amp.instances = mock.AsyncMock(return_value={"Plus01": {"InstanceID": "p", "InstanceName": "Plus01"}})
        amp.setting = mock.AsyncMock(side_effect=lambda inst, key: stored["value"])

        async def change(inst, key, fn):
            stored["value"] = fn(stored["value"])
            return stored["value"]
        amp.change_setting = change
        self.app[AMP] = amp
        addons = self.root / "Plus01" / "arma-reforger" / "1874900" / "AReforgerMaster" / "addons"
        (addons / "ACECarrying_5DBD560C5148E1DA").mkdir(parents=True)
        token = await self.login("boss")
        running = mock.patch("panel.files.amp_running", return_value=True)
        running.start()
        self.addCleanup(running.stop)
        page = await (await self.client.get("/mods/Plus01")).text()
        self.assertIn("ACE Core", page)
        self.assertIn("Downloaded but not loaded (1)", page)
        await self.client.post("/mods/Plus01", data={"csrf": token, "action": "add", "mod_id": "5dbd560c5148e1da"})
        self.assertEqual([m["name"] for m in files.parse_mods(stored["value"])], ["ACE Core", "ACECarrying"])
        await self.client.post("/mods/Plus01", data={"csrf": token, "action": "remove", "mod_id": "60C4CE4888FF4621"})
        self.assertEqual([m["modId"] for m in files.parse_mods(stored["value"])], ["5DBD560C5148E1DA"])
        await self.client.post("/mods/Plus01", data={"csrf": token, "action": "add", "mod_id": "nope"})
        self.assertEqual(len(files.parse_mods(stored["value"])), 1)


    async def test_with_amp_off_mods_go_straight_into_its_settings_file(self):
        kvp = self.root / "Plus01" / "GenericModule.kvp"
        kvp.write_text('App.ExitTimeout=5\nApp.AppSettings={"name":"OYB Plus","mods":"","maxFPS":"120"}\nApp.Other=1')
        token = await self.login("boss")
        with mock.patch("panel.files.amp_running", return_value=False):
            page = await (await self.client.get("/mods/Plus01")).text()
            self.assertIn("AMP is off", page)
            await self.client.post("/mods/Plus01", data={"csrf": token, "action": "add", "mod_id": "60C4CE4888FF4621",
                                                         "name": "ACE Core"})
        lines = kvp.read_text().split("\n")
        self.assertEqual((lines[0], lines[2]), ("App.ExitTimeout=5", "App.Other=1"))
        settings = json.loads(lines[1].split("=", 1)[1])
        self.assertEqual((settings["name"], settings["maxFPS"]), ("OYB Plus", "120"))
        self.assertEqual(files.parse_mods(settings["mods"]), [{"modId": "60C4CE4888FF4621", "name": "ACE Core"}])

    async def test_amp_instances_start_and_stop_from_the_panel(self):
        amp = mock.Mock()
        amp.instance_power = mock.AsyncMock()
        self.app[AMP] = amp
        token = await self.login("chief")
        with mock.patch("panel.files.amp_running", return_value=False):
            page = await (await self.client.get("/manage")).text()
        self.assertIn('action="/instances/Plus01/start"', page)
        await self.client.post("/instances/Plus01/start", data={"csrf": token})
        amp.instance_power.assert_awaited_once_with("Plus01", "start")
        self.assertEqual((await self.client.post("/instances/..%2fx/start", data={"csrf": token})).status, 404)

    def test_amp_counts_as_running_when_its_program_is_in_the_folder(self):
        proc = Path(tempfile.mkdtemp())
        (proc / "123").mkdir()
        os.symlink(f"{self.root}/Plus01/AMP_Linux_x86_64", proc / "123" / "exe")
        (proc / "456").mkdir()
        os.symlink("/usr/bin/python3", proc / "456" / "exe")
        self.assertTrue(files.amp_running(self.root / "Plus01", proc))
        self.assertFalse(files.amp_running(self.root / "Other", proc))


    async def test_overview_console_and_settings_go_through_that_servers_amp(self):
        logs = self.root / "Plus01" / "arma-reforger" / "1874900" / "AReforgerMaster" / "logs" / "logs_2026-10-20"
        logs.mkdir(parents=True)
        (logs / "console.log").write_text("12:00:00 Game started\n12:00:05 Player joined\n")
        amp = mock.Mock()
        amp.instances = mock.AsyncMock(return_value={"Plus01": {"InstanceID": "p", "InstanceName": "Plus01"}})
        amp.status = mock.AsyncMock(return_value={"State": 20, "Uptime": "02:10:00",
                                                  "Metrics": {"Active Users": {"RawValue": 34, "MaxValue": 128}}})
        amp.power = mock.AsyncMock()
        amp.send_console = mock.AsyncMock()
        amp.set_config = mock.AsyncMock()
        # AMP's settings list leaves the game's own settings out; they're read by name.
        (self.root / "Plus01" / "configmanifest.json").write_text(json.dumps([
            {"FieldName": "Name", "InputType": "text"}, {"FieldName": "Visible", "InputType": "checkbox"},
            {"FieldName": "AdminPassword", "InputType": "password"}, {"FieldName": "Secret", "InputType": "hidden"}]))
        amp.configs = mock.AsyncMock(return_value=[
            {"Name": "Server name", "Node": "Game.Name", "InputType": "text", "CurrentValue": "OYB Plus", "Subcategory": "Server:dns:1"},
            {"Name": "Visible", "Node": "Game.Visible", "InputType": "checkbox", "CurrentValue": True, "Subcategory": "Server:dns:1"},
            {"Name": "Admin password", "Node": "Game.AdminPassword", "InputType": "password", "CurrentValue": "secret",
             "Subcategory": "Server:dns:1"}])
        amp.settings_spec = mock.AsyncMock(return_value={"System Settings": [
            {"Name": "Secret", "Node": "Game.Hidden", "Hidden": True, "CurrentValue": "x"}]})
        self.app[AMP] = amp
        running = mock.patch("panel.files.amp_running", return_value=True)
        running.start()
        self.addCleanup(running.stop)
        token = await self.login("chief")
        overview = await (await self.client.get("/manage/Plus01")).text()
        self.assertIn("Running", overview)
        self.assertIn("Active Users", overview)
        await self.client.post("/manage/Plus01/power/restart", data={"csrf": token})
        amp.power.assert_awaited_once_with({"InstanceID": "p", "InstanceName": "Plus01"}, "restart")

        console = await (await self.client.get("/manage/Plus01/console")).text()
        self.assertIn("Player joined", console)
        self.assertIn("Player joined", await (await self.client.get("/manage/Plus01/console.txt")).text())
        await self.client.post("/manage/Plus01/console", data={"csrf": token, "message": "#players"})
        amp.send_console.assert_awaited_once_with({"InstanceID": "p", "InstanceName": "Plus01"}, "#players")

        settings = await (await self.client.get("/manage/Plus01/settings")).text()
        self.assertIn("OYB Plus", settings)
        self.assertEqual(amp.configs.await_args.args[1], ["Meta.GenericModule.Name", "Meta.GenericModule.Visible",
                                                          "Meta.GenericModule.AdminPassword"])
        self.assertNotIn("secret", settings)
        self.assertNotIn("Game.Hidden", settings)
        await self.client.post("/manage/Plus01/settings", data=[("csrf", token), ("n:Game.Name", "OYB Plus 2"),
                                                                ("n:Game.Visible", "false"), ("n:Game.Visible", "true"),
                                                                ("n:Game.AdminPassword", "")])
        amp.set_config.assert_awaited_once_with({"InstanceID": "p", "InstanceName": "Plus01"}, "Game.Name", "OYB Plus 2")
        self.assertEqual(self.db.one("SELECT detail FROM audit WHERE action = 'changed AMP setting'")[0], "OYB Plus 2")


    async def test_admins_are_a_list_of_names_on_the_settings_tab(self):
        gaz, rook = "5f1c2a90-8b1e-4a57-9a3e-2d4b6c8e0f11", "a0b1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d"
        self.db.write("INSERT INTO players (identity, name, first_seen, last_seen) VALUES (?, 'Gaz', 1, 1)", gaz)
        self.db.write("INSERT INTO players (identity, name, first_seen, last_seen) VALUES (?, 'Rook', 1, 1)", rook)
        self.db.set_staff(rook, "chief")
        (self.root / "Plus01" / "configmanifest.json").write_text(json.dumps([{"FieldName": "admins", "InputType": "text"}]))
        amp = mock.Mock()
        amp.instances = mock.AsyncMock(return_value={"Plus01": {"InstanceID": "p", "InstanceName": "Plus01"}})
        amp.configs = mock.AsyncMock(return_value=[{"Name": "Admins", "Node": "Meta.GenericModule.admins",
                                                    "InputType": "text", "CurrentValue": f'"{gaz}","76561198000000000"'}])
        amp.settings_spec = mock.AsyncMock(return_value={})
        amp.change_admins = mock.AsyncMock()
        self.app[AMP] = amp
        running = mock.patch("panel.files.amp_running", return_value=True)
        running.start()
        self.addCleanup(running.stop)
        token = await self.login("chief")
        page = await (await self.client.get("/manage/Plus01/settings")).text()
        self.assertIn(">Gaz</a>", page)
        self.assertIn("Steam ID", page)
        self.assertIn("2 / 20", page)
        self.assertIn("+ Rook", page)                     # marked as admin in the panel, not on this server yet
        self.assertNotIn('name="n:Meta.GenericModule.admins"', page)
        instance = {"InstanceID": "p", "InstanceName": "Plus01"}
        await self.client.post("/manage/Plus01/admins", data=[("csrf", token), ("player", ""), ("identity", ""),
                                                              ("identity", rook)])
        amp.change_admins.assert_awaited_with(instance, add=[rook], remove=[])
        await self.client.post("/manage/Plus01/admins", data={"csrf": token, "player": "Gaz", "identity": ""})
        amp.change_admins.assert_awaited_with(instance, add=[gaz], remove=[])
        await self.client.post("/manage/Plus01/admins", data={"csrf": token, "remove": gaz})
        amp.change_admins.assert_awaited_with(instance, add=[], remove=[gaz])


    async def test_making_a_new_server_copies_the_game_template(self):
        (self.root / "Plus01" / "GenericModule.kvp").write_text(
            "Meta.DisplayName=Arma Reforger\nMeta.ConfigManifest=arma-reforgerconfig.json\n"
            "Meta.MetaConfigManifest=arma-reforgermetaconfig.json\nMeta.ConfigRoot=arma-reforger.kvp\n"
            "Meta.Author=Greelan\nApp.AppSettings={}")
        (self.root / "Plus01" / "AMPConfig.conf").write_text("Webserver.Port=8084\n")
        amp = mock.Mock()
        amp.create_instance = mock.AsyncMock()
        self.app[AMP] = amp
        token = await self.login("chief")
        await self.client.post("/manage/new", data={"csrf": token, "name": "bad name", "copy_from": "Plus01"})
        amp.create_instance.assert_not_awaited()
        await self.client.post("/manage/new", data={"csrf": token, "name": "Plus01", "copy_from": "Plus01"})
        amp.create_instance.assert_not_awaited()                     # already exists
        await self.client.post("/manage/new", data={"csrf": token, "name": "Arland02", "friendly": "Arland 2",
                                                     "copy_from": "Plus01"})
        amp.create_instance.assert_awaited_once_with("Arland02", "Arland 2", 8085, {
            "GenericModule.Meta.DisplayName": "Arma Reforger", "GenericModule.Meta.ConfigManifest": "arma-reforgerconfig.json",
            "GenericModule.Meta.MetaConfigManifest": "arma-reforgermetaconfig.json",
            "GenericModule.Meta.ConfigRoot": "arma-reforger.kvp"})

    async def test_deleting_a_server_is_named_people_only_typed_out_and_backed_up_first(self):
        game = self.root / "Plus01" / "arma-reforger" / "1874900"
        (game / "addons" / "data").mkdir(parents=True)
        (game / "addons" / "data" / "game.pak").write_bytes(b"x" * 1000)
        (game / "AReforgerMaster" / "addons" / "SomeMod_0123456789ABCDEF").mkdir(parents=True)
        (game / "AReforgerMaster" / "profile.txt").write_text("keep me")
        amp = mock.Mock()
        amp.instance_power = mock.AsyncMock()
        amp.delete_instance = mock.AsyncMock()
        self.app[AMP] = amp
        token = await self.login("chief")                            # a manager, but not one of the named accounts
        reply = await self.client.post("/manage/Plus01/delete", data={"csrf": token, "confirm": "Plus01"})
        self.assertEqual(reply.status, 403)
        self.db.add_user("burd", auth.hash_password("burd-password"), "manager")
        token = await self.login("burd")
        await self.client.post("/manage/Plus01/delete", data={"csrf": token, "confirm": "plus01"})
        amp.delete_instance.assert_not_awaited()                     # has to be typed exactly
        with mock.patch("panel.files.amp_running", side_effect=[True, False]):
            await self.client.post("/manage/Plus01/delete", data={"csrf": token, "confirm": "Plus01"})
        amp.instance_power.assert_awaited_once_with("Plus01", "stop")
        amp.delete_instance.assert_awaited_once_with("Plus01")
        saved = list((self.root.parent / files.BACKUPS).glob("Plus01-*.tar.gz"))
        self.assertEqual(len(saved), 1)
        import tarfile
        with tarfile.open(saved[0]) as tar:
            names = tar.getnames()
        self.assertIn("Plus01/arma-reforger/1874900/AReforgerMaster/profile.txt", names)
        self.assertIn("Plus01/GenericModule.kvp", names)
        self.assertFalse([n for n in names if "/addons/" in n or n.endswith("/addons")])
        self.assertTrue(self.db.one("SELECT 1 FROM audit WHERE action = 'backed up server'"))


class AmpLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = Path(tempfile.mkdtemp()) / "panel.local.json"
        self.path.write_text('{"servers": []}\n')
        patcher = mock.patch.dict(os.environ, {"PANEL_CONFIG": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = PanelDB(":memory:")
        self.db.add_user("burd", auth.hash_password("burd-password"), "manager")
        self.db.add_user("boss", auth.hash_password("boss-password"), "owner")
        config = PanelConfig(database=":memory:", cookie_secure=False, servers=[])
        self.app = create_app(config, self.db, ServerManager(config, self.db), start_manager=False)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_a_manager_adds_the_amp_login_from_the_panel(self):
        await self.client.post("/login", data={"username": "boss", "password": "boss-password"})
        self.assertEqual((await self.client.get("/settings/amp")).status, 403)
        await self.client.post("/login", data={"username": "burd", "password": "burd-password"})
        page = await (await self.client.get("/settings/amp")).text()
        self.assertIn("Not set up", page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        refused = mock.AsyncMock(side_effect=AmpError("AMP refused the login."))
        with mock.patch("panel.web.Amp.instances", refused):
            await self.client.post("/settings/amp", data={"csrf": token, "username": "gaz", "password": "wrong"})
        self.assertNotIn("amp", self.path.read_text())
        with mock.patch("panel.web.Amp.instances", mock.AsyncMock(return_value={"Plus01": {}, "A3x01": {}})):
            await self.client.post("/settings/amp", data={"csrf": token, "username": "gaz", "password": "right-one"})
            page = await (await self.client.get("/settings/amp")).text()
        self.assertEqual(json.loads(self.path.read_text())["amp"],
                         {"url": "http://127.0.0.1:8080", "username": "gaz", "password": "right-one"})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertIn("Connected", page)
        self.assertIn("A3x01, Plus01", page)
        self.assertNotIn("right-one", page)
        self.assertNotIn("right-one", str(self.db.all("SELECT detail FROM audit")))


class AmpNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_setting_missing_from_the_list_is_asked_for_by_name(self):
        from panel.amp import Amp
        amp = Amp("http://amp", "gaz", "pw")
        replies = {"Core/GetSettingsSpec": {"Arma Reforger": [{"Node": "Meta.GenericModule.$GamePort", "Name": "Game Port"}]}}

        async def call(http, base, session, endpoint, args=None):
            if endpoint == "Core/GetConfig":
                return {"Name": "Admins"} if args["node"] == "Meta.GenericModule.admins" else {}
            return replies[endpoint]
        amp._call = call
        inst = {"InstanceID": "a", "InstanceName": "A3x01"}
        self.assertEqual(await amp._admins_node(None, "b", "s", inst), "Meta.GenericModule.admins")
        self.assertEqual(await amp._setting_node(None, "b", "s", inst, "admins"), "Meta.GenericModule.admins")
        with self.assertRaises(AmpError):
            await amp._setting_node(None, "b", "s", inst, "nothing")


class AmpStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_starting_a_server_whose_amp_is_off_switches_it_on_first(self):
        from panel.amp import Amp
        amp = Amp("http://amp", "gaz", "pw")
        tries = {"n": 0}

        async def instance(http, inst):
            tries["n"] += 1
            if tries["n"] < 3:
                raise AmpError("Couldn't reach AMP at http://127.0.0.1:8080: Cannot connect to host 127.0.0.1:8084")
            return "base", "session"
        amp._instance = instance
        amp.instance_power = mock.AsyncMock()
        amp._call = mock.AsyncMock(return_value={"Status": True})
        inst = {"InstanceID": "p", "InstanceName": "Plus01", "Running": False}
        await amp.power(inst, "start", pause=0)
        amp.instance_power.assert_awaited_once_with("Plus01", "start")
        amp._call.assert_awaited_once_with(mock.ANY, "base", "session", "Core/Start")
        # Stopping one that's off is just an error, nothing gets switched on.
        tries["n"] = 0
        amp.instance_power.reset_mock()
        with self.assertRaises(AmpError):
            await amp.power(inst, "stop", pause=0)
        amp.instance_power.assert_not_awaited()
