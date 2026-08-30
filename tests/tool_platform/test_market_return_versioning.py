from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from quant_data.boundary import Stage1Application, ToolDispatcher
from quant_data.errors import RegistryError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    analytics_foundation_registry_profile,
    alpaca_etf_options_registry_profile,
    alpaca_spy_options_registry_profile,
    canonical_access_registry_profile,
    company_filing_pagination_registry_profile,
    current_news_registry_profile,
    multi_source_current_news_registry_profile,
    econometrics_model_suite_v3_registry_profile,
    bea_personal_income_registry_profile,
    fmp_calendar_incremental_registry_profile,
    investment_analysis_v2_registry_profile,
    econometrics_v2_registry_profile,
    load_registry,
    market_price_series_v1_registry_profile,
    market_available_ticker_v1_registry_profile,
    market_return_v2_registry_profile,
    placeholder_successor_registry_profile,
    registry_259_additive_successors_profile,
    robust_econometrics_v21_registry_profile,
    stage10_research_analytics_registry_profile,
    stationarity_v21_registry_profile,
    structural_breaks_v2_registry_profile,
    technical_indicators_v25_registry_profile,
    technical_indicators_v26_registry_profile,
    technical_indicators_v27_registry_profile,
    technical_indicators_v23_registry_profile,
    technical_indicators_v22_registry_profile,
    technical_indicators_v21_registry_profile,
    technical_indicators_v2_registry_profile,
    timeseries_analysis_v2_registry_profile,
)
from quant_data.stage1 import explicit_store_map
from quant_data.schema import validate_schema
from quant_data.tool_platform.arguments import parse_arguments
from quant_data.tool_platform.generate import generate, generated_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[2]
V1_CATALOG = (
    PROJECT_ROOT / "quant_data" / "generated" / "tool_contract_schemas_v1.json"
)
V2_CATALOG = (
    PROJECT_ROOT / "quant_data" / "generated" / "tool_contract_schemas_v2.json"
)
V1_CATALOG_SHA256 = (
    "a2469c903cc6c9dae64ea29c4d3b543837a37d4989277290220061101d28de87"
)
V2_CATALOG_SHA256 = (
    "6a4f7e8ce223658617512928b860f5cf5bde85e01f075070771fa019e882ed46"
)
CURRENT_REGISTRY_SHA256 = (
    "b47b6ad63ecaa41477388af033c7f928083ceb5e7db17bf76ff4ab99f71f3dc4"
)
REGISTRY_262_SOURCE_SHA256 = (
    "59db17edd70d8ae9aa77f1468cf7c459338e3d1dff0015b3c99853b2e1e12fd2"
)
CATALOG_223_SHA256 = (
    "05cfbfb29b544594a3b176daeca659470f3c91a20a423c730d8da9622c8cae2d"
)
CATALOG_222_SHA256 = (
    "18e86daf6ef3291407ab794d7f53015c80bb90c88f2518891f7b904002f515a1"
)
REGISTRY_261_SOURCE_SHA256 = (
    "0f43045c1dcc46b0f9d5ecb0aaf98aa3e9ad61a43e250afc389408602a8615ea"
)
CATALOG_221_SHA256 = (
    "b70798594de6149b7710b36d3b735b3d24ff81eb23277ba54dfdc697aceb0efc"
)
REGISTRY_260_SOURCE_SHA256 = (
    "563c4294c47d534754014dc77a4a1b39e84749cc4d8165636bafd82feff679b0"
)
CATALOG_220_SHA256 = (
    "cebc1ca58257fed167db36973be1ce37e6e01162c2bc072c24591a673b0aedb4"
)
REGISTRY_259_SOURCE_SHA256 = (
    "0457910706181d7f4d9bb07efbf18845dd86335999b55c5d5a8d203041d315a1"
)
CATALOG_219_SHA256 = (
    "e4fdb9d9b6783ec131838762d8e2ae4b1c6833cc37c0292e1ceed09e0d919474"
)
REGISTRY_258_SOURCE_SHA256 = (
    "02a4aeb34632c5ae194622b7de77ec31145135bdf399620e45af461132134d43"
)
CATALOG_218_SHA256 = (
    "4803071d5cb2ababa2710ac4c0e3eb1e1640b06aa0b6071e14c2f40be68fec72"
)
PRE_TECHNICAL_INDICATORS_V23_CATALOG_SHA256 = (
    "5c0ea96b9aba9d73f8f89aea20a052c858691e10a1047f22594f027d8f893101"
)
PRE_TECHNICAL_INDICATORS_V23_REGISTRY_SHA256 = (
    "1d36ddd20494cfc0ae9e6172f662c5dfe04b905319c0f83b439446f44fd29b5e"
)
PRE_INVESTMENT_ANALYSIS_V2_CATALOG_SHA256 = (
    "cb1c6965582b90eaeec27054eda0d66e7a4c603aefa69f9b453ae342d779302b"
)
PRE_INVESTMENT_ANALYSIS_V2_REGISTRY_SHA256 = (
    "9782e77c530251401e9b5e42b33115949ce8bb28753f0a0471fa2e8353dd8802"
)
PRE_TECHNICAL_INDICATORS_V22_REGISTRY_SHA256 = (
    "cec35d5cfed9f25c40f5adfa2f2581b0de8d442a75202e687f791b3a16dc3d7f"
)
PRE_TECHNICAL_INDICATORS_V22_CATALOG_SHA256 = (
    "65311bb28efe62651ecb3420f0be13fd413df468487141cca0972d45e7684c85"
)
PRE_TECHNICAL_INDICATORS_V21_REGISTRY_SHA256 = (
    "f40c4d2e0cad90f686bffe52116d138f3b578f33f4844245682387d398a19e01"
)
PRE_TECHNICAL_INDICATORS_V21_CATALOG_SHA256 = (
    "a70903e9ba65fd71d5d79775698174dafea630c58c435bb4d8fcc504320aa05b"
)
PRE_PLACEHOLDER_SUCCESSOR_REGISTRY_SHA256 = (
    "c201524e4e4a72b5377d36390e0cc5c746d392674b598ac1499ab818b418d238"
)
PRE_PLACEHOLDER_SUCCESSOR_CATALOG_SHA256 = (
    "b313cc2c4e4fd39311c00a5c18ae3ef8aa4de157f58f23c0837b52a51d601ac5"
)
PRE_FILING_PAGINATION_REGISTRY_SHA256 = (
    "87c74bbc26ce6101ff6136eefe9fdab0d9616f06d714d049d4c30287eda78323"
)
PRE_FILING_PAGINATION_CATALOG_SHA256 = (
    "3c15ebcbf145d188681a49016dc621ffc96a48f6480f7ff9067d962c8fa29693"
)
PRE_RESEARCH_ANALYTICS_REGISTRY_SHA256 = (
    "1d8485bd1df5351d94f0b13f2264828640d40c756c6241503f40a7b7f4e46c43"
)
PRE_RESEARCH_ANALYTICS_CATALOG_SHA256 = (
    "864a4d07afbf2558a331d30275cf21f4e31521142ee2d9d010dc26cc5680a757"
)
PRE_TECHNICAL_INDICATORS_REGISTRY_SHA256 = (
    "eefa1288e8007518d466a3d4820522ae113ae6c52dc6df0e448cd3654de4a1b8"
)
PRE_TECHNICAL_INDICATORS_CATALOG_SHA256 = (
    "381a78aa59682fbf36cc90acc146d2cfa5b43ea90537b7356eca701df0794fbf"
)
PRE_ANALYTICS_FOUNDATION_REGISTRY_SHA256 = (
    "b5236b88a2b320628b870fe3abe7898b76fa5fc1d527a223f87985963e38e264"
)
PRE_ANALYTICS_FOUNDATION_CATALOG_SHA256 = (
    "e6fa88fa63856247ab00073a14ff1321cfafa05d5323a22c26008e81d0b1eb5c"
)
PRE_CANONICAL_ACCESS_REGISTRY_SHA256 = (
    "f151db20dd26fe2123e887415736431cfe1dcda8bf8f42d83a8b47ad3a27fec2"
)
PRE_CANONICAL_ACCESS_CATALOG_SHA256 = (
    "554873c79f58abbee6f4fbf83e4a6767153c9ff920651825d8cac3bd6075905f"
)
PRE_ALPACA_SPY_OPTIONS_REGISTRY_SHA256 = (
    "1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b"
)
PRE_AVAILABLE_TICKER_REGISTRY_SHA256 = (
    "835fe846c0d0bf0ce630cda0dd23588983c83f6fad663cbe51202fe00deee2a3"
)
PRE_AVAILABLE_TICKER_CATALOG_SHA256 = (
    "0f89da921b37d210c596646b3c4f47e4dbe27c2fe2579d8772c6db2bed312398"
)
PRE_MARKET_PRICE_SERIES_REGISTRY_SHA256 = (
    "72516749f56f962bea2a265ef4a917558ef6e757444ae5d679bc50d2d64e6ed7"
)
PRE_MARKET_PRICE_SERIES_CATALOG_SHA256 = (
    "2c9424a0ff9cda9130d559c2dacb6cef55ff9ff8537191285219d608411b7f24"
)
PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SHA256 = (
    "f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85"
)
PRE_ECONOMETRICS_MODEL_SUITE_V3_REGISTRY_SHA256 = (
    "3d6c0f31f2c72c20e5459c4e7f2358ea273437d59af99ef017b1f3ad47b1b547"
)
PRE_ECONOMETRICS_MODEL_SUITE_V3_CATALOG_SHA256 = (
    "e0650e5b76ec9a851220c2395c34f9172f1380b5a5c9b2bc681056c655bdeb5b"
)
PRE_STRUCTURAL_BREAKS_V2_REGISTRY_SHA256 = (
    "2a2b611ac6f752e6d83a81369155454b1484b8caebf4d1aaa3be60c41f0e866b"
)
PRE_STRUCTURAL_BREAKS_V2_CATALOG_SHA256 = (
    "529d904d46003c53785926730279dc4c37bf09b7d15f99c366122c416c4af26e"
)
PRE_STATIONARITY_V21_REGISTRY_SHA256 = (
    "5c9702d8ca4c2f896083471cc60adecfd3a43c72d45694d04688ab07e6330035"
)
PRE_STATIONARITY_V21_CATALOG_SHA256 = (
    "7de5126d50c438d0bb35cb82a9a0dd282251de3acceeb850d69ca20f894ef7b9"
)

PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SHA256 = (
    "a5e5b11bd9578428430c96f9eec59214e39f8cbb85f5b74fffc37aebaabdc27e"
)
PRE_ROBUST_ECONOMETRICS_V21_CATALOG_SHA256 = (
    "5250c18b70066de734cb2a4715ec20825f4a587892ba70728065dde82a4a239c"
)
PRE_ECONOMETRICS_V2_REGISTRY_SHA256 = (
    "eae80b10840afa2b724215aa431e1b8feab303486bbc87a60a8591e5812536cb"
)
PRE_ECONOMETRICS_V2_CATALOG_SHA256 = (
    "3ae30774d87c31217204da2240a56124c2e732a14f9fb6e38a57e3d6d7341b79"
)
PRE_TIMESERIES_V2_REGISTRY_SHA256 = (
    "4b57a6bfb311001eee852f19ca45ad500095c7f997d804860cdd24a5ef24565d"
)
PRE_TIMESERIES_V2_CATALOG_SHA256 = (
    "2697d9abc49880a156b7d607d59aa6903f518fbd16d8d764adc0b8c0b257415b"
)
PREDECESSOR_REGISTRY_SHA256 = (
    "d28298c36ce6b418ec516845ac3f58c8b9e4c0706afdacbb5f752ed7d5431bd0"
)


class MarketReturnVersioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )

    def test_generator_accepts_only_current_or_exact_direct_predecessor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            registry_path = root / CANONICAL_REGISTRY_PATH
            registry_path.parent.mkdir(parents=True)

            stale = bea_personal_income_registry_profile(self.registry)
            self.assertEqual(stale.revision, "2.34.0")
            registry_path.write_text(
                json.dumps(
                    stale.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "exact reviewed registry source",
            ):
                generated_bytes(root)

            relabeled = dict(stale.raw)
            relabeled["registry_version"] = "2.38.0"
            registry_path.write_text(
                json.dumps(
                    relabeled,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "exact reviewed registry source",
            ):
                generated_bytes(root)

            registry_263_predecessor = multi_source_current_news_registry_profile(
                self.registry
            )
            registry_path.write_text(
                json.dumps(
                    registry_263_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        self.registry.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                json.loads(registry_bytes)["registry_version"], "2.64.0"
            )
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())
            self.assertEqual(v2_bytes, V2_CATALOG.read_bytes())

            registry_262_predecessor = current_news_registry_profile(
                self.registry
            )
            registry_path.write_text(
                json.dumps(
                    registry_262_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        registry_263_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                json.loads(registry_bytes)["registry_version"], "2.63.0"
            )
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(), CATALOG_223_SHA256
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.23.0")

            registry_261_predecessor = (
                technical_indicators_v27_registry_profile(self.registry)
            )
            registry_path.write_text(
                json.dumps(
                    registry_261_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        registry_262_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                json.loads(registry_bytes)["registry_version"], "2.62.0"
            )
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(), CATALOG_222_SHA256
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.22.0")

            registry_260_predecessor = (
                technical_indicators_v26_registry_profile(self.registry)
            )
            registry_path.write_text(
                json.dumps(
                    registry_260_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        registry_261_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                json.loads(registry_bytes)["registry_version"], "2.61.0"
            )
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(), CATALOG_221_SHA256
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.21.0")

            registry_259_predecessor = (
                technical_indicators_v25_registry_profile(self.registry)
            )
            registry_path.write_text(
                json.dumps(
                    registry_259_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        registry_260_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                json.loads(registry_bytes)["registry_version"], "2.60.0"
            )
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(), CATALOG_220_SHA256
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.20.0")

            registry_258_predecessor = (
                registry_259_additive_successors_profile(self.registry)
            )
            registry_path.write_text(
                json.dumps(
                    registry_258_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        registry_259_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                json.loads(registry_bytes)["registry_version"], "2.59.0"
            )
            self.assertEqual(
                hashlib.sha256(registry_bytes).hexdigest(),
                REGISTRY_259_SOURCE_SHA256,
            )
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(), CATALOG_219_SHA256
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.19.0")

            technical_v23_predecessor = (
                technical_indicators_v23_registry_profile(self.registry)
            )
            registry_path.write_text(
                json.dumps(
                    technical_v23_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        registry_258_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                json.loads(registry_bytes)["registry_version"], "2.58.0"
            )
            self.assertEqual(
                hashlib.sha256(registry_bytes).hexdigest(),
                REGISTRY_258_SOURCE_SHA256,
            )
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(),
                CATALOG_218_SHA256,
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.18.0")
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())

            investment_predecessor = investment_analysis_v2_registry_profile(
                self.registry
            )
            registry_path.write_text(
                json.dumps(
                    investment_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        technical_v23_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                json.loads(registry_bytes)["registry_version"], "2.57.0"
            )
            self.assertEqual(
                hashlib.sha256(registry_bytes).hexdigest(),
                PRE_TECHNICAL_INDICATORS_V23_REGISTRY_SHA256,
            )
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(),
                PRE_TECHNICAL_INDICATORS_V23_CATALOG_SHA256,
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.17.0")
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())

            technical_v22_predecessor = technical_indicators_v22_registry_profile(
                investment_predecessor
            )
            technical_v21_predecessor = (
                technical_indicators_v21_registry_profile(self.registry)
            )
            placeholder_predecessor = placeholder_successor_registry_profile(
                self.registry
            )
            self.assertEqual(placeholder_predecessor.revision, "2.53.0")
            registry_path.write_text(
                json.dumps(
                    placeholder_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        technical_v21_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                hashlib.sha256(registry_bytes).hexdigest(),
                PRE_TECHNICAL_INDICATORS_V21_REGISTRY_SHA256,
            )
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(),
                PRE_TECHNICAL_INDICATORS_V21_CATALOG_SHA256,
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.14.0")
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())

            registry_path.write_text(
                json.dumps(
                    technical_v21_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        technical_v22_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                hashlib.sha256(registry_bytes).hexdigest(),
                PRE_TECHNICAL_INDICATORS_V22_REGISTRY_SHA256,
            )
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(),
                PRE_TECHNICAL_INDICATORS_V22_CATALOG_SHA256,
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.15.0")
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())

            registry_path.write_text(
                json.dumps(
                    technical_v22_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                registry_bytes,
                (
                    json.dumps(
                        investment_predecessor.raw,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                hashlib.sha256(registry_bytes).hexdigest(),
                PRE_INVESTMENT_ANALYSIS_V2_REGISTRY_SHA256,
            )
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(),
                PRE_INVESTMENT_ANALYSIS_V2_CATALOG_SHA256,
            )
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.16.0")
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())

            analytics_predecessor = analytics_foundation_registry_profile(
                self.registry
            )
            registry_path.write_text(
                json.dumps(
                    analytics_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            self.assertEqual(
                hashlib.sha256(registry_bytes).hexdigest(),
                PRE_TECHNICAL_INDICATORS_REGISTRY_SHA256,
            )
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(),
                PRE_TECHNICAL_INDICATORS_CATALOG_SHA256,
            )
            self.assertEqual(json.loads(registry_bytes)["registry_version"], "2.47.0")
            self.assertEqual(json.loads(v2_bytes)["schema_version"], "2.10.0")
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())

            canonical_predecessor = canonical_access_registry_profile(
                self.registry
            )
            self.assertEqual(canonical_predecessor.revision, "2.45.0")
            registry_path.write_text(
                json.dumps(
                    canonical_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, v1_bytes, v2_bytes = generated_bytes(root)
            expected_registry_bytes = (
                json.dumps(
                    analytics_predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            self.assertEqual(registry_bytes, expected_registry_bytes)
            self.assertEqual(v1_bytes, V1_CATALOG.read_bytes())
            self.assertEqual(
                hashlib.sha256(registry_bytes).hexdigest(),
                PRE_ANALYTICS_FOUNDATION_REGISTRY_SHA256,
            )
            self.assertEqual(
                hashlib.sha256(v2_bytes).hexdigest(),
                PRE_ANALYTICS_FOUNDATION_CATALOG_SHA256,
            )
            regenerated = json.loads(registry_bytes)
            generated_catalog = json.loads(v2_bytes)
            self.assertEqual(regenerated["registry_version"], "2.46.0")
            self.assertEqual(len(regenerated["tools"]), 61)
            self.assertEqual(len(regenerated["tool_versions"]), 12)
            self.assertEqual(generated_catalog["schema_version"], "2.9.0")
            self.assertEqual(len(generated_catalog["contracts"]), 40)

            incremental_predecessor = (
                fmp_calendar_incremental_registry_profile(self.registry)
            )
            self.assertEqual(incremental_predecessor.revision, "2.39.0")
            self.assertEqual(
                incremental_predecessor.source_sha256,
                PRE_FMP_CALENDAR_INCREMENTAL_REGISTRY_SHA256,
            )
            predecessor = econometrics_model_suite_v3_registry_profile(self.registry)
            self.assertEqual(predecessor.revision, "2.38.0")
            registry_path.write_text(
                json.dumps(
                    predecessor.raw,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            registry_bytes, _, _ = generated_bytes(root)
            regenerated = json.loads(registry_bytes)
            self.assertEqual(regenerated["registry_version"], "2.39.0")
            self.assertIn(
                "bea.macro.personal_income_history",
                {item["id"] for item in regenerated["collectors"]},
            )

    def test_parabolic_sar_v26_projection_restores_exact_260(self) -> None:
        predecessor = technical_indicators_v26_registry_profile(self.registry)
        self.assertEqual(
            (predecessor.registry_version, predecessor.source_sha256),
            ("2.60.0", REGISTRY_260_SOURCE_SHA256),
        )
        self.assertEqual(
            predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.20.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": CATALOG_220_SHA256,
            },
        )
        payload = (
            json.dumps(
                predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            REGISTRY_260_SOURCE_SHA256,
        )
        self.assertEqual(len(predecessor.tool_version_policies), 40)
        self.assertEqual(
            sum(
                len(policy["variants"])
                for policy in predecessor.tool_version_policies
            ),
            53,
        )
        self.assertEqual(
            tuple(
                variant["version"]
                for variant in next(
                    policy
                    for policy in predecessor.tool_version_policies
                    if policy["tool"] == "market.technical_indicators"
                )["variants"]
            ),
            ("2.0.0", "2.1.0", "2.2.0", "2.3.0", "2.4.0", "2.5.0"),
        )
        self.assertIs(
            technical_indicators_v26_registry_profile(predecessor),
            predecessor,
        )

    def test_rolling_regression_v27_projection_restores_exact_261(self) -> None:
        predecessor = technical_indicators_v27_registry_profile(self.registry)
        self.assertEqual(
            (predecessor.registry_version, predecessor.source_sha256),
            ("2.61.0", REGISTRY_261_SOURCE_SHA256),
        )
        self.assertEqual(
            predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.21.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": CATALOG_221_SHA256,
            },
        )
        payload = (
            json.dumps(
                predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            REGISTRY_261_SOURCE_SHA256,
        )
        self.assertEqual(len(predecessor.tool_version_policies), 40)
        self.assertEqual(
            sum(
                len(policy["variants"])
                for policy in predecessor.tool_version_policies
            ),
            54,
        )
        self.assertEqual(
            tuple(
                variant["version"]
                for variant in next(
                    policy
                    for policy in predecessor.tool_version_policies
                    if policy["tool"] == "market.technical_indicators"
                )["variants"]
            ),
            ("2.0.0", "2.1.0", "2.2.0", "2.3.0", "2.4.0", "2.5.0", "2.6.0"),
        )
        self.assertIs(
            technical_indicators_v27_registry_profile(predecessor),
            predecessor,
        )

    def test_wavetrend_v25_projection_restores_exact_259(self) -> None:
        predecessor = technical_indicators_v25_registry_profile(self.registry)
        self.assertEqual(
            (predecessor.registry_version, predecessor.source_sha256),
            ("2.59.0", REGISTRY_259_SOURCE_SHA256),
        )
        self.assertEqual(
            predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.19.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": CATALOG_219_SHA256,
            },
        )
        payload = (
            json.dumps(
                predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            REGISTRY_259_SOURCE_SHA256,
        )
        self.assertEqual(len(predecessor.tool_version_policies), 40)
        self.assertEqual(
            sum(
                len(policy["variants"])
                for policy in predecessor.tool_version_policies
            ),
            52,
        )
        self.assertEqual(
            tuple(
                variant["version"]
                for variant in next(
                    policy
                    for policy in predecessor.tool_version_policies
                    if policy["tool"] == "market.technical_indicators"
                )["variants"]
            ),
            ("2.0.0", "2.1.0", "2.2.0", "2.3.0", "2.4.0"),
        )
        self.assertIs(
            technical_indicators_v25_registry_profile(predecessor),
            predecessor,
        )

    def test_registry_259_projection_restores_exact_258(self) -> None:
        predecessor = registry_259_additive_successors_profile(self.registry)
        self.assertEqual(
            (
                predecessor.registry_version,
                predecessor.source_sha256,
            ),
            ("2.58.0", REGISTRY_258_SOURCE_SHA256),
        )
        self.assertEqual(
            predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.18.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": CATALOG_218_SHA256,
            },
        )
        payload = (
            json.dumps(
                predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            REGISTRY_258_SOURCE_SHA256,
        )
        self.assertEqual(len(predecessor.tool_version_policies), 40)
        self.assertEqual(
            sum(
                len(policy["variants"])
                for policy in predecessor.tool_version_policies
            ),
            47,
        )
        projected_variants = {
            (str(policy["tool"]), str(variant["version"]))
            for policy in predecessor.tool_version_policies
            for variant in policy["variants"]
        }
        self.assertFalse(
            {
                ("market.technical_indicators", "2.4.0"),
                ("market.cross_sectional_performance", "2.1.0"),
                ("energy.get_electricity_retail_sales", "2.1.0"),
                ("energy.get_weekly_fundamentals", "2.1.0"),
                ("company.get_fundamentals", "2.1.0"),
            }.intersection(projected_variants)
        )
        self.assertIs(
            registry_259_additive_successors_profile(predecessor),
            predecessor,
        )


    def test_current_registry_has_forty_one_versioned_tools_and_fifty_six_variants(
        self,
    ) -> None:
        self.assertEqual(
            (self.registry.schema_version, self.registry.registry_version),
            ("1.9.0", "2.64.0"),
        )
        self.assertEqual(self.registry.source_sha256, CURRENT_REGISTRY_SHA256)
        technical_v23_predecessor = (
            technical_indicators_v23_registry_profile(self.registry)
        )
        self.assertEqual(
            (
                technical_v23_predecessor.registry_version,
                technical_v23_predecessor.source_sha256,
            ),
            ("2.57.0", PRE_TECHNICAL_INDICATORS_V23_REGISTRY_SHA256),
        )
        self.assertEqual(
            technical_v23_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.17.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_TECHNICAL_INDICATORS_V23_CATALOG_SHA256,
            },
        )
        technical_v23_predecessor_bytes = (
            json.dumps(
                technical_v23_predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(technical_v23_predecessor_bytes).hexdigest(),
            PRE_TECHNICAL_INDICATORS_V23_REGISTRY_SHA256,
        )
        self.assertEqual(
            tuple(
                variant["version"]
                for variant in next(
                    policy
                    for policy in technical_v23_predecessor.tool_version_policies
                    if policy["tool"] == "market.technical_indicators"
                )["variants"]
            ),
            ("2.0.0", "2.1.0", "2.2.0"),
        )
        self.assertIs(
            technical_indicators_v23_registry_profile(
                technical_v23_predecessor
            ),
            technical_v23_predecessor,
        )
        investment_predecessor = investment_analysis_v2_registry_profile(
            self.registry
        )
        self.assertEqual(
            (
                investment_predecessor.registry_version,
                investment_predecessor.source_sha256,
            ),
            ("2.56.0", PRE_INVESTMENT_ANALYSIS_V2_REGISTRY_SHA256),
        )
        self.assertEqual(
            investment_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.16.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_INVESTMENT_ANALYSIS_V2_CATALOG_SHA256,
            },
        )
        investment_predecessor_bytes = (
            json.dumps(
                investment_predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(investment_predecessor_bytes).hexdigest(),
            PRE_INVESTMENT_ANALYSIS_V2_REGISTRY_SHA256,
        )
        self.assertEqual(len(investment_predecessor.tool_version_policies), 25)
        self.assertIs(
            investment_analysis_v2_registry_profile(investment_predecessor),
            investment_predecessor,
        )
        technical_v22_predecessor = technical_indicators_v22_registry_profile(
            self.registry
        )
        self.assertEqual(
            (
                technical_v22_predecessor.registry_version,
                technical_v22_predecessor.source_sha256,
            ),
            ("2.55.0", PRE_TECHNICAL_INDICATORS_V22_REGISTRY_SHA256),
        )
        self.assertEqual(
            technical_v22_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.15.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_TECHNICAL_INDICATORS_V22_CATALOG_SHA256,
            },
        )
        self.assertEqual(
            tuple(
                variant["version"]
                for variant in next(
                    policy
                    for policy in technical_v22_predecessor.tool_version_policies
                    if policy["tool"] == "market.technical_indicators"
                )["variants"]
            ),
            ("2.0.0", "2.1.0"),
        )
        self.assertIs(
            technical_indicators_v22_registry_profile(
                technical_v22_predecessor
            ),
            technical_v22_predecessor,
        )
        technical_v21_predecessor = technical_indicators_v21_registry_profile(
            self.registry
        )
        self.assertEqual(
            (
                technical_v21_predecessor.registry_version,
                technical_v21_predecessor.source_sha256,
            ),
            ("2.54.0", PRE_TECHNICAL_INDICATORS_V21_REGISTRY_SHA256),
        )
        self.assertEqual(
            technical_v21_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.14.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_TECHNICAL_INDICATORS_V21_CATALOG_SHA256,
            },
        )
        self.assertEqual(
            tuple(
                variant["version"]
                for variant in next(
                    policy
                    for policy in technical_v21_predecessor.tool_version_policies
                    if policy["tool"] == "market.technical_indicators"
                )["variants"]
            ),
            ("2.0.0",),
        )
        self.assertIs(
            technical_indicators_v21_registry_profile(
                technical_v21_predecessor
            ),
            technical_v21_predecessor,
        )
        placeholder_predecessor = placeholder_successor_registry_profile(
            self.registry
        )
        self.assertEqual(
            (
                placeholder_predecessor.registry_version,
                placeholder_predecessor.source_sha256,
            ),
            ("2.53.0", PRE_PLACEHOLDER_SUCCESSOR_REGISTRY_SHA256),
        )
        self.assertEqual(
            placeholder_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.13.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_PLACEHOLDER_SUCCESSOR_CATALOG_SHA256,
            },
        )
        self.assertEqual(len(placeholder_predecessor.tool_version_policies), 23)
        self.assertIs(
            placeholder_successor_registry_profile(placeholder_predecessor),
            placeholder_predecessor,
        )
        for name in (
            "market.search_instruments",
            "company.get_share_count_history",
        ):
            with self.subTest(predecessor_tool=name):
                with self.assertRaises(RegistryError):
                    placeholder_predecessor.tool(name, "2.0.0")
        filing_predecessor = company_filing_pagination_registry_profile(
            self.registry
        )
        self.assertEqual(
            (
                filing_predecessor.registry_version,
                filing_predecessor.source_sha256,
            ),
            ("2.52.0", PRE_FILING_PAGINATION_REGISTRY_SHA256),
        )
        self.assertEqual(
            filing_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.12.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_FILING_PAGINATION_CATALOG_SHA256,
            },
        )
        self.assertEqual(len(filing_predecessor.tool_version_policies), 22)
        self.assertIs(
            company_filing_pagination_registry_profile(filing_predecessor),
            filing_predecessor,
        )
        with self.assertRaises(RegistryError):
            filing_predecessor.tool("company.search_filings", "2.0.0")
        research_predecessor = stage10_research_analytics_registry_profile(
            self.registry
        )
        self.assertEqual(
            (
                research_predecessor.registry_version,
                research_predecessor.source_sha256,
            ),
            ("2.51.0", PRE_RESEARCH_ANALYTICS_REGISTRY_SHA256),
        )
        self.assertEqual(
            research_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.11.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_RESEARCH_ANALYTICS_CATALOG_SHA256,
            },
        )
        self.assertEqual(
            len(research_predecessor.tool_version_policies),
            15,
        )
        self.assertIs(
            stage10_research_analytics_registry_profile(research_predecessor),
            research_predecessor,
        )
        with self.assertRaises(RegistryError):
            research_predecessor.tool(
                "research.point_in_time_panel", "2.0.0"
            )
        technical_predecessor = technical_indicators_v2_registry_profile(
            self.registry
        )
        self.assertEqual(
            (
                technical_predecessor.registry_version,
                technical_predecessor.source_sha256,
            ),
            ("2.47.0", PRE_TECHNICAL_INDICATORS_REGISTRY_SHA256),
        )
        self.assertEqual(
            technical_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.10.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_TECHNICAL_INDICATORS_CATALOG_SHA256,
            },
        )
        self.assertIs(
            technical_indicators_v2_registry_profile(technical_predecessor),
            technical_predecessor,
        )
        analytics_predecessor = analytics_foundation_registry_profile(
            self.registry
        )
        self.assertEqual(
            (
                analytics_predecessor.registry_version,
                analytics_predecessor.source_sha256,
            ),
            ("2.46.0", PRE_ANALYTICS_FOUNDATION_REGISTRY_SHA256),
        )
        self.assertEqual(
            analytics_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.9.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_ANALYTICS_FOUNDATION_CATALOG_SHA256,
            },
        )
        canonical_predecessor = canonical_access_registry_profile(self.registry)
        self.assertEqual(
            (
                canonical_predecessor.registry_version,
                canonical_predecessor.source_sha256,
            ),
            ("2.45.0", PRE_CANONICAL_ACCESS_REGISTRY_SHA256),
        )
        self.assertEqual(
            canonical_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.8.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_CANONICAL_ACCESS_CATALOG_SHA256,
            },
        )
        self.assertIs(
            canonical_access_registry_profile(canonical_predecessor),
            canonical_predecessor,
        )
        predecessor = market_price_series_v1_registry_profile(self.registry)
        alpaca_predecessor = alpaca_spy_options_registry_profile(self.registry)
        self.assertEqual(
            (
                alpaca_predecessor.registry_version,
                alpaca_predecessor.source_sha256,
            ),
            ("2.42.0", PRE_ALPACA_SPY_OPTIONS_REGISTRY_SHA256),
        )
        self.assertIs(alpaca_spy_options_registry_profile(alpaca_predecessor), alpaca_predecessor)
        ticker_predecessor = (
            market_available_ticker_v1_registry_profile(self.registry)
        )
        self.assertEqual(ticker_predecessor.registry_version, "2.41.0")
        self.assertEqual(
            ticker_predecessor.source_sha256,
            PRE_AVAILABLE_TICKER_REGISTRY_SHA256,
        )
        self.assertEqual(
            tuple(item["id"] for item in ticker_predecessor.tools),
            tuple(
                item["id"]
                for item in canonical_predecessor.tools
                if item["id"] != "market.get_available_ticker"
            ),
        )
        self.assertEqual(
            ticker_predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.7.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_AVAILABLE_TICKER_CATALOG_SHA256,
            },
        )
        ticker_predecessor_payload = (
            json.dumps(
                ticker_predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(ticker_predecessor_payload).hexdigest(),
            PRE_AVAILABLE_TICKER_REGISTRY_SHA256,
        )
        self.assertIs(
            market_available_ticker_v1_registry_profile(ticker_predecessor),
            ticker_predecessor,
        )
        self.assertEqual(predecessor.registry_version, "2.40.0")
        self.assertEqual(
            predecessor.source_sha256,
            PRE_MARKET_PRICE_SERIES_REGISTRY_SHA256,
        )
        self.assertEqual(
            tuple(item["id"] for item in predecessor.tools),
            tuple(
                item["id"]
                for item in canonical_predecessor.tools
                if item["id"] not in {
                    "market.get_available_ticker",
                    "market.get_price_series",
                }
            ),
        )
        self.assertEqual(
            predecessor.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.6.0",
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "sha256": PRE_MARKET_PRICE_SERIES_CATALOG_SHA256,
            },
        )
        predecessor_payload = (
            json.dumps(
                predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(predecessor_payload).hexdigest(),
            PRE_MARKET_PRICE_SERIES_REGISTRY_SHA256,
        )
        self.assertIs(
            market_price_series_v1_registry_profile(predecessor),
            predecessor,
        )
        drifted_datasets = replace(
            self.registry,
            datasets=tuple(
                replace(
                    item,
                    tool_ids=tuple(
                        tool_id
                        for tool_id in item.tool_ids
                        if tool_id != "market.get_price_series"
                    ),
                )
                if item.id == "market.stage10.daily_prices"
                else item
                for item in self.registry.datasets
            ),
            raw={
                **self.registry.raw,
                "datasets": [
                    {
                        **item,
                        "tool_ids": [
                            tool_id
                            for tool_id in item["tool_ids"]
                            if tool_id != "market.get_price_series"
                        ],
                    }
                    if item["id"] == "market.stage10.daily_prices"
                    else item
                    for item in self.registry.raw["datasets"]
                ],
            },
        )
        with self.assertRaises(RegistryError):
            market_price_series_v1_registry_profile(drifted_datasets)
        self.assertEqual(
            tuple(policy["tool"] for policy in self.registry.tool_version_policies),
            (
                "macro.search_series",
                "macro.describe_series",
                "macro.get_series",
                "market.get_returns",
                "market.get_forward_returns",
                "market.technical_indicators",
                "timeseries.describe",
                "timeseries.align",
                "timeseries.correlation",
                "econometrics.regression",
                "econometrics.rolling_regression",
                "econometrics.stationarity",
                "econometrics.structural_breaks",
                "data.quality_audit",
                "timeseries.transform",
                "research.point_in_time_panel",
                "research.event_study",
                "alpha.signal_diagnostics",
                "research.walk_forward_backtest",
                "research.robustness_suite",
                "stats.multiple_testing",
                "forecast.evaluate",
                "company.search_filings",
                "company.get_share_count_history",
                "market.search_instruments",
                "news.search",
                "macro.get_release_calendar",
                "market.cross_sectional_performance",
                "macro.revision_analysis",
                "macro.standardize_surprises",
                "macro.get_liquidity_snapshot",
                "macro.get_liquidity_impulse",
                "macro.get_credit_conditions",
                "macro.regime_snapshot",
                "rates.get_funding_conditions",
                "rates.get_repo_facility_usage",
                "rates.curve_analytics",
                "energy.get_electricity_retail_sales",
                "energy.get_weekly_fundamentals",
                "company.get_fundamentals",
                "research.liquidity_credit_state",
            ),
        )
        self.assertEqual(
            self.registry.tool("company.search_filings")["version"],
            "1.0.0",
        )
        filing_v2 = self.registry.tool("company.search_filings", "2.0.0")
        self.assertEqual(
            filing_v2["operation_graph_id"],
            "tool_platform.company.search_filings.v2",
        )
        self.assertEqual(filing_v2["stores"], ["company"])
        self.assertEqual(
            filing_v2["datasets"],
            ["fixture.company.filings"],
        )
        self.assertIn("cursor", filing_v2["input_schema"]["required"])
        share_count_v2 = self.registry.tool(
            "company.get_share_count_history", "2.0.0"
        )
        self.assertEqual(
            share_count_v2["datasets"], ["fixture.company.fundamentals"]
        )
        self.assertEqual(share_count_v2["stores"], ["company"])
        instrument_search_v2 = self.registry.tool(
            "market.search_instruments", "2.0.0"
        )
        self.assertEqual(
            instrument_search_v2["datasets"], ["market.stage10.instruments"]
        )
        self.assertEqual(instrument_search_v2["stores"], ["market"])
        for name in (
            "macro.search_series",
            "macro.describe_series",
            "macro.get_series",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.registry.tool(name)["version"], "1.0.0")
                v2 = self.registry.tool(name, "2.0.0")
                self.assertEqual(
                    v2["operation_graph_id"],
                    f"tool_platform.{name}.v2",
                )
                self.assertEqual(
                    v2["datasets"],
                    [
                        "fixture.macro.rtdsm_employ",
                        "fixture.macro.rtdsm_employ_evidence",
                        "fixture.macro.stage3_catalog",
                        "macro.official_vintages",
                        "macro.official_vintages_evidence",
                    ],
                )
        for name in ("market.get_returns", "market.get_forward_returns"):
            with self.subTest(name=name):
                self.assertEqual(self.registry.tool(name)["version"], "1.0.0")
                self.assertEqual(
                    self.registry.tool(name, "1.0.0")["version"], "1.0.0"
                )
                v2 = self.registry.tool(name, "2.0.0")
                self.assertEqual(v2["operation_graph_id"], f"tool_platform.{name}.v2")
                self.assertEqual(
                    v2["datasets"],
                    [
                        "market.stage10.daily_prices",
                        "market.stage10.source_evidence",
                        "market.stage10.instruments",
                    ],
                )
                with self.assertRaises(RegistryError):
                    self.registry.tool(name, "3.0.0")
        for name in (
            "timeseries.describe",
            "timeseries.align",
            "timeseries.correlation",
            "econometrics.regression",
            "econometrics.rolling_regression",
            "econometrics.stationarity",
            "econometrics.structural_breaks",
            "data.quality_audit",
            "timeseries.transform",
            "market.technical_indicators",
            "research.point_in_time_panel",
            "research.event_study",
            "alpha.signal_diagnostics",
            "research.walk_forward_backtest",
            "research.robustness_suite",
            "stats.multiple_testing",
            "forecast.evaluate",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.registry.tool(name)["version"], "1.0.0")
                self.assertEqual(
                    self.registry.tool(name, "2.0.0")["datasets"], []
                )
                self.assertEqual(
                    self.registry.tool(name, "2.0.0")["operation_graph_id"],
                    f"tool_platform.{name}.v2",
                )
        self.assertEqual(len(self.registry.tool_version_policies), 41)
        self.assertEqual(
            sum(
                len(policy["variants"])
                for policy in self.registry.tool_version_policies
            ),
            57,
        )
        technical_v21 = self.registry.tool(
            "market.technical_indicators", "2.1.0"
        )
        self.assertEqual(technical_v21["datasets"], [])
        self.assertEqual(
            technical_v21["operation_graph_id"],
            "tool_platform.market.technical_indicators.v2_1",
        )
        self.assertIn(
            "supertrend_ai",
            technical_v21["input_schema"]["properties"]["indicator"]["enum"],
        )
        technical_v22 = self.registry.tool(
            "market.technical_indicators", "2.2.0"
        )
        self.assertEqual(technical_v22["datasets"], [])
        self.assertEqual(
            technical_v22["operation_graph_id"],
            "tool_platform.market.technical_indicators.v2_2",
        )
        self.assertIn(
            "swing_structure_forecast",
            technical_v22["input_schema"]["properties"]["indicator"]["enum"],
        )
        technical_v23 = self.registry.tool(
            "market.technical_indicators", "2.3.0"
        )
        self.assertEqual(technical_v23["datasets"], [])
        self.assertEqual(
            technical_v23["operation_graph_id"],
            "tool_platform.market.technical_indicators.v2_3",
        )
        self.assertIn(
            "kdj",
            technical_v23["input_schema"]["properties"]["indicator"]["enum"],
        )
        technical_v24 = self.registry.tool(
            "market.technical_indicators", "2.4.0"
        )
        self.assertEqual(technical_v24["datasets"], [])
        self.assertEqual(
            technical_v24["operation_graph_id"],
            "tool_platform.market.technical_indicators.v2_4",
        )
        self.assertIn(
            "williams_vix_fix",
            technical_v24["input_schema"]["properties"]["indicator"]["enum"],
        )
        technical_v25 = self.registry.tool(
            "market.technical_indicators", "2.5.0"
        )
        self.assertEqual(technical_v25["datasets"], [])
        self.assertEqual(
            technical_v25["operation_graph_id"],
            "tool_platform.market.technical_indicators.v2_5",
        )
        self.assertIn(
            "wavetrend_crosses",
            technical_v25["input_schema"]["properties"]["indicator"]["enum"],
        )
        technical_v26 = self.registry.tool(
            "market.technical_indicators", "2.6.0"
        )
        self.assertEqual(technical_v26["datasets"], [])
        self.assertEqual(
            technical_v26["operation_graph_id"],
            "tool_platform.market.technical_indicators.v2_6",
        )
        self.assertIn(
            "parabolic_sar",
            technical_v26["input_schema"]["properties"]["indicator"]["enum"],
        )
        technical_v27 = self.registry.tool(
            "market.technical_indicators", "2.7.0"
        )
        self.assertEqual(technical_v27["datasets"], [])
        self.assertEqual(
            technical_v27["operation_graph_id"],
            "tool_platform.market.technical_indicators.v2_7",
        )
        self.assertIn(
            "rolling_regression_line",
            technical_v27["input_schema"]["properties"]["indicator"]["enum"],
        )
        for name in (
            "econometrics.regression",
            "econometrics.rolling_regression",
            "econometrics.stationarity",
        ):
            with self.subTest(name=name, version="2.1.0"):
                declaration = self.registry.tool(name, "2.1.0")
                self.assertEqual(declaration["datasets"], [])
                self.assertEqual(
                    declaration["operation_graph_id"],
                    f"tool_platform.{name}.v2_1",
                )
                self.assertTrue(
                    declaration["input_schema_id"].endswith(":2.1.0")
                )
                self.assertTrue(
                    declaration["output_schema_id"].endswith(":2.1.0")
                )
        for name in (
            "market.cross_sectional_performance",
            "energy.get_electricity_retail_sales",
            "energy.get_weekly_fundamentals",
            "company.get_fundamentals",
        ):
            with self.subTest(name=name, version="2.1.0"):
                declaration = self.registry.tool(name, "2.1.0")
                self.assertEqual(
                    declaration["operation_graph_id"],
                    f"tool_platform.{name}.v2_1",
                )
                self.assertTrue(
                    declaration["input_schema_id"].endswith(":2.1.0")
                )
                self.assertTrue(
                    declaration["output_schema_id"].endswith(":2.1.0")
                )
        regression_v3 = self.registry.tool(
            "econometrics.regression",
            "3.0.0",
        )
        self.assertEqual(
            regression_v3["operation_graph_id"],
            "tool_platform.econometrics.regression.v3",
        )
        self.assertTrue(regression_v3["input_schema_id"].endswith(":3.0.0"))
        self.assertTrue(regression_v3["output_schema_id"].endswith(":3.0.0"))
        self.assertEqual(regression_v3["datasets"], [])
        with self.assertRaises(RegistryError):
            self.registry.tool(
                "econometrics.structural_breaks",
                "2.1.0",
            )

    def test_generated_v1_is_frozen_and_v2_catalog_is_separate(self) -> None:
        generate(PROJECT_ROOT, check=True)
        self.assertEqual(hashlib.sha256(V1_CATALOG.read_bytes()).hexdigest(), V1_CATALOG_SHA256)
        self.assertEqual(hashlib.sha256(V2_CATALOG.read_bytes()).hexdigest(), V2_CATALOG_SHA256)
        v1 = loads_strict(V1_CATALOG.read_bytes())
        v2 = loads_strict(V2_CATALOG.read_bytes())
        self.assertEqual(len(v1["contracts"]), 114)
        self.assertEqual(v2["schema_version"], "2.24.0")
        self.assertEqual(len(v2["contracts"]), 130)
        self.assertEqual(
            {item["tool"] for item in v2["contracts"]},
            {
                "market.get_price_series",
                "market.get_volume_series",
                "market.get_returns",
                "market.get_available_ticker",
                "market.get_forward_returns",
                "market.technical_indicators",
                "macro.search_series",
                "macro.describe_series",
                "macro.get_series",
                "macro.get_release_calendar",
                "timeseries.describe",
                "timeseries.align",
                "timeseries.correlation",
                "econometrics.regression",
                "econometrics.rolling_regression",
                "econometrics.stationarity",
                "econometrics.structural_breaks",
                "data.quality_audit",
                "timeseries.transform",
                "research.point_in_time_panel",
                "research.event_study",
                "alpha.signal_diagnostics",
                "research.walk_forward_backtest",
                "research.robustness_suite",
                "stats.multiple_testing",
                "forecast.evaluate",
                "stats.distribution_diagnostics",
                "stats.covariance_matrix",
                "stats.bootstrap_confidence_interval",
                "stats.principal_components",
                "company.search_filings",
                "company.get_share_count_history",
                "market.search_instruments",
                "market.cross_sectional_performance",
                "macro.revision_analysis",
                "macro.standardize_surprises",
                "macro.get_liquidity_snapshot",
                "macro.get_liquidity_impulse",
                "macro.get_credit_conditions",
                "macro.regime_snapshot",
                "rates.get_funding_conditions",
                "rates.get_repo_facility_usage",
                "rates.curve_analytics",
                "energy.get_electricity_retail_sales",
                "energy.get_weekly_fundamentals",
                "company.get_fundamentals",
                "research.liquidity_credit_state",
                "news.search",
            },
        )
        self.assertEqual(
            sum(item["id"].endswith(":1.0.0") for item in v2["contracts"]),
            16,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":1.0.0")
            },
            {
                "market.get_available_ticker",
                "market.get_price_series",
                "market.get_volume_series",
                "macro.get_release_calendar",
                "stats.distribution_diagnostics",
                "stats.covariance_matrix",
                "stats.bootstrap_confidence_interval",
                "stats.principal_components",
            },
        )
        self.assertEqual(
            sum(item["id"].endswith(":2.2.0") for item in v2["contracts"]),
            2,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":2.2.0")
            },
            {"market.technical_indicators"},
        )
        self.assertEqual(
            sum(item["id"].endswith(":2.3.0") for item in v2["contracts"]),
            2,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":2.3.0")
            },
            {"market.technical_indicators"},
        )
        self.assertEqual(
            sum(item["id"].endswith(":2.4.0") for item in v2["contracts"]),
            2,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":2.4.0")
            },
            {"market.technical_indicators"},
        )
        self.assertEqual(
            sum(item["id"].endswith(":2.5.0") for item in v2["contracts"]),
            2,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":2.5.0")
            },
            {"market.technical_indicators"},
        )

        self.assertEqual(
            sum(item["id"].endswith(":2.6.0") for item in v2["contracts"]),
            2,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":2.6.0")
            },
            {"market.technical_indicators"},
        )

        self.assertEqual(
            sum(item["id"].endswith(":2.0.0") for item in v2["contracts"]),
            82,
        )
        self.assertEqual(
            sum(item["id"].endswith(":2.1.0") for item in v2["contracts"]),
            18,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":2.1.0")
            },
            {
                "econometrics.regression",
                "econometrics.rolling_regression",
                "econometrics.stationarity",
                "market.technical_indicators",
                "market.cross_sectional_performance",
                "energy.get_electricity_retail_sales",
                "energy.get_weekly_fundamentals",
                "company.get_fundamentals",
                "news.search",
            },
        )

        self.assertEqual(
            sum(item["id"].endswith(":3.0.0") for item in v2["contracts"]),
            2,
        )
        self.assertEqual(
            {
                item["tool"]
                for item in v2["contracts"]
                if item["id"].endswith(":3.0.0")
            },
            {"econometrics.regression"},
        )

    def test_manifest_exposes_65_names_and_marks_only_versioned_v1_variants_deprecated(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            dispatcher = ToolDispatcher(
                explicit_store_map(Path(temporary) / "stores"), self.registry
            )
            manifest = dispatcher.manifest()
        self.assertEqual(len(manifest["tools"]), 65)
        self.assertEqual(len({item["name"] for item in manifest["tools"]}), 65)
        price_series = next(
            item
            for item in manifest["tools"]
            if item["name"] == "market.get_price_series"
        )
        self.assertEqual(price_series["version"], "1.0.0")
        self.assertEqual(price_series["lifecycle"], "experimental")
        self.assertEqual(
            price_series["compatibility"]["status"],
            "additive_native_v1",
        )
        self.assertEqual(
            price_series["input_schema"]["required"],
            ["ticker", "mode", "as_of", "date_only_policy", "limit"],
        )
        versioned = {
            "macro.search_series",
            "macro.describe_series",
            "macro.get_series",
            "market.get_returns",
            "market.get_forward_returns",
            "market.technical_indicators",
            "timeseries.describe",
            "timeseries.align",
            "timeseries.correlation",
            "econometrics.regression",
            "econometrics.rolling_regression",
            "econometrics.stationarity",
            "econometrics.structural_breaks",
            "data.quality_audit",
            "timeseries.transform",
            "research.point_in_time_panel",
            "research.event_study",
            "alpha.signal_diagnostics",
            "research.walk_forward_backtest",
            "research.robustness_suite",
            "stats.multiple_testing",
            "forecast.evaluate",
            "company.search_filings",
            "company.get_share_count_history",
            "market.search_instruments",
            "macro.get_release_calendar",
            "market.cross_sectional_performance",
            "macro.revision_analysis",
            "macro.standardize_surprises",
            "macro.get_liquidity_snapshot",
            "macro.get_liquidity_impulse",
            "macro.get_credit_conditions",
            "macro.regime_snapshot",
            "rates.get_funding_conditions",
            "rates.get_repo_facility_usage",
            "rates.curve_analytics",
            "energy.get_electricity_retail_sales",
            "energy.get_weekly_fundamentals",
            "company.get_fundamentals",
            "research.liquidity_credit_state",
            "news.search",
        }
        for name in versioned:
            tool = next(item for item in manifest["tools"] if item["name"] == name)
            self.assertEqual(tool["version"], "1.0.0")
            self.assertEqual(tool["lifecycle"], "deprecated")
            expected_versions = ["1.0.0", "2.0.0"]
            if name in {
                "econometrics.regression",
                "econometrics.rolling_regression",
                "econometrics.stationarity",
                "market.technical_indicators",
                "market.cross_sectional_performance",
                "energy.get_electricity_retail_sales",
                "energy.get_weekly_fundamentals",
                "company.get_fundamentals",
                "news.search",
            }:
                expected_versions.append("2.1.0")
            if name == "market.technical_indicators":
                expected_versions.extend(
                    ("2.2.0", "2.3.0", "2.4.0", "2.5.0", "2.6.0", "2.7.0")
                )
            if name == "econometrics.regression":
                expected_versions.append("3.0.0")
            self.assertEqual(
                [item["version"] for item in tool["versions"]],
                expected_versions,
            )
            self.assertEqual(tool["versions"][0]["lifecycle"], "deprecated")
            self.assertTrue(
                all(
                    item["lifecycle"] == "experimental"
                    for item in tool["versions"][1:]
                )
            )
            self.assertEqual(
                tool["deprecation"]["removal"],
                {"status": "not_scheduled", "milestone": None},
            )
        self.assertEqual(
            {
                item["name"]
                for item in manifest["tools"]
                if item["lifecycle"] == "deprecated"
            },
            versioned,
        )

    def test_new_econometric_projections_restore_exact_predecessors(self) -> None:
        pre_model_suite = econometrics_model_suite_v3_registry_profile(
            self.registry
        )
        self.assertEqual(pre_model_suite.registry_version, "2.38.0")
        self.assertEqual(
            pre_model_suite.source_sha256,
            PRE_ECONOMETRICS_MODEL_SUITE_V3_REGISTRY_SHA256,
        )
        self.assertEqual(
            pre_model_suite.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.5.0",
                "resource": (
                    "quant_data/generated/tool_contract_schemas_v2.json"
                ),
                "sha256": PRE_ECONOMETRICS_MODEL_SUITE_V3_CATALOG_SHA256,
            },
        )
        with self.assertRaises(RegistryError):
            pre_model_suite.tool("econometrics.regression", "3.0.0")
        self.assertEqual(
            pre_model_suite.tool("econometrics.regression", "2.1.0")["version"],
            "2.1.0",
        )

        pre_structural = structural_breaks_v2_registry_profile(
            self.registry
        )
        self.assertEqual(pre_structural.registry_version, "2.37.0")
        self.assertEqual(
            pre_structural.source_sha256,
            PRE_STRUCTURAL_BREAKS_V2_REGISTRY_SHA256,
        )
        self.assertEqual(
            pre_structural.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.4.0",
                "resource": (
                    "quant_data/generated/tool_contract_schemas_v2.json"
                ),
                "sha256": PRE_STRUCTURAL_BREAKS_V2_CATALOG_SHA256,
            },
        )
        self.assertNotIn(
            "econometrics.structural_breaks",
            {
                policy["tool"]
                for policy in pre_structural.tool_version_policies
            },
        )
        self.assertEqual(
            pre_structural.tool(
                "econometrics.stationarity",
                "2.1.0",
            )["version"],
            "2.1.0",
        )

        pre_stationarity = stationarity_v21_registry_profile(self.registry)
        self.assertEqual(pre_stationarity.registry_version, "2.36.0")
        self.assertEqual(
            pre_stationarity.source_sha256,
            PRE_STATIONARITY_V21_REGISTRY_SHA256,
        )
        self.assertEqual(
            pre_stationarity.raw["tool_version_schema_catalog"],
            {
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.3.0",
                "resource": (
                    "quant_data/generated/tool_contract_schemas_v2.json"
                ),
                "sha256": PRE_STATIONARITY_V21_CATALOG_SHA256,
            },
        )
        with self.assertRaises(RegistryError):
            pre_stationarity.tool(
                "econometrics.stationarity",
                "2.1.0",
            )


    def test_robust_v21_projection_restores_exact_235_registry(self) -> None:
        predecessor = robust_econometrics_v21_registry_profile(self.registry)
        self.assertEqual(
            (predecessor.schema_version, predecessor.registry_version),
            ("1.9.0", "2.35.0"),
        )
        self.assertEqual(
            predecessor.source_sha256,
            PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SHA256,
        )
        payload = (
            json.dumps(
                predecessor.raw,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            PRE_ROBUST_ECONOMETRICS_V21_REGISTRY_SHA256,
        )
        self.assertEqual(
            predecessor.raw["tool_version_schema_catalog"],
            {
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.2.0",
                "sha256": PRE_ROBUST_ECONOMETRICS_V21_CATALOG_SHA256,
            },
        )
        self.assertEqual(
            (
                len(predecessor.migrations),
                len(predecessor.datasets),
                len(predecessor.collectors),
            ),
            (39, 51, 50),
        )
        self.assertTrue(
            all(
                tuple(
                    variant["version"]
                    for variant in policy["variants"]
                )
                == ("2.0.0",)
                for policy in predecessor.tool_version_policies
            )
        )
        self.assertIs(
            robust_econometrics_v21_registry_profile(predecessor),
            predecessor,
        )

        drifted_variants = replace(
            self.registry,
            tool_version_policies=self.registry.tool_version_policies[:-1],
        )
        with self.assertRaises(RegistryError):
            robust_econometrics_v21_registry_profile(drifted_variants)
        drifted_catalog = replace(
            self.registry,
            raw={
                **self.registry.raw,
                "tool_version_schema_catalog": {
                    **self.registry.raw["tool_version_schema_catalog"],
                    "schema_version": "drifted",
                },
            },
        )
        with self.assertRaises(RegistryError):
            robust_econometrics_v21_registry_profile(drifted_catalog)

    def test_projection_restores_exact_233_then_232_then_231_registries(self) -> None:
        pre_econometrics = econometrics_v2_registry_profile(self.registry)
        self.assertEqual(
            (pre_econometrics.schema_version, pre_econometrics.registry_version),
            ("1.9.0", "2.33.0"),
        )
        self.assertEqual(
            pre_econometrics.source_sha256,
            PRE_ECONOMETRICS_V2_REGISTRY_SHA256,
        )
        self.assertEqual(
            tuple(
                policy["tool"]
                for policy in pre_econometrics.tool_version_policies
            ),
            (
                "market.get_returns",
                "market.get_forward_returns",
                "timeseries.describe",
                "timeseries.align",
                "timeseries.correlation",
            ),
        )
        self.assertEqual(
            pre_econometrics.raw["tool_version_schema_catalog"],
            {
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.1.0",
                "sha256": PRE_ECONOMETRICS_V2_CATALOG_SHA256,
            },
        )
        self.assertIs(
            econometrics_v2_registry_profile(pre_econometrics),
            pre_econometrics,
        )

        pre_timeseries = timeseries_analysis_v2_registry_profile(self.registry)
        self.assertEqual(
            (pre_timeseries.schema_version, pre_timeseries.registry_version),
            ("1.9.0", "2.32.0"),
        )
        self.assertEqual(
            pre_timeseries.source_sha256, PRE_TIMESERIES_V2_REGISTRY_SHA256
        )
        self.assertEqual(
            tuple(
                policy["tool"]
                for policy in pre_timeseries.tool_version_policies
            ),
            ("market.get_returns", "market.get_forward_returns"),
        )
        self.assertEqual(
            pre_timeseries.raw["tool_version_schema_catalog"],
            {
                "resource": "quant_data/generated/tool_contract_schemas_v2.json",
                "schema_id": "quant_data.tool_contract_catalog.v2",
                "schema_version": "2.0.0",
                "sha256": PRE_TIMESERIES_V2_CATALOG_SHA256,
            },
        )
        self.assertIs(
            timeseries_analysis_v2_registry_profile(pre_timeseries),
            pre_timeseries,
        )

        predecessor = market_return_v2_registry_profile(self.registry)
        self.assertEqual(
            (predecessor.schema_version, predecessor.registry_version),
            ("1.8.0", "2.31.0"),
        )
        self.assertEqual(predecessor.source_sha256, PREDECESSOR_REGISTRY_SHA256)
        self.assertEqual(predecessor.tool_version_policies, ())
        self.assertNotIn("tool_versions", predecessor.raw)
        self.assertNotIn("tool_version_schema_catalog", predecessor.raw)
        for dataset_id in (
            "market.stage10.daily_prices",
            "market.stage10.source_evidence",
            "market.stage10.instruments",
        ):
            self.assertEqual(
                next(item for item in predecessor.datasets if item.id == dataset_id).tool_ids,
                (),
            )
        self.assertIs(market_return_v2_registry_profile(predecessor), predecessor)

        drifted = replace(
            self.registry,
            tool_version_policies=self.registry.tool_version_policies[:-1],
        )
        with self.assertRaises(RegistryError):
            timeseries_analysis_v2_registry_profile(drifted)

    def test_v2_schema_and_cross_field_validation_run_without_a_reader(self) -> None:
        declaration = self.registry.tool("market.get_returns", "2.0.0")
        example = dict(declaration["examples"][0])
        for field, value in (
            ("identifier_kind", "ticker"),
            ("mode", "first_release"),
            ("date_only_policy", "guess"),
            ("method", "arithmetic"),
            ("horizon", 0),
            ("limit", 10001),
        ):
            with self.subTest(field=field):
                invalid = {**example, field: value}
                with self.assertRaises(ValidationError):
                    validate_schema(invalid, declaration["input_schema"])

        with self.assertRaises(ValidationError):
            parse_arguments(
                "stage10_market_return_v2",
                {**example, "mode": "as_of", "as_of": None},
                lambda value: value,
            )
        for name in (
            "timeseries.describe",
            "timeseries.align",
            "timeseries.correlation",
            "econometrics.regression",
            "econometrics.rolling_regression",
            "econometrics.stationarity",
            "market.technical_indicators",
            "research.point_in_time_panel",
            "research.event_study",
            "alpha.signal_diagnostics",
            "research.walk_forward_backtest",
            "research.robustness_suite",
            "stats.multiple_testing",
            "forecast.evaluate",
        ):
            with self.subTest(name=name):
                declaration = self.registry.tool(name, "2.0.0")
                for example in declaration["examples"]:
                    validate_schema(example, declaration["input_schema"])
        for name in (
            "econometrics.regression",
            "econometrics.rolling_regression",
        ):
            with self.subTest(name=name, version="2.1.0"):
                robust_declaration = self.registry.tool(name, "2.1.0")
                for robust_example in robust_declaration["examples"]:
                    validate_schema(
                        robust_example,
                        robust_declaration["input_schema"],
                    )

        regression_declaration = self.registry.tool(
            "econometrics.regression", "2.1.0"
        )
        regression_example = dict(regression_declaration["examples"][0])
        with self.assertRaises(ValidationError):
            validate_schema(
                {**regression_example, "diagnostic_lag": 19},
                regression_declaration["input_schema"],
            )

        decoder_calls = 0

        def decode(_: object) -> object:
            nonlocal decoder_calls
            decoder_calls += 1
            return object()

        with self.assertRaises(ValidationError):
            parse_arguments(
                "stage10_market_regression_v2_1",
                {
                    **regression_example,
                    "covariance": "hc3",
                    "hac_lag": 1,
                },
                decode,
            )
        rolling_declaration = self.registry.tool(
            "econometrics.rolling_regression", "2.1.0"
        )
        rolling_example = dict(rolling_declaration["examples"][0])
        for invalid in (
            {
                **rolling_example,
                "window": 8,
                "diagnostic_lag": 4,
            },
            {
                **rolling_example,
                "covariance": "newey_west_hac_bartlett",
                "window": 8,
                "hac_lag": 8,
                "diagnostic_lag": 1,
            },
        ):
            with self.assertRaises(ValidationError):
                parse_arguments(
                    "stage10_market_rolling_regression_v2_1",
                    invalid,
                    decode,
                )
        self.assertEqual(decoder_calls, 0)

        with self.assertRaises(ValidationError):
            parse_arguments(
                "stage10_market_return_v2",
                {
                    **example,
                    "start_date": "2026-08-12",
                    "end_date": "2026-08-10",
                },
                lambda value: value,
            )

    def test_technical_indicator_v2_dispatch_is_store_free(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            dispatcher = ToolDispatcher(
                explicit_store_map(root / "stores"), self.registry
            )
            declaration = self.registry.tool(
                "market.technical_indicators", "2.0.0"
            )
            result = dispatcher.call(
                "market.technical_indicators",
                dict(declaration["examples"][0]),
                tool_version="2.0.0",
            )
            self.assertFalse(any(root.iterdir()))

        self.assertEqual(result["tool"], "market.technical_indicators")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["series"]), 1)
        self.assertEqual(result["series"][0]["metadata"]["indicator"], "sma")
        self.assertEqual(
            [
                item["value"]
                for item in result["series"][0]["observations"]
            ],
            [
                None,
                None,
                Decimal("101"),
                Decimal("101.3333333333333333333333333333333"),
                Decimal("102"),
            ],
        )

    def test_http_unknown_version_is_sanitized_before_store_access(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            stores = explicit_store_map(Path(temporary) / "stores")
            application = Stage1Application(stores, self.registry)
            response = application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "market.get_returns",
                        "tool_version": "3.0.0",
                        "arguments": {},
                    }
                ).encode("utf-8"),
            )
            malformed = application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "market.get_returns",
                        "tool_version": "2.0.0 ",
                        "arguments": {},
                    }
                ).encode("utf-8"),
            )
            non_string = tuple(
                application.handle(
                    "POST",
                    "/api/agent-tools/call",
                    headers={"Content-Type": "application/json"},
                    body=dumps_strict(
                        {
                            "api_version": "1.0",
                            "tool": "market.get_returns",
                            "tool_version": value,
                            "arguments": {},
                        }
                    ).encode("utf-8"),
                )
                for value in (None, 7, {})
            )
            self.assertFalse(any(Path(temporary).iterdir()))
        payload = loads_strict(response.body)
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "unsupported_tool_version")
        self.assertEqual(payload["receipt"]["tool_version"], "3.0.0")
        self.assertEqual(payload["receipt"]["operation_graph_id"], None)
        malformed_payload = loads_strict(malformed.body)
        self.assertEqual(malformed.status, 400)
        self.assertEqual(malformed_payload["error"]["code"], "invalid_request")
        self.assertEqual(
            malformed_payload["receipt"],
            {"registry_revision": "2.64.0"},
        )
        for response in non_string:
            with self.subTest(body=response.body):
                payload = loads_strict(response.body)
                self.assertEqual(response.status, 400)
                self.assertEqual(payload["error"]["code"], "invalid_request")
                self.assertEqual(
                    payload["receipt"],
                    {"registry_revision": "2.64.0"},
                )


if __name__ == "__main__":
    unittest.main()
