"""Regresi lifecycle bot tanpa Telegram, MCP, atau data pasar."""
import asyncio
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("BOT_TOKEN", "dummy:offline-test")
import bot_telegram as bot
from telegram.error import Conflict
from telegram.ext import Application


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lock = self.root / "bot.lock"
        self.pid = self.root / "bot.pid"
        self.patches = [patch.object(bot, "_LOCK_PATH", self.lock),
                        patch.object(bot, "_PID_PATH", self.pid)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        bot._cleanup_lock()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_conflict_uses_real_sync_api(self):
        app = Mock(spec=Application)
        with contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(bot.on_error(None, SimpleNamespace(
                error=Conflict("terminated by other getUpdates"), application=app)))
        app.stop_running.assert_called_once_with()

    def test_other_error_does_not_stop(self):
        app = Mock(spec=Application)
        with contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(bot.on_error(None, SimpleNamespace(
                error=RuntimeError("offline error"), application=app)))
        app.stop_running.assert_not_called()

    def test_stale_pid_and_reacquisition(self):
        self.pid.write_text("999999", encoding="utf-8")
        bot._ensure_single_instance()
        self.assertEqual(self.pid.read_text(), str(os.getpid()))
        first = bot._lock_fh
        bot._ensure_single_instance()
        self.assertIs(first, bot._lock_fh)
        bot._cleanup_lock()
        self.assertFalse(self.pid.exists())
        self.assertTrue(self.lock.exists())
        bot._ensure_single_instance()
        self.assertEqual(self.pid.read_text(), str(os.getpid()))

    def test_second_process_rejected_without_changing_owner(self):
        bot._ensure_single_instance()
        code = (
            "import sys; from pathlib import Path; "
            "sys.path.insert(0, sys.argv[1]); import bot_telegram as b; "
            "b._LOCK_PATH=Path(sys.argv[2]); b._PID_PATH=Path(sys.argv[3]); "
            "b._ensure_single_instance()"
        )
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, "-c", code, str(Path(bot.__file__).parent),
                 str(self.lock), str(self.pid)], cwd=self.root,
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Bot lain masih berjalan", result.stderr)
            self.assertEqual(self.pid.read_text(), str(os.getpid()))
            self.assertTrue(self.lock.exists())

    def test_failed_acquisition_has_no_cleanup_ownership(self):
        self.pid.write_text("12345", encoding="utf-8")
        with patch.object(bot.msvcrt, "locking", side_effect=OSError("busy")):
            with self.assertRaises(SystemExit):
                bot._ensure_single_instance()
        self.assertIsNone(bot._lock_fh)
        bot._cleanup_lock()
        self.assertEqual(self.pid.read_text(), "12345")


if __name__ == "__main__":
    unittest.main(verbosity=2)
