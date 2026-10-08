<!-- Created: WIB 2026-10-08 18:23 -->
# SPEC — API layer (engine rules)

Sources (fetched + verified WIB 2026-10-08):
- for-developers/api/info-endpoint.md, for-developers/api/signing.md

## Info endpoint (real shapes, quoted)
- POST /info, body {"type": ...}. allMids returns {coin: string px};
  "if the book is empty, the last trade price will be used as a fallback".
- openOrders rows: {coin, limitPx, oid, side: "A"|"B", sz, timestamp}.
- userFills rows: {closedPnl, coin, crossed, dir, hash, oid, px, side, sz,
  time, fee, feeToken, tid} (max 2000).

## Signing (quoted)
- Two schemes: sign_l1_action vs sign_user_signed_action; msgpack field order
  matters; trailing zeroes matter; lowercase addresses.
- SUBSTITUTION in the imitation: signature = stub envelope {signer: addr};
  nonce replay protection enforced; API wallets authorized via
  approveApiWallet act for their master. Real EIP-712 = future work.

## Exchange endpoint
- {action, nonce, signature} envelope; action types: order (with optional
  builder {b, f}), cancel, approveApiWallet, createSubaccount, usdSend,
  usdClassTransfer.
- Builder fee: f bps charged to the taker per fill, routed to the builder.
- Rate limiting per user (docs: weight-based; sim: request count per window).

## Substitutions (honest labels)
- WS subscriptions (l2Book/trades/userEvents) -> GET /events?since=N poll
  returning the same payloads. Real RFC6455 server = future work.
- Display formatting uses float str(); the ENGINE stays integer-only.
