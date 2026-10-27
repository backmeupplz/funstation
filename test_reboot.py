"""Run with python3 -m unittest; no Pi hardware or real reboot required."""
import importlib.util
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("funstation", Path(__file__).parent / "pi/funstation.py")
app = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {name: MagicMock() for name in ("evdev", "websocket", "tkinter")}):
    spec.loader.exec_module(app)


class RebootTest(unittest.TestCase):
    def test_games_finish_saving_before_reboot(self):
        calls = MagicMock()
        with patch.dict(app.games_running, {"game.gba": calls.game}, clear=True), \
                patch.object(app.subprocess, "run", calls.run):
            app.reboot()
        self.assertEqual([c[0] for c in calls.mock_calls],
                         ["game.send_signal", "game.terminate", "game.wait", "run"])
        calls.game.send_signal.assert_called_once_with(signal.SIGCONT)
        calls.game.wait.assert_called_once_with(timeout=10)
        calls.run.assert_called_once_with(["sudo", "-n", "systemctl", "reboot"],
                                          check=True, capture_output=True, timeout=10)

    def test_save_timeout_aborts_reboot(self):
        game = MagicMock()
        game.wait.side_effect = subprocess.TimeoutExpired("mgba", 10)
        with patch.dict(app.games_running, {"game.gba": game}, clear=True), \
                patch.object(app.subprocess, "run") as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                app.reboot()
            run.assert_not_called()

    def test_http_reboot_origin_and_errors(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/reboot"
        handler.send_json = MagicMock()
        handler.send_error = MagicMock()
        with patch.object(app, "reboot") as reboot, patch.object(app.log, "exception"):
            handler.headers = {"Origin": "https://gemflix.site"}
            handler.do_POST()
            handler.send_error.assert_called_once_with(403)
            reboot.assert_not_called()
            handler.headers = {"Origin": "http://localhost:8080"}
            handler.do_POST()
            handler.send_json.assert_called_with({"ok": True})
            reboot.side_effect = subprocess.CalledProcessError(1, "sudo")
            handler.do_POST()
            self.assertEqual(handler.send_json.call_args.args[1], 500)


if __name__ == "__main__":
    unittest.main()
