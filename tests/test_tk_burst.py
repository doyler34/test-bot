import unittest

from panel.tk_burst import TkBurst

ROOK = "a0b1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d"


def tk(at, victim="Marsh", killer=ROOK):
    return {"kind": "teamkill", "at": at, "killer": killer, "killer_name": "Pte Rook", "victim_name": victim}


class TkBurstTests(unittest.TestCase):
    def test_three_in_a_minute_alerts_once(self):
        burst = TkBurst()
        self.assertIsNone(burst.feed(tk(1000, "A"), 3, 60, 1000))
        self.assertIsNone(burst.feed(tk(1020, "B"), 3, 60, 1020))
        alert = burst.feed(tk(1040, "C"), 3, 60, 1040)
        self.assertEqual((alert["count"], alert["seconds"], alert["victims"]), (3, 40, ["A", "B", "C"]))
        self.assertIsNone(burst.feed(tk(1050, "D"), 3, 60, 1050))

    def test_spread_out_teamkills_do_not_alert(self):
        burst = TkBurst()
        for at in (1000, 1070, 1140, 1210):
            self.assertIsNone(burst.feed(tk(at), 3, 60, at))

    def test_old_log_lines_do_not_alert(self):
        burst = TkBurst()
        for at in (1000, 1010, 1020):
            self.assertIsNone(burst.feed(tk(at), 3, 60, 5000))

    def test_ordinary_kills_are_ignored(self):
        burst = TkBurst()
        for at in (1000, 1001, 1002):
            self.assertIsNone(burst.feed(dict(tk(at), kind="kill"), 3, 60, at))

    def test_alerts_again_after_the_cooldown(self):
        burst = TkBurst()
        for at in (1000, 1001, 1002):
            burst.feed(tk(at), 3, 60, at)
        for at in (1400, 1401):
            self.assertIsNone(burst.feed(tk(at), 3, 60, at))
        self.assertIsNotNone(burst.feed(tk(1402), 3, 60, 1402))


if __name__ == "__main__":
    unittest.main()
