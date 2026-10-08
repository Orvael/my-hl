<!-- Created: WIB 2026-10-08 14:48 -->
# my-hl — Hyperliquid imitation engine

WIB 2026-10-08 14:4x. Deterministic single-node perp exchange, built as an
imitation of Hyperliquid's HyperCore design.

Basis: specs/ = distilled from LIVE Hyperliquid docs, fetched and verified
2026-10-08 (funding, margining, liquidations, entry/PnL, order
types, tick/lot, ADL, fees). An independently compiled reference document's
engine claims were re-verified against those live pages.

## Run

    cd ~/Documents/my-hl
    python3 -c "
    import sys; sys.path.insert(0, '.')
    import tests.test_engine as t, tests.test_book as tb
    t.test_trade_flow(); t.test_liq_keeps_remainder(); t.test_backstop()
    t.test_adl(); t.test_journal_determinism(); t.test_funding_boundary_once()
    tb.test_price_priority(); tb.test_fifo_and_tifs(); tb.test_stp_cancel_conserve()
    print('ALL TESTS GREEN')
    "

## Layout

    specs/   verified engine rules (quote + engine mapping per page)
    hl/      config, num, types, book (CLOB), clearing (margin/funding/liq/adl),
             engine (blocks, deterministic), journal (replay + state hash)
    tests/   zero-dependency assert tests (9 scenarios)

## v1 simplifications (each documented in specs/)
- mark = oracle (robust index = v2)
- funding premium: one sample per hour boundary (docs: 5s sampling)
- liquidation: full-size forced closes only (docs: 20% slices for >100k)
- backstop transfers position+margin unpriced (docs silent on fill pricing)
- STP v1: sweep stops at same-user maker (STP page not fetched)
- isolated margin, HIP-3, spot: not in v1
