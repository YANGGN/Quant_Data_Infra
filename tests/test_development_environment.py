"""Offline checks for development dependency reporting; no package imports."""
from contextlib import redirect_stdout
from importlib import metadata, util
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_environment.py"
spec = util.spec_from_file_location("check_environment", SCRIPT)
checker = util.module_from_spec(spec)
spec.loader.exec_module(checker)


class DevelopmentEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quant-env-check-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.requirements = self.root / "requirements"
        self.requirements.mkdir()

    def write_profile(self, name, text):
        path = self.requirements / (name + ".txt")
        path.write_text(text, encoding="utf-8")
        return path

    def test_core_needs_no_distribution_lookup(self):
        self.write_profile("core", "# Standard library only\n")
        def reject_lookup(name):
            self.fail("Unexpected distribution lookup: " + name)
        for version in ((3, 11, 11), (3, 12, 3)):
            with self.subTest(version=version):
                report = checker.check("core", root=self.root, python=version, lookup=reject_lookup)
                self.assertTrue(report["ok"])
                self.assertEqual(report["dependencies"], [])

    def test_reports_missing_and_mismatched_pins_without_importing_them(self):
        self.write_profile("theta", "available==1.0\nmissing==2.0\nwrong==3.0\n")
        def lookup(name):
            if name == "missing":
                raise metadata.PackageNotFoundError(name)
            return {"available": "1.0", "wrong": "3.1"}[name]
        with patch("builtins.__import__", side_effect=AssertionError("Package import forbidden")):
            report = checker.check("theta", root=self.root, python=(3, 12, 3), lookup=lookup)
        self.assertFalse(report["ok"])
        self.assertTrue(report["python_ok"])
        self.assertEqual([row["status"] for row in report["dependencies"]],
                         ["ok", "missing", "version_mismatch"])
        self.assertIsNone(report["dependencies"][1]["installed"])
        self.assertEqual(report["dependencies"][2]["installed"], "3.1")

    def test_rejects_interpreters_outside_recorded_profiles(self):
        cases = {"core": ((3, 10, 9), (3, 13, 0)),
                 "calendar": ((3, 10, 9), (3, 12, 3)),
                 "theta": ((3, 11, 11), (3, 13, 0)),
                 "options-monitor": ((3, 11, 11), (3, 13, 0))}
        for profile, versions in cases.items():
            self.write_profile(profile, "")
            for version in versions:
                with self.subTest(profile=profile, version=version):
                    report = checker.check(profile, root=self.root, python=version)
                    self.assertFalse(report["python_ok"])
                    self.assertFalse(report["ok"])

    def test_sibling_includes_normalize_distribution_names_and_deduplicate(self):
        self.write_profile("theta", "Some_Package==1.2.3 # retained pin\n")
        main = self.write_profile("options-monitor", "-r theta.txt\nsome.package==1.2.3\nextra==2.0\n")
        self.assertEqual(checker.read_pins(main), {"some-package": "1.2.3", "extra": "2.0"})

    def test_conflicting_pins_and_recursive_includes_fail(self):
        self.write_profile("theta", "some_package==1.2.3\n")
        main = self.write_profile("options-monitor", "-r theta.txt\nsome-package==2.0\n")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            checker.read_pins(main)
        self.write_profile("theta", "-r options-monitor.txt\n")
        with self.assertRaisesRegex(ValueError, "Cyclic"):
            checker.read_pins(main)

    def test_only_exact_pins_and_sibling_includes_are_accepted(self):
        for text in ("pkg>=1.0", "pkg", "-r ../elsewhere.txt", "-r /tmp/elsewhere.txt",
                     "--index-url https://example.invalid", "-e ."):
            with self.subTest(text=text):
                path = self.write_profile("core", text)
                with self.assertRaises(ValueError):
                    checker.read_pins(path)

    def test_cli_returns_machine_readable_success_mismatch_and_input_errors(self):
        for ok, exit_code in ((True, 0), (False, 1)):
            with self.subTest(ok=ok):
                output = StringIO()
                report = {"profile": "theta", "ok": ok}
                with patch.object(checker, "check", return_value=report) as check, redirect_stdout(output):
                    self.assertEqual(checker.main(["--profile", "theta"]), exit_code)
                check.assert_called_once_with("theta")
                self.assertEqual(json.loads(output.getvalue()), report)
        for error in (ValueError("Conflicting pins"), FileNotFoundError("Missing profile")):
            with self.subTest(error=error):
                output = StringIO()
                with patch.object(checker, "check", side_effect=error), redirect_stdout(output):
                    self.assertEqual(checker.main([]), 2)
                self.assertFalse(json.loads(output.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
