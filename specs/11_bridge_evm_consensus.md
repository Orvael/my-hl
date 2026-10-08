<!-- Created: WIB 2026-10-08 16:09 -->
# SPEC — Bridge, EVM sidecar, consensus (engine rules, simulated layers)

Sources (fetched + verified WIB 2026-10-08):
- hypercore/usdc.md, for-developers/hyperevm/* (interacting-with-hypercore,
  transfers, interaction-timings), hypercore/oracle.md
- The legacy Arbitrum bridge details (validator-signed, 2/3, dispute window)
  come from the user's compiled reference (section 5.3), marked [U] there;
  simulated here with those semantics.

## Bridge (simulated validator set)
- Deposits: credited when >2/3 of stake-signed; dispute-locked otherwise.
- Withdrawals: debited immediately; after 2/3 stake signs, an EVM-side claim
  releases; dispute window during which a challenger can lock the release.
- v1: 1 USDC withdrawal fee to cover release gas (as quoted in ref 5.3).

## EVM sidecar (architecture simulation)
- EVM accounts: {address: {token_idx: wei}} + native HYPE gas balances.
- System addresses: first byte 0x20 + token index big-endian
  (token 200 -> 0x2000...00c8); HYPE exception 0x2222...2.
- Core->EVM: sendAsset to a system address -> queued, credited next EVM block.
- EVM->Core: ERC20-style transfer to system address -> Transfer log processed
  in the SAME L1 block, after the EVM block, BEFORE CoreWriter actions.
- CoreWriter 0x3333...: action encoding = version byte (1) + 3-byte BE action
  id + ABI-style payload. IDs: 1 limit order, 2 vault transfer, 3 token
  delegate, 4 staking deposit, 5 staking withdraw, 6 spot send, 7 USD class
  transfer. Order/vault actions delayed a few seconds onchain.
- Read precompiles: 0x800 + slot; scaled reads: perp px scaled 10^(6-szDec),
  spot 10^(8-szDec); gas 2000 + 65*(in+out) (modeled as a counter).
- Block order per docs: L1 block -> EVM block -> EVM->Core transfers ->
  CoreWriter actions.
- Isolation: 'contracts' = deterministic Python callables whose ONLY state
  access is the narrow interface (system-address transfer, CoreWriter queue,
  read precompiles). A contract crash cannot touch clearing state.

## Consensus (HyperBFT-style simulation)
- Validator set {name: stake}; leader round-robin by stake order proposes a
  block of actions; commit when stake-weighted votes > 2/3 of total stake.
- Finality = one block; deterministic execution -> identical state hash on
  all honest nodes.
- Jailing: absent vote / equivocation (two different blocks same height) ->
  jailed (excluded from signing; stake locked; NO slashing — matches
  Hyperliquid's jailing-not-slashing model).
- Faults: f < 1/3 by stake down -> liveness holds; >= 1/3 by stake -> halt.
