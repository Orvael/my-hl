<!-- Created: WIB 2026-10-08 16:08 -->
# SPEC — Margin tiers, caps, isolated margin, partial liq (engine rules)

Sources (fetched + verified WIB 2026-08-08):
- trading/margin-tiers.md, trading/contract-specifications.md, trading/margining.md

## Tiered maintenance (quoted)
maintenance_margin = notional * mm_rate(tier) - maintenance_deduction(tier)
mm_rate(tier n) = (initial margin rate at max leverage of tier n) / 2
deduction(0) = 0
deduction(n) = deduction(n-1) + lower_bound(n) * (rate(n) - rate(n-1))
Tier chosen by notional position value. Example (mainnet BTC):
0-150M -> 40x, >150M -> 20x.

## Order caps (quoted)
Max market order value: $30M for max_lev >= 25; $5M for [20,25);
$2M for [10,20); otherwise $500k. Max limit = 10 * market cap.
(These scale by the asset's max leverage, not the user's leverage setting.)

## Funding impact notional (CORRECTED v1 config)
"20000 USDC for BTC and ETH; 6000 USDC for all other assets."
v1 shipped 200k default — WRONG. Config now: btc/eth 20_000, others 6_000.

## Isolated margin (quoted, margining.md)
- Margin bucketed per position; liquidation inputs = isolated margin +
  isolated notional ONLY (other positions untouched).
- strict-isolated: margin cannot be removed; margin leaves proportionally
  as the position closes.
- Add/remove isolated margin actions; liquidation of an isolated position
  does not touch cross positions.
- transfer rule: withdraw/transfer needs remaining >= max(init_req, 10% notional).

## Partial liquidations (quoted)
For liquidatable positions > 100k USDC (10k testnet): only 20% of the position
is sent as a market liq order; 30s cooldown after any partial liq block, during
which liq orders are full-size. v1 upgrade: implement with cooldown by block time.

## Engine mapping
- assets config gains: margin_tiers [{lower_usd, max_lev}, ...] (optional;
  single-tier assets keep max_leverage only).
- clearing.maintenance_req uses the tier formula; MAINTENANCE_LEVERAGE in the
  liq-price display formula = the position's tier max_lev.
- Position gains iso_margin; apply_side on isolated accounts keeps the bucket:
  opening deducts from cross balance into the bucket, closing returns
  proportional margin + realized pnl to cross.
- Order caps enforced in engine.place via notional check.
