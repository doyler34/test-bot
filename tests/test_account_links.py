import tempfile
import unittest
from pathlib import Path
from account_links import AccountLinks, LinkConflict


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
                with self.assertRaises(LinkConflict):
                    links.verified_link(1, 10, "22222222-2222-3333-4444-555555555555", "test-verifier")
            finally:
                links.close()
            links = AccountLinks(path)
            try:
                self.assertEqual(links.lookup(1, 10), identity)
                self.assertIsNone(links.lookup(1, 20))
            finally:
                links.close()
