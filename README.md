<!-- Created: WIB 2026-10-08 14:48 -->
# my-hl — Hyperliquid imitation engine

WIB 2026-10-08 14:4x. Deterministic single-node perp exchange, built as an
imitation of Hyperliquid's HyperCore design.

Basis: specs/ = distilled from LIVE Hyperliquid docs, fetched and verified
2026-10-08 (funding, margining, liquidations, entry/PnL, order
types, tick/lot, ADL, fees). An independently compiled reference document's
engine claims were re-verified against those live pages.

## Run

    ./run.sh                 # one command: testnet + UI at http://localhost:8770
    PORT=9000 ./run.sh       # custom port
    python3 tools/bench.py   # performance: ~480k orders/sec place, ~680k match/sec

## What you get in one command
Engine + chain + API + streaming UI, seeded BTC market with a live price walk,
PURR/USDC spot with Hyperliquidity, HLP + user-created vaults, staking,
leaderboard, referral program, portfolio margin, faucet. Sign in with any
account name and trade.

## Run (dev)

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
    hl/      config, num, types, book (CLOB), clearing, engine, journal,
             oracle (validator oracle + robust mark), triggers (TP/SL, OCO,
             TWAP), spot (HIP-1, spot books, HIP-2 Hyperliquidity),
             hip3 (builder-deployed perps), bridge (validator-signed),
             evm_side (sidecar sim: system addresses, CoreWriter,
             precompiles), consensus (HyperBFT-style sim with jailing)
    tests/   zero-dependency assert tests (25 scenarios, all green)

## v2 layers added (each simulated faithfully to specs/06-11)
Oracle aggregation (stake-weighted median), robust mark price (3-input median
with EMAs), margin tiers, isolated margin, partial liquidations, order caps,
trigger orders, spot trading, HIP-1 tokens, HIP-2 quoter, HIP-3 builder perps
(stake, auctions, fee share, halt), validator bridge with dispute window,
EVM sidecar architecture, multi-node consensus with jailing.

## v1 simplifications (each documented in specs/)
- mark = oracle (robust index = v2)
- funding premium: one sample per hour boundary (docs: 5s sampling)
- liquidation: full-size forced closes only (docs: 20% slices for >100k)
- backstop transfers position+margin unpriced (docs silent on fill pricing)
- STP v1: sweep stops at same-user maker (STP page not fetched)
- isolated margin, HIP-3, spot: not in v1
