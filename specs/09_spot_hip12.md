<!-- Created: WIB 2026-10-08 16:09 -->
# SPEC — Spot: HIP-1 tokens, spot books, HIP-2 Hyperliquidity (engine rules)

Sources (fetched + verified WIB 2026-10-08):
- hip-1-native-token-standard.md, hip-2-hyperliquidity.md, fees.md (spot tiers)

## HIP-1 token standard (quoted)
- Capped-supply fungible tokens with onchain spot order books.
- Genesis params: name (<=6 chars), weiDecimals, szDecimals
  (szDecimals + 5 <= weiDecimals), maxSupply, initialWei, anchorTokenWei.
- Lot size on spot books = 10^(weiDecimals - szDecimals) minimal units.
- USDC spot: szDecimals = weiDecimals = 8.
- Fees: fees collected in non-USDC tokens go to the token deployer
  (share 0-100%, only decreasing); other quote tokens -> Assistance Fund.
- Dust: daily 00:00 UTC, balances < 1 lot and <= $1 notional aggregated,
  market-sold, USDC returned weighted. (Dust = v-next, not this build.)

## Spot books
- Pairs parametrized by base+quote; HIP-1 tokens get a native USDC pair.
- Spot fees tier-0: taker 0.070%, maker 0.040%.

## HIP-2 Hyperliquidity (quoted)
- Params: startPx, nOrders, orderSz, nSeededLevels. USDC pairs only.
- Price range: px_0 = startPx, px_i = round(px_{i-1} * 1.003).
- Updates every block where block time >= 3s since previous update.
- After update: target nFull = floor(balance / orderSz) full asks + partial;
  refills filled tranches to orderSz on the side with available balance.
- Guarantee: 0.3% spread every 3s. Part of block transition logic (no keepers).

## Engine mapping (v1 of the spot layer)
- Token: {name, wei_decimals, sz_decimals, max_supply, deployer, fee_share_pct}.
- SpotStore: balances {user: {token_idx: wei}} (int wei), pair books
  OrderBook(base_idx, quote_idx); orders hold wei-integer px/sz.
- Pair px = wei quote per wei base (integers, no floats).
- HL (Hyperliquidity) strategy = account 'hip2_<idx>' holding base+quote wei;
  update pass: if block_ts - last >= 3s: cancel own resting, re-place range
  around current mid (px_i = px_{i-1} * 1.003 // 1) as ALOs.
- Fees: taker pays in quote; split per deployer fee_share_pct (base-token fees),
  rest burned (removed from supply). USDC-quote base fees -> deployer share,
  remainder burned; quote-token fees (non-USDC quote) -> Assistance Fund vault.
