<!-- Created: WIB 2026-10-08 18:30 -->
# SPEC — Blockchain layer + economy sweep (engine rules)

Sources (fetched + verified WIB 2026-10-08):
- referrals.md, trading/delisting.md, hypercore/multi-sig.md
- Chain layer: ref section 14 (L1 blueprint) + 3.1 (HyperBFT one-block finality)

## Chain layer (the missing "blockchain")
- Genesis block: height 0, fixed prev_hash, state hash of the fresh engine.
- Block header: {height, prev_hash, ts, action_hash, proposer, state_hash}.
  action_hash = sha256 of canonical actions; state_hash = engine state hash.
- One-block finality (HyperBFT): committed blocks are final; fork choice is
  trivial (no reorgs) — the chain only extends.
- verify_chain(): hash links + action hashes + full replay from genesis into
  a fresh engine must reproduce the last state_hash. Tamper => mismatch.
- Explorer reads: GET /block/{h}, GET /chain/latest.

## Referrals (quoted)
- Code requires $10k volume. Referrer earns 10% of referred users' fees
  (less their discount), first $1B. Referred user: 4% fee discount first $25M.
- Rewards accrue in quote assets, claimable >$1, paid to spot balance.

## Delisting (quoted)
- Validators vote; the perp settles to the 1-hour time-weighted spot oracle
  price BEFORE the voting time; all orders cancel; no new orders accepted.

## Multi-sig (quoted)
- ConvertToMultiSigUser {authorized users (max 10), threshold}; then ALL
  actions wrapped in MultiSig {target, leader, signatures[]}; leader must be
  authorized; only the leader's nonce is validated/updated; wraps any action
  incl. ConvertToMultiSigUser to change or revert it.

## Fee tiers (fees.md, verified earlier)
- 14d weighted volume = perps volume + 2x spot volume.
- Perps taker/maker tier-0 0.045%/0.015%; tiers at >5M/25M/100M/500M/2B/7B
  step down (0.040/0.012, 0.035/0.008, 0.030/0.004, 0.028/0, ...).
- Staking discounts apply on top (not in this sweep).

## Points / genesis distribution (ref 2.17, 6.x)
- Points ledger: activity accrual action; airdrop = distribution action that
  assigns token balances by points share. Distribution mechanics, not money.

## EVM-side gas
- CoreWriter/EVM actions burn native HYPE gas from the sender's native
  balance (gas_used counter x gas price); precompile reads already metered.
