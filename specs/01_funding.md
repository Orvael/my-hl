# SPEC — Funding (engine rule)

Source: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding.md
Fetched + verified verbatim WIB 2026-10-08 13:5x against live docs.

## Formula (quoted from docs)
`Funding Rate (F) = Average Premium Index (P) + clamp(interest rate - Premium Index (P), -0.0005, 0.0005)`

- Interest rate: fixed 0.01% per 8 hours (0.00125%/hour).
- Premium sampled every 5 seconds, averaged over the hour.
- F is an 8h rate; funding PAID EVERY HOUR at 1/8 of F.
- Cap: 4% per hour (applies to F before payment).
- Payment = `position_size * oracle_price * funding_rate` — oracle price, NOT mark.
- Premium = impact_price_difference / oracle_px, where
  `impact_price_difference = max(impact_bid_px - oracle_px, 0) - max(oracle_px - impact_ask_px, 0)`
  impact bid/ask = average execution price to trade `impact_notional_usd` on each side.
- HIP-3 variant (different premium formula) — NOT in v1.

## Worked example (docs' own numbers — golden test)
- interest 0.01%; impact bid $10,100; oracle $10,000.
- premium = (10,100 - 10,000)/10,000 = 0.01
- clamped = min(max(0.01% - 1%, -0.05%), 0.05%) = -0.05%
- F = 1% + (-0.05%) = 0.95%  (8h rate)

## Engine mapping (v1)
- All rates as int in 1e8 scale (0.95% = 950_000).
- Premium computed hourly from OUR book: walk levels until impact_notional filled,
  avg fill px per side, apply the quoted impact_price_difference formula vs oracle input.
- Hourly settle: payment = -qdiv(szi * oracle_px * F_hourly, SCALE), F_hourly = F/8.
  Long pays when F > 0. Zero-sum across accounts (fees vault untouched).
- Config: interest = 10_000 (0.01% in 1e8), clamp = 50_000, cap = 4_000_000.
