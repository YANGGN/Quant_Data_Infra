from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.errors import RegistryError, ValidationError
from quant_data.operations import alpaca_etf_options_refresh as operation


class AlpacaEtfOptionsRefreshTests(unittest.TestCase):
    def test_existing_weekday_unit_runs_major_etf_grid_with_hardening(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        service = (
            project_root
            / "deploy/systemd/quant-data-alpaca-spy-options.service"
        ).read_text(encoding="utf-8")
        timer = (
            project_root
            / "deploy/systemd/quant-data-alpaca-spy-options.timer"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "ExecStart=/usr/bin/python3 -m "
            "quant_data.operations.alpaca_etf_options_refresh",
            service,
        )
        self.assertIn("TimeoutStartSec=16min", service)
        for hardening_setting in (
            "UMask=0077",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "ReadWritePaths=/home/volatility/Python_Projects/Quant_Data_Infra/data",
        ):
            self.assertIn(hardening_setting, service)
        self.assertEqual(
            [line for line in timer.splitlines() if line.startswith("OnCalendar=")],
            ["OnCalendar=Mon..Fri *-*-* 16:20:00 America/New_York"],
        )
        self.assertEqual(
            [line for line in timer.splitlines() if line.startswith("Persistent=")],
            ["Persistent=false"],
        )
        self.assertIn("Unit=quant-data-alpaca-spy-options.service", timer)

    def test_main_emits_compact_success_only_for_complete_grid(self) -> None:
        report = {
            "outcome": "succeeded",
            "session_date": "2026-08-25",
            "requests_issued": 32,
            "captures": 15,
            "contracts": 30,
            "surface_rows": 30,
            "written_count": 123,
            "completed_underlyings": ["SPY", "QQQ"],
            "failed_underlyings": [],
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
                "contract": "quant_data.alpaca_etf_option_surface_grid_receipt",
                "receipt": report,
                "version": "1.0.0",
            },
        )

    def test_partial_never_claims_success_or_leaks_exception_text(self) -> None:
        partial = {
            "outcome": "partial",
            "session_date": "2026-08-25",
            "requests_issued": 32,
            "captures": 2,
            "contracts": 4,
            "surface_rows": 4,
            "written_count": 10,
            "completed_underlyings": ["SPY"],
            "failed_underlyings": ["QQQ"],
        }
        stdout, stderr = StringIO(), StringIO()
        with (
            patch.object(operation, "_run", return_value=partial),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = operation.main()
        self.assertEqual(code, 75)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "contract": "quant_data.alpaca_etf_option_surface_grid_error",
                "error": "incomplete_universe",
                "exit_code": 75,
                "receipt": partial,
                "version": "1.0.0",
            },
        )

    def test_exception_text_is_replaced_with_generic_receipt(self) -> None:
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
        self.assertNotIn(secret, output)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "contract": "quant_data.alpaca_etf_option_surface_grid_error",
                "error": "invalid_request",
                "exit_code": 64,
                "version": "1.0.0",
            },
        )

    def test_registry_readiness_error_is_specific_and_sanitized(self) -> None:
        secret = "fixture-registry-path-must-not-appear"
        stdout, stderr = StringIO(), StringIO()
        with (
            patch.object(
                operation,
                "_run",
                side_effect=RegistryError(f"catalog mismatch at {secret}"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = operation.main()
        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(code, 75)
        self.assertNotIn(secret, output)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "contract": "quant_data.alpaca_etf_option_surface_grid_error",
                "error": "registry_not_ready",
                "exit_code": 75,
                "version": "1.0.0",
            },
        )


if __name__ == "__main__":
    unittest.main()
