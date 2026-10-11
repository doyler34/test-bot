import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "auto_update.sh"

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
# Answers like systemctl for two services; records restarts; a service named in $ROOT/broken won't start.
unit=""; for a in "$@"; do case "$a" in *.service) unit="$a";; esac; done
case "$1" in
  show)
    case "$4" in
      LoadState) echo loaded ;;
      User) echo "" ;;
      WorkingDirectory) [ "$unit" = oyb-panel.service ] && echo "$ROOT/panel" || echo "$ROOT/bot" ;;
    esac ;;
  restart) echo "$unit" >> "$ROOT/restarts" ;;
  is-active) ! grep -qx "$unit" "$ROOT/broken" 2>/dev/null ;;
esac
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class AutoUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(self.root)}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.root / "origin.git")], check=True)
        work = self.root / "work"
        subprocess.run(["git", "clone", "-q", str(self.root / "origin.git"), str(work)], check=True)
        (work / "panel").mkdir()
        (work / "bot").mkdir()
        (work / "panel" / "web.py").write_text("v1\n")
        (work / "bot" / "main.py").write_text("v1\n")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "first")
        git(work, "push", "-q", "origin", "HEAD:main")
        for name in ("panel", "bot"):
            subprocess.run(["git", "clone", "-q", str(self.root / "origin.git"), str(self.root / name)], check=True)
        fake = self.root / "systemctl"
        fake.write_text(FAKE_SYSTEMCTL)
        fake.chmod(0o755)
        self.work = work

    def run_update(self):
        env = {**os.environ, "SYSTEMCTL": str(self.root / "systemctl"), "ROOT": str(self.root),
               "OYB_UPDATE_LOG": str(self.root / "update.log"), "OYB_UPDATE_WAIT": "0"}
        subprocess.run(["bash", str(SCRIPT)], check=True, env=env)
        restarts = self.root / "restarts"
        done = restarts.read_text().split() if restarts.exists() else []
        restarts.unlink(missing_ok=True)
        return done

    def push(self, path, text):
        (self.work / path).write_text(text)
        git(self.work, "commit", "-qam", f"change {path}")
        git(self.work, "push", "-q", "origin", "HEAD:main")

    def test_nothing_new_restarts_nothing(self):
        self.assertEqual(self.run_update(), [])
        self.assertFalse((self.root / "update.log").exists() and (self.root / "update.log").read_text())

    def test_a_panel_change_restarts_only_the_panel(self):
        self.push("panel/web.py", "v2\n")
        self.assertEqual(self.run_update(), ["oyb-panel.service"])
        self.assertEqual((self.root / "panel" / "panel" / "web.py").read_text(), "v2\n")
        self.assertEqual((self.root / "bot" / "panel" / "web.py").read_text(), "v2\n")
        log = (self.root / "update.log").read_text()
        self.assertIn("reforger-timer.service: nothing it uses changed, not restarted", log)
        self.assertEqual(self.run_update(), [])

    def test_a_bot_that_wont_start_is_put_back(self):
        before = git(self.root / "bot", "rev-parse", "HEAD")
        (self.root / "broken").write_text("reforger-timer.service\n")
        self.push("bot/main.py", "v2\n")
        restarts = self.run_update()
        self.assertEqual(restarts.count("reforger-timer.service"), 2)
        self.assertEqual(git(self.root / "bot", "rev-parse", "HEAD"), before)
        self.assertEqual((self.root / "bot" / "bot" / "main.py").read_text(), "v1\n")
        self.assertIn("didn't start", (self.root / "update.log").read_text())

    def test_hand_made_commits_are_left_alone(self):
        (self.root / "bot" / "bot" / "main.py").write_text("local\n")
        git(self.root / "bot", "commit", "-qam", "local fix")
        self.push("bot/main.py", "v2\n")
        self.run_update()
        self.assertEqual((self.root / "bot" / "bot" / "main.py").read_text(), "local\n")
        self.assertIn("wasn't touched", (self.root / "update.log").read_text())


if __name__ == "__main__":
    unittest.main()
