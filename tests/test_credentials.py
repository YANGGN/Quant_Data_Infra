from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from quant_data.credentials import read_project_credential
from quant_data.errors import ValidationError


_ERROR = "Credential is missing or invalid"
_NAME = "FMP_API_KEY"


def _process_environment_fingerprint() -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (
                key,
                hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest(),
            )
            for key, value in os.environ.items()
        )
    )


class ProjectCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _dotenv(self, source: str | bytes, *, mode: int = 0o600) -> Path:
        path = self.root / ".env"
        if isinstance(source, str):
            path.write_text(source, encoding="utf-8")
        else:
            path.write_bytes(source)
        path.chmod(mode)
        return path

    def _read(self, environment: dict[str, str] | None = None) -> str:
        return read_project_credential(
            project_root=self.root,
            name=_NAME,
            environment={} if environment is None else environment,
        )

    def _assert_invalid(self, *, secret: str | None = None) -> None:
        with self.assertRaises(ValidationError) as raised:
            self._read()
        self.assertEqual(str(raised.exception), _ERROR)
        if secret is not None:
            self.assertNotIn(secret, str(raised.exception))

    def test_process_environment_precedence_never_opens_dotenv(self) -> None:
        with patch("quant_data.credentials.os.open", side_effect=AssertionError("opened")):
            self.assertEqual(self._read({_NAME: "process-value"}), "process-value")

    def test_blank_process_environment_falls_back_to_dotenv(self) -> None:
        self._dotenv(f"{_NAME}=dotenv-value\n")
        self.assertEqual(self._read({_NAME: "  \t"}), "dotenv-value")

    def test_common_dotenv_forms_parse_only_the_requested_name(self) -> None:
        cases = (
            (f"{_NAME}=unquoted-value # trailing comment\n", "unquoted-value"),
            (f"  export\t{_NAME} \t= 'single quoted value' # comment\n", "single quoted value"),
            (f'export {_NAME} = "double # literal" # comment\n', "double # literal"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self._dotenv(source)
                self.assertEqual(self._read(), expected)
                (self.root / ".env").unlink()

    def test_unrelated_assignments_are_ignored_and_no_environment_is_mutated(self) -> None:
        self._dotenv(
            "UNRELATED_CREDENTIAL=unrelated-secret\n"
            f"{_NAME}_EXTRA=wrong-value\n"
            f"{_NAME}=requested-value\n"
        )
        supplied_environment = {"UNRELATED_CREDENTIAL": "outside-value", _NAME: ""}
        process_before = _process_environment_fingerprint()
        supplied_before = dict(supplied_environment)
        self.assertEqual(self._read(supplied_environment), "requested-value")
        self.assertEqual(supplied_environment, supplied_before)
        self.assertEqual(_process_environment_fingerprint(), process_before)

    def test_duplicate_requested_assignments_are_rejected_without_value_disclosure(self) -> None:
        self._dotenv(f"{_NAME}=first-secret\n{_NAME}=second-secret\n")
        self._assert_invalid(secret="first-secret")
        with self.assertRaises(ValidationError) as raised:
            self._read()
        self.assertNotIn("second-secret", str(raised.exception))

    def test_malformed_requested_assignment_is_rejected_without_source_disclosure(self) -> None:
        self._dotenv(f"UNRELATED=unrelated-secret\nexport {_NAME} raw-secret\n")
        self._assert_invalid(secret="raw-secret")
        with self.assertRaises(ValidationError) as raised:
            self._read()
        self.assertNotIn("unrelated-secret", str(raised.exception))

    def test_empty_and_oversized_requested_values_are_rejected(self) -> None:
        self._dotenv(f"{_NAME}=  # no value\n")
        self._assert_invalid()
        self._dotenv(f"{_NAME}=" + ("x" * 4097))
        self._assert_invalid()

    def test_unsafe_group_or_other_writable_dotenv_is_rejected(self) -> None:
        path = self._dotenv(f"{_NAME}=secret\n", mode=0o620)
        self.assertTrue(stat.S_IMODE(path.stat().st_mode) & stat.S_IWGRP)
        self._assert_invalid(secret="secret")

    def test_symlink_dotenv_is_rejected(self) -> None:
        target = self.root / "target.env"
        target.write_text(f"{_NAME}=secret\n", encoding="utf-8")
        target.chmod(0o600)
        (self.root / ".env").symlink_to(target)
        self._assert_invalid(secret="secret")

    def test_hardlinked_dotenv_is_rejected(self) -> None:
        target = self.root / "target.env"
        target.write_text(f"{_NAME}=secret\n", encoding="utf-8")
        target.chmod(0o600)
        os.link(target, self.root / ".env")
        self._assert_invalid(secret="secret")

    def test_oversized_dotenv_and_control_source_are_rejected(self) -> None:
        self._dotenv(b"x" * ((64 * 1024) + 1))
        self._assert_invalid()
        self._dotenv(f"{_NAME}=secret\x00\n".encode("utf-8"))
        self._assert_invalid(secret="secret")

    def test_nonregular_dotenv_is_rejected(self) -> None:
        (self.root / ".env").mkdir()
        self._assert_invalid()

    def test_missing_requested_assignment_uses_the_generic_error(self) -> None:
        self._dotenv("UNRELATED=unrelated-secret\n")
        self._assert_invalid(secret="unrelated-secret")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
