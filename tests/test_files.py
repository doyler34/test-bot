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
        (self.root / "Other").mkdir()
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
        self.assertEqual(files.instances(self.root), ["Other", "Plus01"])

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
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Plus01" / "Configs").mkdir(parents=True)
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
        folder = await (await self.client.get("/files/Plus01")).text()
        self.assertIn("Configs", folder)
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

    async def test_senior_admins_get_mods_but_not_files(self):
        await self.login("boss")
        self.assertEqual((await self.client.get("/files/Plus01")).status, 403)
        self.assertIn("Plus01", await (await self.client.get("/files")).text())

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
        token = await self.login("boss")
        with mock.patch("panel.files.amp_running", return_value=False):
            page = await (await self.client.get("/files")).text()
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


class AmpLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = Path(tempfile.mkdtemp()) / "panel.local.json"
        self.path.write_text('{"servers": []}\n')
        patcher = mock.patch.dict(os.environ, {"PANEL_CONFIG": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = PanelDB(":memory:")
        self.db.add_user("chief", auth.hash_password("chief-password"), "manager")
        self.db.add_user("boss", auth.hash_password("boss-password"), "owner")
        config = PanelConfig(database=":memory:", cookie_secure=False, servers=[])
        self.app = create_app(config, self.db, ServerManager(config, self.db), start_manager=False)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_a_manager_adds_the_amp_login_from_the_panel(self):
        await self.client.post("/login", data={"username": "boss", "password": "boss-password"})
        self.assertEqual((await self.client.get("/settings/amp")).status, 403)
        await self.client.post("/login", data={"username": "chief", "password": "chief-password"})
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
