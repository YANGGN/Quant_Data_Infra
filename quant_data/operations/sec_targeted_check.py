"""Explicit finite SEC discrepancy/gap checks; never called by the daily timer."""
import argparse
from .sec_conditional_refresh import run_conditional_refresh
from .sec_selected_refresh import PROJECT_ROOT, STATE_ROOT
from .collection_transport import host_fetch
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.market.collection_bindings import load_bindings, pin_binding
from quant_data.registry import load_registry
from quant_data.stores import StoreMap

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cik", action="append", required=True, help="Exact ten-digit CIK; up to ten")
    parser.add_argument("--check-id", required=True, help="Stable ID for this one authorized check")
    parser.add_argument("--reason", required=True, help="The discrepancy or missing data being checked")
    args = parser.parse_args(argv)
    # Validate finite scope before resolving credentials or constructing transport.
    import re
    if (len(args.cik) > 10 or len(set(args.cik)) != len(args.cik)
        or any(not re.fullmatch("[0-9]{10}", c) for c in args.cik)
        or not re.fullmatch("[a-z0-9][a-z0-9-]{0,63}", args.check_id)
        or not 1 <= len(args.reason.strip()) <= 300):
        parser.error("Use up to ten unique CIKs, a lowercase check ID and a nonempty reason")
    try:
        stores = StoreMap.four_explicit(**{r: PROJECT_ROOT / "data" / (r + ".sqlite")
            for r in ("market", "macro", "company", "news")})
        registry = load_registry(PROJECT_ROOT / "config/system_registry.json",
            project_root=PROJECT_ROOT, environment={})
        selection = pin_binding(stores, load_bindings(PROJECT_ROOT / "config/collection_bindings.json")["sec_filings_companyfacts"])
        if selection.binding.mode != "active" or len(selection.subjects) != 2248:
            raise ConflictError("SEC selected binding differs from its activated universe")
        fetch = host_fetch(PROJECT_ROOT, "sec")
        result = run_conditional_refresh(root=STATE_ROOT, stores=stores, registry=registry,
            selection=selection, fetch=fetch, secret_values=fetch.secret_values,
            request_limit=2 * len(args.cik), byte_limit=128 * 1024 * 1024 * len(args.cik),
            second_limit=min(7200, 600 * len(args.cik)),
            check_ciks=tuple(sorted(args.cik)), check_id=args.check_id, check_reason=args.reason)
        print(dumps_strict(result))
        return 0 if result["outcome"] in ("succeeded", "already_recorded") else 75
    except (ConflictError, ResourceLimitError, ValidationError) as error:
        print(dumps_strict({"outcome": "blocked", "error": type(error).__name__}))
        return 75

if __name__ == "__main__":
    raise SystemExit(main())
