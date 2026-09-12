from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_backend import status_data
from waytail import backend, cli
from waytail.backend import WaytailError


class CliTests(unittest.TestCase):
    @patch("waytail.backend.run_tailscale")
    def test_status_prints_normalised_json(self, run) -> None:
        run.return_value = json.dumps(status_data())
        output = io.StringIO()

        with redirect_stdout(output):
            result = cli.main(["status"])

        self.assertEqual(result, 0)
        status = json.loads(output.getvalue())
        self.assertEqual(status["self_node"]["hostname"], "desktop")
        self.assertEqual(status["active_exit_node"]["ip"], "100.64.0.4")

    @patch("waytail.cli.refresh_waybar")
    def test_commands_dispatch_actions_before_refreshing(self, refresh) -> None:
        for argv, name, expected in (
            (["connect"], "connect", ()),
            (["disconnect"], "disconnect", ()),
            (["set-exit", "100.64.0.3"], "set_exit_node", ("100.64.0.3",)),
        ):
            with self.subTest(command=argv), patch.object(cli, name) as action:
                refresh.reset_mock()
                action.side_effect = lambda *_args: refresh.assert_not_called()

                self.assertEqual(cli.main(argv), 0)

                action.assert_called_once_with(*expected)
                refresh.assert_called_once_with()

    def test_panel_returns_application_exit_code(self) -> None:
        application = Mock(return_value=7)
        with patch.dict(sys.modules, {"waytail.panel": SimpleNamespace(run_panel=application)}):
            self.assertEqual(cli.main(["panel"]), 7)
        application.assert_called_once_with()

    def test_missing_panel_dependencies_report_an_error_without_traceback(self) -> None:
        errors = io.StringIO()
        with patch.dict(sys.modules, {"waytail.panel": None}), redirect_stderr(errors):
            result = cli.main(["panel"])
        self.assertEqual(result, 1)
        self.assertNotIn("Traceback", errors.getvalue())

    @patch("waytail.cli.refresh_waybar")
    @patch("waytail.cli.connect", side_effect=backend.WaytailError("Sign-in required"))
    def test_failed_connection_does_not_refresh_waybar(self, _connect, refresh) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["connect"]), 1)
        refresh.assert_not_called()

    @patch("waytail.cli.waybar_payload", return_value={"text": "", "class": ["connected"]})
    def test_waybar_prints_json(self, _payload) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            result = cli.main(["waybar"])

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), {"text": "", "class": ["connected"]})

    @patch("waytail.cli.refresh_waybar")
    @patch("waytail.cli.clear_exit_node")
    def test_mutation_refreshes_waybar(self, clear, refresh) -> None:
        self.assertEqual(cli.main(["clear-exit"]), 0)

        clear.assert_called_once_with()
        refresh.assert_called_once_with()

    @patch("waytail.cli.disconnect", side_effect=WaytailError("failed"))
    def test_command_errors_return_nonzero(self, _disconnect) -> None:
        error = io.StringIO()

        with redirect_stderr(error):
            result = cli.main(["disconnect"])

        self.assertEqual(result, 1)
        self.assertEqual(error.getvalue(), "failed\n")


if __name__ == "__main__":
    unittest.main()
