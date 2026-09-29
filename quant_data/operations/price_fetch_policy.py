"""Reviewed exclusions from future recurring price acquisition, not identity edits."""
from datetime import date

# Effective trading boundaries from the primary exchange notices. Historical
# identities, source mappings and canonical facts remain immutable.
RETIRED_PRICE_SYMBOLS = {
    "ATAI": {
        "last_trading_date": "2026-09-10",
        "reason": "acquired_and_suspended",
        "source": "https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2026-633",
    },
    "IRBO": {
        "last_trading_date": "2024-08-09",
        "reason": "symbol_changed_to_ARTY",
        "source": "https://www.miaxglobal.com/alert/2024/08/09/miax-exchange-group-options-markets-corporate-action-alert-ishares-robotics",
    },
}


def current_price_targets(rows, *, session):
    """Remove reviewed retired symbols before planning recurring requests.

    This is acquisition policy only. It neither aliases ARTY to IRBO nor
    changes historical request windows, identities or stored coverage.
    """
    day = date.fromisoformat(session)
    active, excluded = [], []
    for row in rows:
        symbol = row["provider_symbol"]
        rule = RETIRED_PRICE_SYMBOLS.get(symbol)
        if rule is not None and day > date.fromisoformat(rule["last_trading_date"]):
            excluded.append({"symbol": symbol, **rule})
        else:
            active.append(row)
    return tuple(active), tuple(excluded)
