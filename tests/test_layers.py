# Created: WIB 2026-10-08 16:3x — HIP-3, bridge, EVM side, consensus (my-hl)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.hip3 import Hip3Store, DEPLOY_STAKE
from hl.bridge import Bridge, WITHDRAW_FEE
from hl.evm_side import EvmSide, system_address, run_contract
from hl.consensus import HyperBFTSim
from hl.config import SCALE

S = SCALE
FAILS = []


def check(cond, label):
    if not cond:
        FAILS.append(label)
    return cond


def end():
    if FAILS:
        print("FAILS:", FAILS)
        sys.exit(1)
    print("test_layers OK")
    FAILS.clear()


def test_hip3():
    h = Hip3Store()
    check(not h.deploy_dex("d1", "mydex", 0)[0], "h3-no-stake")
    h.stake("d1", DEPLOY_STAKE)
    ok, why = h.deploy_dex("d1", "mydex", 0)
    check(ok, "h3-deploy")
    check(not h.deploy_dex("d1", "other", 1)[0], "h3-one-dex")
    for i in range(3):
        ok, why = h.list_asset("d1", "mydex", "C%d" % i, 2)
        check(ok and why == "free", "h3-free-%d" % i)
    ok, why = h.list_asset("d1", "mydex", "C4", 3)
    check(not ok and why == "auction", "h3-auction-needed")
    h.start_auction("C4", 100)
    px0 = h.auction_px(100)
    px_mid = h.auction_px(100 + 31 * 3600 // 2)
    px_end = h.auction_px(100 + 31 * 3600)
    check(px_mid > px_end == 500 * S, "h3-decay")  # decays to exactly 500
    end()


def test_hip3_fees():
    h = Hip3Store()
    h.stake("d1", DEPLOY_STAKE)
    h.deploy_dex("d1", "mydex", 0)
    check(h.taker_fee_for("mydex") == 45_000, "h3f-base")
    h.set_fee_share("d1", "mydex", 20_000)  # 200%
    check(h.taker_fee_for("mydex") == 90_000, "h3f-raised")
    proto, dep = h.fee_split("mydex", 90_000)
    check(proto == 30_000 and dep == 60_000, "h3f-split")
    check(not h.set_fee_share("d1", "mydex", 10_000), "h3f-only-down")
    end()


def test_bridge():
    b = Bridge(total_stake=100)
    got = 0
    for i, v in enumerate(("a", "b", "c")):
        r = b.deposit_sig("alice", 500 * S, v, 40 if i < 2 else 20)
        if r:
            got = r
    check(got == 500 * S, "br-deposit-2of3")
    w = b.request_withdrawal("alice", 500 * S, 0)
    check(w["usd"] == 500 * S - WITHDRAW_FEE, "br-fee")
    r = b.sign_withdrawal(0, "a", 40, 10)
    check(r == "signed", "br-signed")
    r = b.sign_withdrawal(0, "b", 40, 10)
    check(r == "pending_dispute", "br-dispute-window")
    check(b.dispute(0), "br-locked")
    r = b.sign_withdrawal(0, "c", 20, 4000)
    check(r == "locked", "br-locked-release")
    end()


def test_evm_side():
    e = EvmSide()
    check(system_address(200) == "0x20000000000000000000000000000000000000c8",
          "evm-sysaddr")
    e._add("user1", 5, 1_000)
    check(e.send_to_system("user1", 5, 400), "evm-send")
    check(e.bal("user1", 5) == 600, "evm-debited")
    transfers, cw = e.process_block(10)
    check(len(transfers) == 1, "evm-transfer-first")
    e.send_asset_core_to_evm("user1", "user1", 5, 100)
    e.process_block(11)
    check(e.bal("user1", 5) == 700, "evm-credited-next-block")
    e.native["user1"] = 100_000_000  # fund gas
    e.corewriter("user1", 1, b"payload", 20)
    tr, cw = e.process_block(23)
    check(len(cw) == 1 and cw[0]["action_id"] == 1, "evm-cw-delayed")
    check(cw[0]["blob"][0] == 1 and cw[0]["blob"][1] == 0, "evm-cw-encoding")
    slot, v = e.precompile_read(7, 123 * S)
    check(slot == 0x807 and v == 123 * S, "evm-precompile")
    end()


def test_contract_isolation():
    def bad_contract(side):
        raise RuntimeError("boom")
    e = EvmSide()
    e._add("u", 1, 500)
    out, err = run_contract(e, bad_contract)
    check(out is None and err == "boom", "iso-contained")
    check(e.bal("u", 1) == 500, "iso-state-intact")
    end()


def _engine_builder():
    from hl.engine import Engine
    return lambda: Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                                   "impact_notional_usd": 6_000 * S}})


def test_consensus():
    sim = HyperBFTSim({"v1": 40, "v2": 30, "v3": 20, "v4": 10}, _engine_builder())
    acts = [{"t": "oracle", "coin": "BTC", "px": 100 * S},
            {"t": "deposit", "user": "a", "usd": 1_000 * S}]
    r = sim.commit(0, acts)
    check(r["status"] == "committed", "cn-commit")
    check(sim.agree(), "cn-agree")
    # one validator skips voting -> jailed; 90/100 still votes -> live
    r = sim.commit(1, [{"t": "deposit", "user": "a", "usd": 100 * S}],
                   faulty=("v4",))
    check(r["status"] == "committed" and r["jailed"] == ["v4"], "cn-jailed")
    check(sim.agree(), "cn-agree-2")
    # two more jailed -> only 40/100 stake votes -> no quorum
    r = sim.commit(2, [{"t": "deposit", "user": "a", "usd": 100 * S}],
                   faulty=("v2", "v3"))
    check(r["status"] == "no_quorum", "cn-noquorum")
    # deterministic replay: a fresh sim running the SAME rounds reaches the same hash
    sim2 = HyperBFTSim({"v1": 40, "v2": 30, "v3": 20, "v4": 10}, _engine_builder())
    sim2.commit(0, acts)
    sim2.commit(1, [{"t": "deposit", "user": "a", "usd": 100 * S}], faulty=("v4",))
    sim2.commit(2, [{"t": "deposit", "user": "a", "usd": 100 * S}],
                faulty=("v2", "v3"))
    from hl.journal import state_hash
    check(state_hash(sim.nodes["v1"].engine)
          == state_hash(sim2.nodes["v1"].engine), "cn-determinism")
    end()


test_hip3()
test_hip3_fees()
test_bridge()
test_evm_side()
test_contract_isolation()
test_consensus()
end()
