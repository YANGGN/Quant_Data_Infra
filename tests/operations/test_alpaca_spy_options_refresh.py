from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from quant_data.errors import ValidationError
from quant_data.operations import alpaca_spy_options_refresh as operation


class AlpacaSpyOptionsRefreshTests(unittest.TestCase):
    def test_main_emits_compact_credential_free_success_receipt(self) -> None:
        report = {
            "contracts": 300,
            "outcome": "succeeded",
            "requests_issued": 4,
            "selected_expiration": "2026-09-25",
            "session_date": "2026-08-25",
            "surface_rows": 300,
            "written_count": 610,
        }
        stdout, stderr = StringIO(), StringIO()

        with (
            patch.object(operation, "_run", return_value=report),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = operation.main()

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "contract": "quant_data.alpaca_spy_option_surface_receipt",
                "receipt": report,
                "version": "1.0.0",
            },
        )

    def test_main_replaces_exception_text_with_generic_error_receipt(self) -> None:
        secret = "fixture-secret-must-not-appear"
        stdout, stderr = StringIO(), StringIO()

        with (
            patch.object(
                operation,
                "_run",
                side_effect=ValidationError(f"provider rejected {secret}"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = operation.main()

        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(code, 64)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(secret, output)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "contract": "quant_data.alpaca_spy_option_surface_error",
                "error": "invalid_request",
                "exit_code": 64,
                "version": "1.0.0",
            },
        )


if __name__ == "__main__":
    unittest.main()
