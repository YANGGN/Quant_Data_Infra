"""Fixed approved option roots; this declaration is not permission to refetch SPY."""
TIER1_ETFS=("SPY","QQQ","IWM","DIA","XLB","XLC","XLE","XLF","XLI","XLK","XLP","XLRE","XLU","XLV","XLY")
EXPANSION_ETFS=TIER1_ETFS[1:]
EXPANSION_START="2016-01-04"
EXPANSION_END="2026-09-23"
# Issuer listing date, not an inferred price-history floor.
LISTING_STARTS={"XLC":"2018-06-19"}
MAX_REQUESTS=120000
MAX_BYTES=256*1024**3
MAX_SECONDS=72*3600
