# Created: WIB 2026-10-08 14:03
# WIB 2026-10-08 14:0x — engine config, Hyperliquid imitation (my-hl)
# All verified numbers sourced in specs/*.md (fetched from live docs this session).

SCALE = 10**8  # canonical int scale for px, sz, USD (no floats anywhere in engine)

# Fees, perps tier-0 (specs/05: taker 0.045%, maker 0.015%)
TAKER_FEE = 45_000    # 0.00045 * 1e8
MAKER_FEE = 15_000    # 0.00015 * 1e8

# Funding constants (specs/01)
INTEREST_8H = 10_000      # 0.01% / 8h as 1e8-scale rate
FUNDING_CLAMP = 50_000    # clamp on (interest - premium): +/-0.05%
FUNDING_CAP = 4_000_000   # 4% / hour cap on F
FUNDING_INTERVAL_S = 3600

# docs say premium sampled every 5s and averaged; v1 samples ONCE at the hour boundary.
# Recorded simplification (v2: sub-hour premium samples).
PREMIUM_SAMPLE_S = 5

DEFAULT_ASSET = {
    "max_leverage": 20,
    "sz_decimals": 2,
    # Contract specs (verified specs/07): 20k USDC for BTC/ETH, 6k for others.
    "impact_notional_usd": 6_000 * SCALE,
}
IMPACT_NOTIONAL_LARGE = 20_000 * SCALE  # BTC/ETH per contract specs
