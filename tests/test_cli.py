from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from waytail import cli
from waytail.backend import WaytailError


class CliTests(unittest.TestCase):
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
