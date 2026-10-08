<!-- Created: WIB 2026-10-08 14:01 -->
# SPEC — Order book, tick/lot, STP, ADL, fees (engine rules)

Sources (fetched + verified WIB 2026-10-08 13:5x):
- trading/order-book.md, trading/order-types.md, api/tick-and-lot-size.md
- trading/auto-deleveraging.md, trading/fees.md

## Order book (quoted)
- "Orders are matched in price-time priority."
- "price is an integer multiple of the tick size, and size is an integer multiple of lot size."

## Tick and lot (perps)
- Price: <=5 significant figures AND <= (6 - szDecimals) decimal places; integer prices always allowed.
  Doc examples: 1234.5 valid, 1234.56 not (sig figs); 0.001234 valid, 0.0012345 not (>6dp);
  szDecimals=1 -> 0.01234 valid, 0.012345 not.
- Size: <= szDecimals decimals.

## Order types v1
- Limit GTC / IOC / ALO (post-only: canceled if it would cross), reduce-only flag.
- Market = aggressive limit (API requires px anyway).
- NOT in v1: stops/takes/trailing/TWAP/chase/scale, isolated margin, HIP-3, spot, fee tiers >0.

## STP (simplification — STP page not fetched)
- Same-user maker hit during sweep -> skip that maker order, keep sweeping. Revisit vs docs later.

## ADL (quoted)
- Trigger: "account value or isolated position value becomes negative".
- Ranking index = (mark_price / entry_price) * (notional_position / account_value).
- Opposite-side traders "closed at the previous mark price against the now underwater user".
- Invariant (quoted): "a user who has no open positions will not socialize any losses."

## Fees (v1 flat tier-0)
- Perps tier-0: taker 0.045% = 45_000 (1e8 scale), maker 0.015% = 15_000.
- Volume tiering, spot/HIP-3 schedules: NOT in v1.
- impact_notional per-asset: NOT verified (contract-specifications page not fetched);
  config default 200_000 USD, flagged unverified.
