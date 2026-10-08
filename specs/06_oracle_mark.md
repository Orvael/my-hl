<!-- Created: WIB 2026-10-08 16:07 -->
# SPEC — Oracle aggregation + robust mark price (engine rules)

Sources (fetched + verified WIB 2026-10-08):
- hypercore/oracle.md, trading/robust-price-indices.md

## Oracle (quoted)
- "validators are responsible for publishing spot oracle prices for each perp
  asset every 3 seconds."
- Per-validator oracle = weighted median of Binance, OKX, Bybit, Kraken,
  Kucoin, Gate IO, MEXC, Hyperliquid spot mids, weights 3,2,2,1,1,1,1,1.
  (HL spot excluded for BTC-style assets; external excluded for HYPE-style.)
- Final clearinghouse oracle = weighted median of validator submissions,
  weighted by stake.

## Mark price (quoted)
Mark = median of three inputs:
1. oracle + 150s EMA of (HL mid - oracle)
2. median(best bid, best ask, last trade) on HL
3. weighted median of Binance, OKX, Bybit, Gate IO, MEXC perp mids (3,2,2,1,1)
If exactly 2 of 3 exist: also add the 30s EMA of input 2 to the median inputs.
Used for: margining, liquidations, TP/SL triggers, unrealized pnl.

## EMA (docs formula)
numerator -> numerator*exp(-t/2.5min) + sample*t ; denominator likewise.
Small-t limit reduces to a standard EMA with period 150s (30s for the short one).
ENGINE v1: integer EMA, ema += qdiv((sample - ema) * t, period). Deterministic.

## Engine mapping
- OracleEngine: validators {name: stake}; source prices arrive as actions
  {'t':'oracle_src', coin, validator, src, px} — engine folds to per-validator
  weighted median, then stake-weighted median across validators.
- MarkEngine: tracks last trade px + EMA of (mid - oracle); external perp mids
  arrive as actions {'t':'ext_px', coin, src, px}. Missing inputs follow the
  2-of-3 rule exactly as quoted.
- mark(coin) replaces v1's mark = oracle everywhere (margin, liq, triggers, upnl).
