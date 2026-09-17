import sqlite3
import tempfile
import unittest
from pathlib import Path
from bot.storage.account_links import AccountLinks, LinkConflict


class AccountLinkTests(unittest.TestCase):
    def test_approval_rejection_and_stale_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            links = AccountLinks(Path(tmp) / "links.sqlite3")
            try:
                identity = "11111111-2222-3333-4444-555555555555"
                old = links.submit(1, 10, identity, "Player")
                current = links.submit(1, 10, identity, "Player")
                with self.assertRaises(LinkConflict):
                    links.review(1, old, 99, True)
                self.assertIsNone(links.lookup(1, 10))
                links.review(1, current, 99, False)
                self.assertIsNone(links.lookup(1, 10))
                current = links.submit(1, 10, identity, "Player")
                competing = links.submit(1, 20, identity, "Player")
                links.review(1, current, 99, True)
                with self.assertRaises(LinkConflict):
                    links.review(1, competing, 99, True)
                self.assertEqual(links.lookup(1, 10), identity)
                self.assertIsNone(links.lookup(1, 20))
            finally:
                links.close()

    def test_unlink_removes_link_and_frees_the_game_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            links = AccountLinks(Path(tmp) / "links.sqlite3")
            try:
                identity = "11111111-2222-3333-4444-555555555555"
                links.verified_link(1, 10, identity, "admin:1")
                self.assertEqual(links.unlink(1, 10), [identity])
                self.assertIsNone(links.lookup(1, 10))
                self.assertEqual(links.unlink(1, 10), [])  # already gone
                # The game account and a different Discord user can both link again.
                links.verified_link(1, 20, identity, "admin:1")
                self.assertEqual(links.lookup(1, 20), identity)
            finally:
                links.close()

    def test_member_keyed_database_is_rebuilt_in_place(self):
        # The shape running in production before a member could hold more than
        # one platform. Fresh databases never exercise this path.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "links.sqlite3"
            mine = "11111111-2222-3333-4444-555555555555"
            theirs = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            old = sqlite3.connect(path)
            old.execute("""CREATE TABLE account_links (guild INTEGER NOT NULL,
                discord_id INTEGER NOT NULL, identity TEXT NOT NULL, linked_at REAL NOT NULL,
                verified_by TEXT NOT NULL, PRIMARY KEY(guild,discord_id), UNIQUE(guild,identity))""")
            old.executemany("INSERT INTO account_links VALUES (?,?,?,?,?)",
                            [(1, 10, mine, 1000.0, "admin:1"), (1, 20, theirs, 1001.0, "admin:1")])
            old.commit()
            old.close()
            links = AccountLinks(path)
            try:
                self.assertEqual(links.identities(1, 10), [mine])
                self.assertEqual(links.identities(1, 20), [theirs])
                # The point of the rebuild: a second platform now fits.
                links.verified_link(1, 10, "99999999-8888-7777-6666-555555555555", "admin:1")
                self.assertEqual(len(links.identities(1, 10)), 2)
                # And an account someone else holds is still off limits.
                with self.assertRaises(LinkConflict):
                    links.verified_link(1, 30, theirs, "admin:1")
            finally:
                links.close()

    def test_persistence_and_duplicate_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "links.sqlite3"
            identity = "11111111-2222-3333-4444-555555555555"
            links = AccountLinks(path)
            try:
                links.verified_link(1, 10, identity, "test-verifier")
                links.verified_link(1, 10, identity, "test-verifier")
                with self.assertRaises(LinkConflict):
                    links.verified_link(1, 20, identity, "test-verifier")
                # A second platform is allowed; a fourth account is not.
                links.verified_link(1, 10, "22222222-2222-3333-4444-555555555555", "test-verifier")
                links.verified_link(1, 10, "33333333-2222-3333-4444-555555555555", "test-verifier")
                self.assertEqual(len(links.identities(1, 10)), 3)
                with self.assertRaises(LinkConflict):
                    links.verified_link(1, 10, "44444444-2222-3333-4444-555555555555", "test-verifier")
            finally:
                links.close()
            links = AccountLinks(path)
            try:
                self.assertEqual(links.lookup(1, 10), identity)
                self.assertIsNone(links.lookup(1, 20))
            finally:
                links.close()
