"""
Tests for the vanilla Reforger log-line parser.

Run with pytest, or directly: ``python tests/test_parser.py``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reforger_monitor import LineEvent, parse_line  # noqa: E402

GAME_START = "02:45:31.127 SCRIPT      : SCR_BaseGameMode::OnGameStateChanged = GAME"
GAME_END = "12:34:56.789 SCRIPT      : SCR_BaseGameMode::OnGameStateChanged = POSTGAME"
HEARTBEAT = "12:34:56.789 DEFAULT      : FPS: 60.5, Mem: 2589841 kB, Player: 12, AI: 340"
PREGAME = "02:45:10.001 SCRIPT      : SCR_BaseGameMode::OnGameStateChanged = PREGAME"
NOISE = "02:45:31.127 SCRIPT      : some other unrelated log line"


def test_game_start():
    parsed = parse_line(GAME_START)
    assert parsed is not None
    assert parsed.event is LineEvent.GAME_START


def test_game_end():
    parsed = parse_line(GAME_END)
    assert parsed is not None
    assert parsed.event is LineEvent.GAME_END


def test_heartbeat_players():
    parsed = parse_line(HEARTBEAT)
    assert parsed is not None
    assert parsed.event is LineEvent.HEARTBEAT
    assert parsed.players == 12


def test_pregame_is_not_start():
    # Only GAME (not PREGAME/POSTGAME) counts as a session start.
    assert parse_line(PREGAME) is None


def test_noise_ignored():
    assert parse_line(NOISE) is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
