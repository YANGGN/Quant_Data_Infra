"""Static consistency across metadata, checked-in units and registry ownership.

No installed-unit queries, provider requests or database access.
"""
import configparser
import json
from pathlib import Path
import shlex
import unittest

from quant_data.collector_metadata import ARCHIVED_BATCH_ARGUMENTS, COLLECTORS

ROOT = Path(__file__).resolve().parents[1]


def unit_file(name):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    with (ROOT / "deploy/systemd" / name).open() as source:
        parser.read_file(source)
    return parser


class CollectorMetadataTests(unittest.TestCase):
    def test_current_timer_targets_and_service_arguments_match_metadata(self):
        units = [collector.unit for collector in COLLECTORS]
        self.assertEqual(len(units), len(set(units)), "Duplicate timer metadata")
        labels = [collector.label for collector in COLLECTORS]
        self.assertEqual(len(labels), len(set(labels)), "Duplicate status labels")
        for collector in COLLECTORS:
            with self.subTest(unit=collector.unit):
                service = collector.unit.removesuffix(".timer") + ".service"
                self.assertEqual(unit_file(collector.unit)["Timer"]["Unit"], service)
                command = shlex.split(unit_file(service)["Service"]["ExecStart"])
                self.assertEqual(command[1], "-m")
                self.assertTrue(command[2].startswith("quant_data.operations."))
                self.assertTrue((ROOT / (command[2].replace(".", "/") + ".py")).is_file())
                self.assertEqual(tuple(command[3:]), collector.arguments)

    def test_output_bindings_name_registered_datasets_without_duplicates(self):
        registry = json.loads((ROOT / "config/system_registry.json").read_text())
        datasets = {dataset["id"] for dataset in registry["datasets"]}
        options = json.loads((ROOT / "config/options_registry.json").read_text())
        option_datasets = {dataset["id"] for dataset in options["datasets"]}
        self.assertFalse(datasets & option_datasets, "Companion-store ownership overlaps")
        datasets |= option_datasets
        for collector in COLLECTORS:
            with self.subTest(unit=collector.unit):
                self.assertEqual(len(collector.datasets), len(set(collector.datasets)))
                if collector.unit.startswith("quant-data-theta-options-"):
                    self.assertTrue(collector.datasets)
                    self.assertLessEqual(set(collector.datasets), option_datasets)
                self.assertFalse(set(collector.datasets) - datasets,
                                 "Collector output binding references an unknown dataset")

    def test_retired_receipts_remain_separate_from_current_timer_metadata(self):
        current = {collector.unit for collector in COLLECTORS}
        self.assertIn("quant-data-alpaca-spy-options.timer", ARCHIVED_BATCH_ARGUMENTS)
        self.assertFalse(current & set(ARCHIVED_BATCH_ARGUMENTS))
        self.assertTrue({"quant-data-theta-options-daily.timer",
                         "quant-data-theta-options-weekly.timer"} <= current)
