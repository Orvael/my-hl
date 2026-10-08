<!-- Created: WIB 2026-10-08 22:22 -->
# SPEC — Portfolio margin (engine rules)

Source: trading/portfolio-margin.md (downloaded docs, ~/Documents/hl-docs/)
Fetched + verified WIB 2026-10-08.

## Quoted rules
- Portfolio margin = generalization of cross margin: all cross perp positions
  + spot balances margined together in one account. Sub-accounts separate.
- Eligibility: >$5M weighted volume OR account value >$10k; caps per asset.
- LTVs: HYPE 0.65, BTC 0.5. Orders auto-borrow against eligible collateral up
  to `token_balance * borrow_oracle_price * ltv`.
- Borrow interest (stablecoins): `0.05 + 4.75 * max(0, util - 0.8)` APY,
  continuously compounded, indexed hourly to the funding interval. Suppliers
  earn proportionally; protocol keeps 10% of interest as a liquidation buffer.
- Liquidation: when the whole PM account breaches its portfolio maintenance
  requirement (portfolio margin ratio).

## Engine mapping (v1, one documented substitution)
- Mode gate: account value >= $10k.
- Collateral registry {user: {asset: [qty, ltv, mark]}}; supply/borrow/repay
  move pool liquidity; borrowed value accrues the quoted rate hourly.
- PM maintenance = perp maintenance + borrowed_value x 1.2 (SUBSTITUTION: the
  docs define a full portfolio margin ratio; simplified, documented here).
- Auto-borrow on order placement = v-next.
