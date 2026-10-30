import unittest
from pathlib import Path

from panel.db import PanelDB
from panel.feedsides import feed_parts, sides_from


class SidesTests(unittest.TestCase):
    def test_newest_pick_wins(self):
        newest_first = ["Ann joined USSR", "Bob joined FIA", "Ann joined US", "not a side row"]
        self.assertEqual(sides_from(newest_first), {"Ann": "USSR", "Bob": "FIA"})

    def test_join_and_leave_name_the_player(self):
        sides = {"Ann": "US"}
        self.assertEqual(feed_parts("join", "Ann connected", sides), [("us", "Ann"), ("", " connected")])
        self.assertEqual(feed_parts("leave", "Cal disconnected", sides), [("none", "Cal"), ("", " disconnected")])

    def test_a_side_row_shows_the_side_just_picked(self):
        self.assertEqual(feed_parts("side", "Ann joined FIA", {"Ann": "US"}), [("fia", "Ann"), ("", " joined FIA")])

    def test_civilians_and_unknown_sides_are_pink(self):
        self.assertEqual(feed_parts("side", "Ann joined CIV", {}), [("none", "Ann"), ("", " joined CIV")])

    def test_kills_mark_both_players(self):
        sides = {"Ann": "US", "Bob": "USSR"}
        self.assertEqual(feed_parts("kill", "Ann killed Bob (120 m) · explosive", sides),
                         [("us", "Ann"), ("", " killed "), ("ussr", "Bob"), ("", " (120 m) · explosive")])
        self.assertEqual(feed_parts("teamkill", "Ann teamkilled Dee", sides),
                         [("us", "Ann"), ("", " teamkilled "), ("none", "Dee"), ("", "")])

    def test_the_ai_gets_no_chip(self):
        self.assertEqual(feed_parts("kill", "AI killed Bob (30 m)", {"Bob": "FIA"}),
                         [("", "AI"), ("", " killed "), ("fia", "Bob"), ("", " (30 m)")])

    def test_suicide(self):
        self.assertEqual(feed_parts("kill", "Bob killed themselves", {"Bob": "FIA"}),
                         [("fia", "Bob"), ("", " killed themselves")])

    def test_other_rows_are_left_alone(self):
        self.assertEqual(feed_parts("rcon", "Ann killed Bob", {"Ann": "US"}), [("", "Ann killed Bob")])
        self.assertEqual(feed_parts("kill", "something odd", {}), [("", "something odd")])


class MarkupTests(unittest.TestCase):
    def test_tags_do_not_reuse_the_sidebar_class(self):
        """The sidebar's own .side rule pins it to the left edge, so a tag called "side" would cover it."""
        template = Path("panel/templates/_feed.html").read_text()
        self.assertIn("sidetag", template)
        self.assertNotRegex(template, r'class="side[ "]')
        css = Path("panel/static/style.css").read_text()
        self.assertNotRegex(css, r"\.feed \.side[ {-]")


class FeedTests(unittest.TestCase):
    def test_feed_rows_carry_their_parts(self):
        db = PanelDB(":memory:")
        db.add_feed("one", "side", "Ann joined USSR", at=100)
        db.add_feed("one", "kill", "Ann killed Bob (10 m)", at=101)
        db.add_feed("one", "rcon", "hello", at=102)
        db.add_feed("two", "side", "Bob joined US", at=103)
        rows = {r["kind"]: r for r in db.feed("one")}
        self.assertEqual(rows["kill"]["parts"][0], ("ussr", "Ann"))
        self.assertEqual(rows["kill"]["parts"][2], ("none", "Bob"))
        self.assertEqual(rows["rcon"]["parts"], [("", "hello")])
        self.assertEqual(rows["kill"]["text"], "Ann killed Bob (10 m)")


if __name__ == "__main__":
    unittest.main()
