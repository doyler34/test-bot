import json
import os
import re
import tempfile
import time
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
import asyncio

from panel import web as web_module
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
        for tab in ("Overview", "Console", "Settings", "Mods", "Arsenal", "Files"):
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


    def arsenal_profile(self, items=True):
        profile = self.root / "Plus01" / "arma-reforger" / "1874900" / "AReforgerMaster" / "profile"
        (profile / "OYB_Arsenal").mkdir(parents=True)
        if items:
            (profile / "OYB_Arsenal" / "items.json").write_text(json.dumps({
                "version": 1,
                "prefabs": ["{AAAA000000000001}Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et",
                            "{AAAA000000000002}Prefabs/Weapons/Handguns/M9/Handgun_M9.et",
                            "{AAAA000000000003}Prefabs/Items/Medicine/FieldDressing_US_01.et"],
                "names": ["M16A2", "M9", "Field Dressing"],
                "categories": ["Rifles", "Pistols", "Medical"],
                "factions": ["US", "US", "US,USSR"],
                "prices": [30, 15, 2]} | ({"version": 2,
                "prefabs": ["{AAAA000000000001}Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et",
                            "{AAAA000000000002}Prefabs/Weapons/Handguns/M9/Handgun_M9.et",
                            "{AAAA000000000003}Prefabs/Items/Medicine/FieldDressing_US_01.et",
                            "{CCCC000000000004}Prefabs/Weapons/Rifles/G3/Rifle_G3A3.et"],
                "names": ["M16A2", "M9", "Field Dressing", "HK G3A3"],
                "categories": ["Rifles", "Pistols", "Medical", "Rifles"],
                "factions": ["US", "US", "US,USSR", ""],
                "prices": [30, 15, 2, 0], "found": [0, 0, 0, 1]} if items == "scan" else {})))
        return profile / "OYB_Arsenal"

    def arsenal_saved(self, folder):
        return json.loads((folder / "settings.json").read_text())

    async def test_the_arsenal_page_waits_for_the_server_to_list_its_items(self):
        await self.login("boss")
        page = await (await self.client.get("/arsenal/Plus01")).text()
        self.assertIn("No item list yet", page)
        self.assertIn(">Arsenal</a>", page)

    async def test_items_can_be_hidden_shown_and_repriced(self):
        folder = self.arsenal_profile()
        m16 = "{AAAA000000000001}Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et"
        token = await self.login("boss")
        page = await (await self.client.get("/arsenal/Plus01")).text()
        for name in ("M16A2", "M9", "Field Dressing"):
            self.assertIn(name, page)
        searched = await (await self.client.get("/arsenal/Plus01", params={"q": "m16"})).text()
        self.assertIn("M16A2", searched)
        self.assertNotIn("Field Dressing", searched)

        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "hide", "prefab": m16})
        self.assertEqual(self.arsenal_saved(folder)["hidden"], [m16])
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "price", "prefab": m16, "cost": "45"})
        saved = self.arsenal_saved(folder)
        self.assertEqual((saved["priceItems"], saved["priceCosts"]), ([m16], [45]))
        self.assertEqual(files.arsenal_settings(self.root / "Plus01"), ({m16}, {m16: 45}, {}, {}))

        # Bad prices and items the server never listed change nothing.
        for cost in ("-1", "abc", str(files.ARSENAL_MAX_PRICE + 1)):
            await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "price", "prefab": m16, "cost": cost})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "hide", "prefab": "{BBBB}Nope.et"})
        self.assertEqual(files.arsenal_settings(self.root / "Plus01"), ({m16}, {m16: 45}, {}, {}))

        hidden_only = await (await self.client.get("/arsenal/Plus01", params={"show": "hidden"})).text()
        self.assertIn("M16A2", hidden_only)
        self.assertNotIn("Handgun_M9", hidden_only)

        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "show", "prefab": m16})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "reset", "prefab": m16})
        self.assertEqual(files.arsenal_settings(self.root / "Plus01"), (set(), {}, {}, {}))
        actions = {r["action"] for r in self.db.all("SELECT action FROM audit")}
        self.assertTrue({"arsenal hide", "arsenal price", "arsenal show", "arsenal reset"} <= actions)

    async def test_a_whole_filter_can_be_hidden_at_once(self):
        folder = self.arsenal_profile()
        token = await self.login("boss")
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "hide_found", "faction": "US", "cat": "Rifles"})
        self.assertEqual(self.arsenal_saved(folder)["hidden"], ["{AAAA000000000001}Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et"])
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "hide_found", "faction": "USSR"})
        self.assertEqual(len(self.arsenal_saved(folder)["hidden"]), 2)
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "show_found"})
        self.assertEqual(self.arsenal_saved(folder)["hidden"], [])

    async def test_items_found_by_the_scan_can_be_added_and_taken_out(self):
        folder = self.arsenal_profile(items="scan")
        g3 = "{CCCC000000000004}Prefabs/Weapons/Rifles/G3/Rifle_G3A3.et"
        m9 = "{AAAA000000000002}Prefabs/Weapons/Handguns/M9/Handgun_M9.et"
        token = await self.login("boss")
        unlisted = await (await self.client.get("/arsenal/Plus01", params={"show": "unlisted"})).text()
        self.assertIn("HK G3A3", unlisted)
        self.assertIn("Add to arsenal", unlisted)
        self.assertNotIn("Handgun_M9", unlisted)

        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "add", "prefab": g3, "cost": "35",
                                                        "category": "Sniper rifles", "faction": "US"})
        saved = self.arsenal_saved(folder)
        self.assertEqual((saved["extraItems"], saved["extraCosts"], saved["extraFactions"], saved["extraCategories"]),
                         ([g3], [35], ["US"], ["Sniper rifles"]))
        # Its price is changed with the rest of its settings, not as an override.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "price", "prefab": g3, "cost": "50"})
        self.assertEqual(files.arsenal_settings(self.root / "Plus01")[1:3],
                         ({}, {g3: {"cost": 50, "factions": "US", "category": "Sniper rifles"}}))
        added = await (await self.client.get("/arsenal/Plus01", params={"show": "added"})).text()
        self.assertIn("HK G3A3", added)
        self.assertIn("Remove", added)

        # Catalogued items don't need adding, and the category has to be a real one.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "add", "prefab": m9, "cost": "5",
                                                        "category": "Pistols", "faction": ""})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "add", "prefab": g3, "cost": "5",
                                                        "category": "Lasers", "faction": ""})
        self.assertEqual(set(files.arsenal_settings(self.root / "Plus01")[2]), {g3})
        self.assertEqual(files.arsenal_settings(self.root / "Plus01")[2][g3]["cost"], 50)

        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "remove_added", "prefab": g3})
        self.assertEqual(files.arsenal_settings(self.root / "Plus01")[2], {})

    async def test_items_can_be_put_on_other_sides(self):
        folder = self.arsenal_profile()
        m9 = "{AAAA000000000002}Prefabs/Weapons/Handguns/M9/Handgun_M9.et"
        token = await self.login("boss")
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides", "prefab": m9, "side": ["US", "USSR"]})
        saved = self.arsenal_saved(folder)
        self.assertEqual((saved["sideItems"], saved["sideFactions"]), ([m9], ["US,USSR"]))
        ussr = await (await self.client.get("/arsenal/Plus01", params={"faction": "USSR"})).text()
        self.assertIn("Handgun_M9", ussr)
        self.assertIn("normally US", ussr)

        # Only the other side.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides", "prefab": m9, "side": "USSR"})
        self.assertEqual(files.arsenal_sides(self.root / "Plus01"), {m9: "USSR"})
        self.assertNotIn("Handgun_M9", await (await self.client.get("/arsenal/Plus01", params={"faction": "US"})).text())

        # No side at all, or one no item belongs to, changes nothing; the item's
        # own sides clear the override; other changes keep it.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides", "prefab": m9})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides", "prefab": m9, "side": "FIA"})
        self.assertEqual(files.arsenal_sides(self.root / "Plus01"), {m9: "USSR"})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "hide", "prefab": m9})
        self.assertEqual(files.arsenal_sides(self.root / "Plus01"), {m9: "USSR"})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides", "prefab": m9, "side": "US"})
        self.assertEqual(files.arsenal_sides(self.root / "Plus01"), {})

        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides", "prefab": m9, "side": "USSR"})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "reset_sides", "prefab": m9})
        self.assertEqual(files.arsenal_sides(self.root / "Plus01"), {})

    async def test_a_whole_filter_can_be_put_on_other_sides(self):
        folder = self.arsenal_profile(items="scan")
        m16 = "{AAAA000000000001}Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et"
        m9 = "{AAAA000000000002}Prefabs/Weapons/Handguns/M9/Handgun_M9.et"
        token = await self.login("boss")
        # Items found by the scan get their sides when they're added.
        dressing = "{AAAA000000000003}Prefabs/Items/Medicine/FieldDressing_US_01.et"
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides_found", "faction": "US", "side": "USSR"})
        self.assertEqual(files.arsenal_sides(self.root / "Plus01"), {m16: "USSR", m9: "USSR", dressing: "USSR"})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides_found", "q": "m16", "side": ["US", "USSR"]})
        self.assertEqual(files.arsenal_sides(self.root / "Plus01"), {m16: "US,USSR", m9: "USSR", dressing: "USSR"})
        # Back to their own sides clears the change.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides_found", "faction": "USSR", "side": "US"})
        self.assertEqual(files.arsenal_sides(self.root / "Plus01"), {dressing: "US"})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "sides_found"})
        self.assertEqual(len(self.arsenal_saved(folder)["sideItems"]), 1)

    async def test_items_can_be_renamed(self):
        folder = self.arsenal_profile()
        m16 = "{AAAA000000000001}Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et"
        token = await self.login("boss")
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "rename", "prefab": m16, "name": "  Sixteen   A2 "})
        saved = self.arsenal_saved(folder)
        self.assertEqual((saved["renameItems"], saved["renameNames"]), ([m16], ["Sixteen A2"]))
        page = await (await self.client.get("/arsenal/Plus01", params={"q": "sixteen"})).text()
        self.assertIn("Sixteen A2", page)
        self.assertIn("normally M16A2", page)
        self.assertIn("Sixteen A2", await (await self.client.get("/arsenal/Plus01", params={"show": "renamed"})).text())

        # Too long, or a character the game would read as markup, changes nothing.
        for bad in ("x" * 41, "#AR-Name", "<b>M16</b>"):
            await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "rename", "prefab": m16, "name": bad})
        self.assertEqual(files.arsenal_renames(self.root / "Plus01"), {m16: "Sixteen A2"})
        # Other changes keep it.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "hide", "prefab": m16})
        self.assertEqual(files.arsenal_renames(self.root / "Plus01"), {m16: "Sixteen A2"})

        # An empty box, or the item's own name, clears it.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "rename", "prefab": m16, "name": ""})
        self.assertEqual(files.arsenal_renames(self.root / "Plus01"), {})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "rename", "prefab": m16, "name": "Short"})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "rename", "prefab": m16, "name": "M16A2"})
        self.assertEqual(files.arsenal_renames(self.root / "Plus01"), {})

    async def test_the_refund_limit_can_be_changed(self):
        folder = self.arsenal_profile()
        token = await self.login("boss")
        self.assertEqual(files.arsenal_refund_cap(self.root / "Plus01"), 350)
        self.assertIn('value="350"', await (await self.client.get("/arsenal/Plus01")).text())
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "refund_cap", "cost": "200"})
        self.assertEqual(self.arsenal_saved(folder)["refundCap"], 200)
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "refund_cap", "cost": "-5"})
        # Other changes keep it.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "hide",
                                                        "prefab": "{AAAA000000000002}Prefabs/Weapons/Handguns/M9/Handgun_M9.et"})
        self.assertEqual(files.arsenal_refund_cap(self.root / "Plus01"), 200)

    async def test_items_can_be_given_their_own_refund(self):
        folder = self.arsenal_profile()
        m16 = "{AAAA000000000001}Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et"
        token = await self.login("boss")
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "refund", "prefab": m16, "cost": "5"})
        saved = self.arsenal_saved(folder)
        self.assertEqual((saved["refundItems"], saved["refundCosts"]), ([m16], [5]))
        self.assertIn('value="5" placeholder="Refund"', await (await self.client.get("/arsenal/Plus01", params={"q": "m16"})).text())
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "refund", "prefab": m16, "cost": "-1"})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "price", "prefab": m16, "cost": "40"})
        self.assertEqual(files.arsenal_refunds(self.root / "Plus01"), {m16: 5})
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "refund", "prefab": m16, "cost": ""})
        self.assertEqual(files.arsenal_refunds(self.root / "Plus01"), {})

    def vehicle_profile(self):
        folder = self.root / "Plus01" / "arma-reforger" / "1874900" / "AReforgerMaster" / "profile" / "OYB_Core"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "vehicle_list.json").write_text(json.dumps({
            "version": 1,
            "prefabs": ["{V1}Prefabs/Vehicles/Wheeled/M998/M998.et", "{V2}Prefabs/Vehicles/Wheeled/M998/M998_covered.et",
                        "{V3}Prefabs/Vehicles/Helicopters/Mi8/Mi8MT.et"],
            "names": ["M998", "M998_covered", "Mi-8MT"], "categories": ["Cars", "Cars", "Helicopters"],
            "factions": ["US", "US", "USSR"], "prices": [100, 0, 1500],
            "variantOf": ["", "{V1}Prefabs/Vehicles/Wheeled/M998/M998.et", ""]}))
        return folder

    async def test_the_vehicle_page_waits_for_the_server_to_list_its_vehicles(self):
        await self.login("boss")
        page = await (await self.client.get("/vehicles/Plus01")).text()
        self.assertIn("No vehicle list yet", page)
        self.assertIn(">Vehicles</a>", page)

    async def test_vehicles_can_be_hidden_and_repriced(self):
        self.vehicle_profile()
        covered, mi8 = "{V2}Prefabs/Vehicles/Wheeled/M998/M998_covered.et", "{V3}Prefabs/Vehicles/Helicopters/Mi8/Mi8MT.et"
        token = await self.login("boss")
        page = await (await self.client.get("/vehicles/Plus01")).text()
        for name in ("M998", "Mi-8MT", "variant of M998"):
            self.assertIn(name, page)
        self.assertLess(page.index("M998_covered"), page.index("Mi-8MT"))       # variants follow their vehicle

        await self.client.post("/vehicles/Plus01", data={"csrf": token, "action": "hide", "prefab": covered})
        await self.client.post("/vehicles/Plus01", data={"csrf": token, "action": "price", "prefab": mi8, "cost": "900"})
        await self.client.post("/vehicles/Plus01", data={"csrf": token, "action": "price", "prefab": covered, "cost": "1"})
        await self.client.post("/vehicles/Plus01", data={"csrf": token, "action": "hide", "prefab": "{X}Nope.et"})
        settings = files.vehicle_settings(self.root / "Plus01")
        self.assertEqual((settings["hidden"], settings["prices"]), ({covered}, {mi8: 900}))
        await self.client.post("/vehicles/Plus01", data={"csrf": token, "action": "reset", "prefab": mi8})
        await self.client.post("/vehicles/Plus01", data={"csrf": token, "action": "hide_found", "cat": "Helicopters"})
        self.assertEqual(files.vehicle_settings(self.root / "Plus01")["hidden"], {covered, mi8})
        await self.client.post("/vehicles/Plus01", data={"csrf": token, "action": "show_found"})
        self.assertEqual(files.vehicle_settings(self.root / "Plus01")["hidden"], set())

    async def test_building_menu_vehicles_are_listed_with_their_cost(self):
        folder = self.root / "Plus01" / "arma-reforger" / "1874900" / "AReforgerMaster" / "profile" / "OYB_Core"
        folder.mkdir(parents=True, exist_ok=True)
        humvee, variant, uaz, mod = ("{B1}Prefabs/Vehicles/Wheeled/M1025/M1025.et", "{B2}Prefabs/Vehicles/Wheeled/M1025/M1025_MERDC.et",
                                     "{B3}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et", "{B4}Prefabs/Vehicles/Tracked/T72/T72_Mod.et")
        (folder / "vehicle_list.json").write_text(json.dumps({
            "version": 3, "build": "vehicles-test", "scanComplete": True, "catalogueEntries": 0,
            "prefabs": [humvee, variant, uaz, mod], "names": ["M1025", "M1025_MERDC", "UAZ-469", "T-72 (mod)"],
            "categories": ["Cars", "Cars", "Cars", "Armour"], "factions": ["US", "US", "USSR", "USSR"],
            "prices": [-1, -1, 150, -1], "buildCosts": [300, -1, -1, -1],
            "menus": ["building", "building", "depot,building", ""],
            "sources": ["building", "building", "catalogue,scan", "scan"],
            "variantOf": ["", humvee, "", ""]}))
        vehicles = {v["prefab"]: v for v in files.vehicle_list(self.root / "Plus01")}
        self.assertEqual((vehicles[humvee]["menus"], vehicles[humvee]["price"], vehicles[humvee]["build_cost"]),
                         (["building"], None, 300))
        self.assertEqual((vehicles[uaz]["menus"], vehicles[uaz]["price"], vehicles[uaz]["sources"]),
                         (["depot", "building"], 150, ["catalogue", "scan"]))
        self.assertEqual((vehicles[mod]["menus"], vehicles[mod]["sources"]), ([], ["scan"]))
        self.assertEqual(files.vehicle_list_info(self.root / "Plus01")["build"], "vehicles-test")
        token = await self.login("boss")
        page = await (await self.client.get("/vehicles/Plus01")).text()
        for text in ("building menu: 300", "variant of M1025", "T-72 (mod)", "not in a menu", "OYB Core vehicles-test"):
            self.assertIn(text, page)
        post = lambda **data: self.client.post("/vehicles/Plus01", data={"csrf": token, **data})
        await post(action="price", prefab=humvee, cost="50")       # building menu cost of its own
        await post(action="price", prefab=uaz, cost="90")          # depot
        await post(action="price", prefab=variant, cost="10")      # a variant costs what its vehicle costs
        await post(action="price", prefab=mod, cost="10")          # in no menu: a price would do nothing
        await post(action="hide", prefab=humvee)
        await post(action="hide", prefab=mod)
        settings = files.vehicle_settings(self.root / "Plus01")
        self.assertEqual((settings["prices"], settings["hidden"]), ({humvee: 50, uaz: 90}, {humvee, mod}))

    def test_vehicle_settings_are_replaced_whole(self):
        folder = self.vehicle_profile()
        files.save_vehicle_settings(self.root / "Plus01", {"hidden": {"{V1}a.et"}, "prices": {}, "enabled": True,
                                                           "groups": {"Cars": 2}, "members": {"{V1}a.et": "Cars"}})
        self.assertEqual(json.loads((folder / "vehicles.json").read_text())["groupLimits"], [2])
        self.assertFalse((folder / "vehicles.json.new").exists())

    def test_an_old_vehicle_list_is_all_depot_vehicles(self):
        self.vehicle_profile()
        vehicles = files.vehicle_list(self.root / "Plus01")
        self.assertEqual({tuple(v["menus"]) for v in vehicles}, {("depot",)})
        self.assertEqual({v["name"]: v["price"] for v in vehicles}, {"M998": 100, "M998_covered": 0, "Mi-8MT": 1500})

    async def test_vehicles_share_limits_in_groups(self):
        folder = self.vehicle_profile()
        m998, covered, mi8 = ("{V1}Prefabs/Vehicles/Wheeled/M998/M998.et",
                              "{V2}Prefabs/Vehicles/Wheeled/M998/M998_covered.et", "{V3}Prefabs/Vehicles/Helicopters/Mi8/Mi8MT.et")
        token = await self.login("boss")
        post = lambda **data: self.client.post("/vehicles/Plus01", data={"csrf": token, **data})
        await post(action="group_add", name="Helicopters", count="2")
        await post(action="group_add", name="Cars")                                  # empty: unlimited
        await post(action="group_add", name="Helicopters", count="5")                # already there
        await post(action="group_add", name="Bad|name", count="1")
        await post(action="set_group", prefab=mi8, group="Helicopters")
        await post(action="set_group", prefab=covered, group="Cars")                 # variants follow their vehicle
        await post(action="set_group", prefab=m998, group="Nope")
        settings = files.vehicle_settings(self.root / "Plus01")
        self.assertEqual(settings["groups"], {"Helicopters": 2, "Cars": files.VEHICLE_UNLIMITED})
        self.assertEqual(settings["members"], {mi8: "Helicopters"})
        saved = json.loads((folder / "vehicles.json").read_text())
        self.assertEqual((saved["groupNames"], saved["groupLimits"], saved["groupItems"], saved["groupOf"], saved["limitsEnabled"]),
                         (["Cars", "Helicopters"], [-1, 2], [mi8], ["Helicopters"], True))

        await post(action="group_found", cat="Cars", to_group="Cars")                   # the vehicle, not its variant
        self.assertEqual(files.vehicle_settings(self.root / "Plus01")["members"], {mi8: "Helicopters", m998: "Cars"})
        cars = await (await self.client.get("/vehicles/Plus01", params={"group": "Cars"})).text()
        self.assertIn("M998_covered", cars)                                           # its variant counts there too
        self.assertNotIn("Mi-8MT", cars)

        await post(action="group_limit", name="Helicopters", count="0")
        await post(action="limits", enabled="0")
        settings = files.vehicle_settings(self.root / "Plus01")
        self.assertEqual((settings["groups"]["Helicopters"], settings["enabled"]), (0, False))
        await post(action="group_delete", name="Helicopters")
        await post(action="limits", enabled="1")
        settings = files.vehicle_settings(self.root / "Plus01")
        self.assertEqual((settings["groups"], settings["members"], settings["enabled"]),
                         ({"Cars": -1}, {m998: "Cars"}, True))
        actions = {r["action"] for r in self.db.all("SELECT action FROM audit")}
        self.assertTrue({"vehicles group_add", "vehicles set_group", "vehicles group_limit", "vehicles limits"} <= actions)

    async def test_items_can_be_moved_to_another_category(self):
        folder = self.arsenal_profile()
        m9 = "{AAAA000000000002}Prefabs/Weapons/Handguns/M9/Handgun_M9.et"
        token = await self.login("boss")
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "move", "prefab": m9, "category": "Rifles"})
        saved = self.arsenal_saved(folder)
        self.assertEqual((saved["categoryItems"], saved["categoryNames"]), ([m9], ["Rifles"]))
        rifles = await (await self.client.get("/arsenal/Plus01", params={"cat": "Rifles"})).text()
        self.assertIn("Handgun_M9", rifles)
        self.assertIn("normally Pistols", rifles)
        self.assertNotIn("Handgun_M9", await (await self.client.get("/arsenal/Plus01", params={"cat": "Pistols"})).text())
        # Moving it back to where the game put it clears the change.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "move", "prefab": m9, "category": "Pistols"})
        self.assertEqual(files.arsenal_settings(self.root / "Plus01")[3], {})
        # A whole filter at once, and only real categories.
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "move_found", "faction": "US", "category": "Tools"})
        self.assertEqual(set(files.arsenal_settings(self.root / "Plus01")[3].values()), {"Tools"})
        self.assertEqual(len(files.arsenal_settings(self.root / "Plus01")[3]), 3)
        await self.client.post("/arsenal/Plus01", data={"csrf": token, "action": "move", "prefab": m9, "category": "Lasers"})
        self.assertEqual(files.arsenal_settings(self.root / "Plus01")[3][m9], "Tools")

    async def test_hand_written_arsenal_settings_are_read_carefully(self):
        folder = self.arsenal_profile(items=False)
        (folder / "settings.json").write_text(json.dumps({
            "hidden": ["{A}a.et", 5], "priceItems": ["{A}a.et", "{B}b.et", "{C}c.et"], "priceCosts": [10, -3, "x"],
            "extraItems": ["{D}d.et", "{E}e.et"], "extraCosts": [3, True], "extraFactions": ["", ""],
            "extraCategories": ["Lasers", "Rifles"]}))
        self.assertEqual(files.arsenal_settings(self.root / "Plus01"),
                         ({"{A}a.et"}, {"{A}a.et": 10}, {"{D}d.et": {"cost": 3, "factions": "", "category": "Other"}}, {}))
        self.assertIsNone(files.arsenal_items(self.root / "Plus01"))

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
        amp.instances = mock.AsyncMock(return_value={"Plus01": {"FriendlyName": "OYB Plus"}})
        self.app[AMP] = amp
        (self.root / "Plus01" / "GenericModule.kvp").write_text(
            'App.AppSettings={"name":"[OYB] Plus | Modded","$MaxUsers":"64","mods":"{\\"modId\\":\\"60C4CE4888FF4621\\"}",'
            '"admins":"\\"a0b1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d\\""}')
        token = await self.login("chief")
        with mock.patch("panel.files.amp_running", return_value=False):
            page = await (await self.client.get("/manage")).text()
        self.assertIn('action="/instances/Plus01/start"', page)
        for words in (">OYB Plus</a>", "[OYB] Plus | Modded", "<span> / 64</span>", "AMP off", "Servers running"):
            self.assertIn(words, page)
        await self.client.post("/instances/Plus01/start", data={"csrf": token})
        amp.instance_power.assert_awaited_once_with("Plus01", "start")
        self.assertEqual((await self.client.post("/instances/..%2fx/start", data={"csrf": token})).status, 404)

    def test_a_running_game_reports_its_memory_and_uptime(self):
        proc = Path(tempfile.mkdtemp())
        (proc / "uptime").write_text("10000.0 1.0\n")
        game = proc / "321"
        game.mkdir()
        os.symlink(f"{self.root}/Plus01/arma-reforger/1874900/ArmaReforgerServer", game / "exe")
        ticks = os.sysconf("SC_CLK_TCK")
        started = 10000 - 3600                                      # an hour ago, in seconds since boot
        (game / "stat").write_text("321 (ArmaReforgerSer) S " + " ".join(["0"] * 18) + f" {started * ticks} 0 0\n")
        (game / "status").write_text("Name:\tArmaReforgerSer\nVmRSS:\t5242880 kB\n")
        found = files.game_process(self.root / "Plus01", proc)
        self.assertEqual(found["memory"], 5 * 1024 ** 3)
        self.assertAlmostEqual(found["up"], 3600, delta=5)
        self.assertIsNone(files.game_process(self.root / "Other", proc))

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


    async def test_making_a_new_server_copies_the_template_and_settings_then_finishes_in_the_background(self):
        (self.root / "Plus01" / "GenericModule.kvp").write_text(
            "Meta.DisplayName=Arma Reforger\nMeta.ConfigManifest=arma-reforgerconfig.json\n"
            "Meta.MetaConfigManifest=arma-reforgermetaconfig.json\nMeta.ConfigRoot=arma-reforger.kvp\n"
            "Meta.Author=Greelan\n"
            'App.AppSettings={"name":"OYB Plus","scenarioId":"{ECC}","password":"old","adminPassword":"old",'
            '"$MaxUsers":"64","mods":"{\\"modId\\":\\"60C4CE4888FF4621\\"}","admins":"\\"a\\"","maxFPS":"120",'
            '"missionHeader":"\\"m_fXpMultiplier\\": 3,\\n\\"m_iStartingHQSupplies\\": 1200,\\n\\"m_bCustomBaseWhitelist\\": 1",'
            '"CustomServerArgs":"-playerLimits=US:54,USSR:54,FIA:20 -fXpMultiplier:3,-noThrow"}')
        (self.root / "Plus01" / "configmanifest.json").write_text(json.dumps([
            {"FieldName": "scenarioId", "EnumValues": {"{ECC}": "Conflict - Everon", "{ARL}": "Conflict - Arland"}}]))
        (self.root / "Plus01" / "AMPConfig.conf").write_text("Webserver.Port=8084\n")
        instance = {"InstanceID": "n", "InstanceName": "Arland02"}
        amp = mock.Mock()
        amp.create_instance = mock.AsyncMock()
        amp.instances = mock.AsyncMock(return_value={"Arland02": instance})
        amp.status = mock.AsyncMock(side_effect=[{"State": 0}, {"State": 75}, {"State": 0}])
        amp.set_configs = mock.AsyncMock(return_value={})
        amp.power = mock.AsyncMock()
        self.app[AMP] = amp
        token = await self.login("chief")
        page = await (await self.client.get("/manage/new")).text()
        self.assertIn("Conflict - Arland", page)
        self.assertIn('value="64"', page)
        self.assertIn('name="xp" value="3"', page)                    # read from the copied server's header
        self.assertIn('name="fia" value="20"', page)
        await self.client.post("/manage/new", data={"csrf": token, "name": "bad name", "copy_from": "Plus01",
                                                     "server_name": "X"})
        amp.create_instance.assert_not_awaited()
        real = web_module.finish_setup
        with mock.patch("panel.web.finish_setup", new=lambda app, name, settings, start, **more: real(
                app, name, settings, start, pause=0.001, **more)):
            await self.client.post("/manage/new", data={
                "csrf": token, "name": "Arland02", "friendly": "Arland 2", "copy_from": "Plus01", "copy": "1",
                "copy_mods": "1", "server_name": "OYB Arland", "scenario": "{ARL}", "players": "100",
                "admin_password": "newadmin1", "visible": "1", "start": "1", "xp": "1.5", "supplies": "2000",
                "us": "60", "ussr": "60", "fia": "8", "fps": "90", "quick_stop": "1"})
            for _ in range(200):
                if self.app[web_module.SETUPS]["Arland02"]["done"]:
                    break
                await asyncio.sleep(0.01)
        amp.create_instance.assert_awaited_once_with("Arland02", "Arland 2", 8085, {
            "GenericModule.Meta.DisplayName": "Arma Reforger", "GenericModule.Meta.ConfigManifest": "arma-reforgerconfig.json",
            "GenericModule.Meta.MetaConfigManifest": "arma-reforgermetaconfig.json",
            "GenericModule.Meta.ConfigRoot": "arma-reforger.kvp"})
        self.assertTrue(self.app[web_module.SETUPS]["Arland02"]["done"], self.app[web_module.SETUPS]["Arland02"])
        applied = amp.set_configs.await_args_list[0].args[1]
        self.assertEqual(applied["Meta.GenericModule.name"], "OYB Arland")
        self.assertEqual(applied["Meta.GenericModule.scenarioId"], "{ARL}")
        self.assertEqual(applied["Meta.GenericModule.$MaxUsers"], "100")
        self.assertEqual(applied["Meta.GenericModule.adminPassword"], "newadmin1")
        self.assertNotIn("Meta.GenericModule.password", applied)                       # never copied
        self.assertEqual(applied["Meta.GenericModule.admins"], "")                     # admins not ticked
        self.assertIn("60C4CE4888FF4621", applied["Meta.GenericModule.mods"])
        self.assertEqual((applied["Meta.GenericModule.visible"], applied["Meta.GenericModule.crossPlatform"]),
                         ("true", "false"))
        header = applied["Meta.GenericModule.missionHeader"]
        self.assertIn('"m_fXpMultiplier": 1.5', header)
        self.assertIn('"m_iStartingHQSupplies": 2000', header)
        self.assertIn('"m_bCustomBaseWhitelist": 1', header)                          # the rest kept
        self.assertEqual(applied["Meta.GenericModule.CustomServerArgs"],
                         "-playerLimits=US:60,USSR:60,FIA:8 -fXpMultiplier=1.5,-noThrow")
        self.assertEqual(applied["Meta.GenericModule.maxFPS"], "90")
        amp.set_configs.assert_any_await(instance, {"GenericModule.App.ExitTimeout": "5"})
        amp.power.assert_awaited_once_with(instance, "start")
        self.assertEqual(self.app[web_module.SETUPS]["Arland02"]["step"], "Started")
        self.assertIn("Setting up Arland02", await (await self.client.get("/manage")).text())

    async def test_mods_from_our_other_servers_can_be_listed_and_added(self):
        (self.root / "Arland02").mkdir()
        (self.root / "Arland02" / "GenericModule.kvp").write_text('App.AppSettings={"mods":"' + json.dumps(
            [{"modId": "6898FB528382C1E4", "name": "AK74 RIS Rail"}, {"modId": "60C4CE4888FF4621", "name": "ACE Core"}]
        ).replace('"', '\\"') + '"}')
        token = await self.login("chief")
        page = await (await self.client.get("/mods/Plus01", params={"tag": "ours"})).text()
        self.assertIn("AK74 RIS Rail", page)
        self.assertIn("on Arland02", page)
        searched = await (await self.client.get("/mods/Plus01", params={"tag": "ours", "q": "ace"})).text()
        self.assertIn("ACE Core", searched)
        self.assertNotIn("AK74 RIS Rail", searched)
        await self.client.post("/mods/Plus01", data=[("csrf", token), ("action", "add_many"),
                                                     ("pick", "6898FB528382C1E4:AK74 RIS Rail")])
        self.assertEqual([m["modId"] for m in files.parse_mods(files.kvp_mods(self.root / "Plus01"))], ["6898FB528382C1E4"])
        self.assertIn("On this server", await (await self.client.get("/mods/Plus01", params={"tag": "ours"})).text())

    async def test_ticking_several_mods_adds_them_in_one_go(self):
        token = await self.login("chief")
        picks = [("pick", "6A263A54663F2EDB:AdvancedFlashBangSystem"), ("pick", "not-an-id:Nope"),
                 ("pick", "6a843b0ec5b8bd3b:AKMRISRail"), ("pick", "6A263A54663F2EDB:AdvancedFlashBangSystem")]
        await self.client.post("/mods/Plus01", data=[("csrf", token), ("action", "add_many")] + picks)
        mods = files.parse_mods(files.kvp_mods(self.root / "Plus01"))
        self.assertEqual([(m["modId"], m["name"]) for m in mods],
                         [("6A263A54663F2EDB", "AdvancedFlashBangSystem"), ("6A843B0EC5B8BD3B", "AKMRISRail")])
        self.assertTrue(self.db.one("SELECT 1 FROM audit WHERE action = 'add mods'"))
        again = await self.client.post("/mods/Plus01", data=[("csrf", token), ("action", "add_many")] + picks[:1])
        self.assertIn("1 already on the list", await again.text())
        self.assertEqual(len(files.parse_mods(files.kvp_mods(self.root / "Plus01"))), 2)
        empty = await self.client.post("/mods/Plus01", data=[("csrf", token), ("action", "add_many")])
        self.assertIn("Tick at least one mod", await empty.text())

    async def test_a_servers_map_comes_from_the_scenario_it_is_set_to_play(self):
        (self.root / "Plus01" / "GenericModule.kvp").write_text(
            'App.AppSettings={"name":"OYB Plus","scenarioId":"{ARL}"}')
        (self.root / "Plus01" / "configmanifest.json").write_text(json.dumps([
            {"FieldName": "scenarioId", "EnumValues": {"{ECC}": "Conflict - Everon", "{ARL}": "Game Master - Arland"}}]))
        self.assertEqual(files.map_name(self.root / "Plus01"), "Arland")
        config = ServerConfig("plus", "Plus", log_dir=f"{self.root}/Plus01/arma-reforger/1874900/AReforgerMaster/logs")
        self.assertEqual(files.server_map(config), "Arland")
        by_name = ServerConfig("plus", "Plus", amp_instance="Plus01", log_dir=f"{self.root}/Elsewhere/logs")
        self.assertEqual(files.server_map(by_name), "Arland")
        (self.root / "Plus01" / "GenericModule.kvp").write_text('App.AppSettings={"scenarioId":"{ECC}"}')
        os.utime(self.root / "Plus01" / "GenericModule.kvp", (time.time() + 5, time.time() + 5))
        self.assertEqual(files.map_name(self.root / "Plus01"), "Everon")
        self.assertEqual(files.map_name(self.root / "Missing"), "")
        self.assertEqual(files.server_map(ServerConfig("x", "X")), "")

    async def test_new_game_puts_the_save_aside_and_starts_the_server_again(self):
        profile = self.root / "Plus01" / "arma-reforger" / "1874900" / "AReforgerMaster" / "profile"
        (profile / ".save").mkdir(parents=True)
        (profile / ".save" / "world.dat").write_text("progress")
        trash = self.root / "Plus01" / "arma-reforger" / "1874900" / ".trash" / "AReforgerMaster" / "profile" / ".save.1"
        trash.mkdir(parents=True)
        amp = mock.Mock()
        amp.instance_power = mock.AsyncMock()
        self.app[AMP] = amp
        self.db.add_user("mod", auth.hash_password("mod-password"), "moderator")
        await self.client.post("/login", data={"username": "mod", "password": "mod-password"})
        page = await (await self.client.get("/guide")).text()
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        reply = await self.client.post("/manage/Plus01/newgame", data={"csrf": token})
        self.assertEqual(reply.status, 403)
        self.assertTrue((profile / ".save").exists())
        token = await self.login("chief")
        with mock.patch("panel.files.amp_running", side_effect=[True, False]):
            await self.client.post("/manage/Plus01/newgame", data={"csrf": token})
        self.assertEqual([c.args for c in amp.instance_power.await_args_list],
                         [("Plus01", "stop"), ("Plus01", "start")])
        self.assertFalse((profile / ".save").exists())
        kept = list((self.root.parent / files.SAVE_BACKUPS / "Plus01").glob("*/.save/world.dat"))
        self.assertEqual([p.read_text() for p in kept], ["progress"])
        self.assertTrue(trash.exists())
        self.assertTrue(self.db.one("SELECT 1 FROM audit WHERE action = 'new game'"))
        self.assertIn("is on a new game", await (await self.client.get("/manage/Plus01")).text())

    async def test_new_game_on_a_stopped_server_does_not_start_it(self):
        profile = self.root / "Plus01" / "arma-reforger" / "1874900" / "AReforgerMaster" / "profile"
        (profile / ".save").mkdir(parents=True)
        amp = mock.Mock()
        amp.instance_power = mock.AsyncMock()
        self.app[AMP] = amp
        token = await self.login("chief")
        with mock.patch("panel.files.amp_running", return_value=False):
            await self.client.post("/manage/Plus01/newgame", data={"csrf": token})
        amp.instance_power.assert_not_awaited()
        self.assertFalse((profile / ".save").exists())

    async def test_new_game_with_no_save_changes_nothing(self):
        amp = mock.Mock()
        amp.instance_power = mock.AsyncMock()
        self.app[AMP] = amp
        token = await self.login("chief")
        await self.client.post("/manage/Plus01/newgame", data={"csrf": token})
        amp.instance_power.assert_not_awaited()

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

    async def test_a_new_manager_gets_the_walkthrough_once(self):
        self.db.add_user("newmgr", auth.hash_password("newmgr-password"), "manager")
        await self.client.post("/login", data={"username": "newmgr", "password": "newmgr-password"})
        first = await (await self.client.get("/manage")).text()
        self.assertIn('data-done="/tour/manage/done"', first)
        self.assertIn("Welcome, newmgr", first)
        self.assertIn("/tour/manage/done", await (await self.client.get("/manage")).text())
        token = re.search(r'name="csrf" value="([^"]+)"', first).group(1)
        await self.client.post("/tour/manage/done", data={"csrf": token})
        again = await (await self.client.get("/manage")).text()
        self.assertNotIn('id="tour"', again)
        # Someone who saw the first version of the walkthrough gets the updated one once.
        self.db.write("UPDATE users SET manage_toured = 1 WHERE username = ?", "newmgr")
        await self.client.post("/logout", data={"csrf": token})
        await self.client.post("/login", data={"username": "newmgr", "password": "newmgr-password"})
        self.assertIn('data-done="/tour/manage/done"', await (await self.client.get("/manage")).text())
        # A senior admin is not a manager, so it never shows for them.
        await self.client.post("/logout", data={"csrf": token})
        self.db.add_user("senior", auth.hash_password("senior-password"), "owner")
        await self.client.post("/login", data={"username": "senior", "password": "senior-password"})
        self.assertNotIn('id="tour"', await (await self.client.get("/mods/Plus01")).text())
        # A moderator who has been given the Files grant runs servers too, so they get the walkthrough.
        self.db.add_user("helper", auth.hash_password("helper-password"), "moderator")
        self.db.set_grants(self.db.one("SELECT id FROM users WHERE username = ?", "helper")["id"], {"files"})
        await self.client.post("/logout", data={"csrf": token})
        await self.client.post("/login", data={"username": "helper", "password": "helper-password"})
        self.assertIn('data-done="/tour/manage/done"', await (await self.client.get("/manage")).text())


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
        self.db.add_user("manager2", auth.hash_password("manager2-password"), "manager")
        self.db.set_grants(self.db.user_by_name("manager2")["id"], {"console"})
        await self.client.post("/logout", data={"csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1)})
        await self.client.post("/login", data={"username": "manager2", "password": "manager2-password"})
        self.assertEqual((await self.client.get("/settings/amp")).status, 403)   # even with the box console ticked
        await self.client.post("/logout", data={"csrf": re.search(r'name="csrf" value="([^"]+)"', await (await self.client.get("/")).text()).group(1)})
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
