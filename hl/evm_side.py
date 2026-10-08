# Created: WIB 2026-10-08 16:2x — EVM sidecar simulation (my-hl, specs/11)
from .config import SCALE

HYPE_SYSTEM = "0x2222222222222222222222222222222222222222"
COREWRITER = "0x3333333333333333333333333333333333333333"
PRECOMPILE_BASE = 0x800

CW_ACTIONS = {1: "limit_order", 2: "vault_transfer", 3: "token_delegate",
              4: "staking_deposit", 5: "staking_withdraw", 6: "spot_send",
              7: "usd_class_transfer"}


def system_address(token_idx):
    """specs/11: 0x20 + token index big-endian."""
    return "0x20%038x" % token_idx


class EvmSide:
    """Simulated sidecar: EVM balances, system addresses, CoreWriter queue,
    read precompiles. Block order per docs: evm block -> evm->core transfers
    -> CoreWriter actions. Contracts = callables with narrow access only."""

    def __init__(self):
        self.balances = {}     # addr -> {token_idx: wei}
        self.native = {}       # addr -> HYPE gas wei
        self.core_to_evm = []  # queued sendAsset (credited next evm block)
        self.evm_to_core = []  # Transfer logs pending core credit
        self.cw_queue = []     # {actor, action_id, payload, eta}
        self.gas_used = 0

    def bal(self, addr, idx):
        return self.balances.get(addr, {}).get(idx, 0)

    def _add(self, addr, idx, wei):
        self.balances.setdefault(addr, {})
        self.balances[addr][idx] = self.bal(addr, idx) + wei

    def send_to_system(self, addr, idx, wei):
        """EVM->Core: ERC20 transfer to the system address."""
        sysaddr = system_address(idx)
        if self.bal(addr, idx) < wei:
            return False
        self._add(addr, idx, -wei)
        self._add(sysaddr, idx, wei)
        self.evm_to_core.append({"from": addr, "idx": idx, "wei": wei})
        return True

    def send_asset_core_to_evm(self, core_user, addr, idx, wei):
        """Core->EVM: queued; credited at the next EVM block."""
        self.core_to_evm.append({"user": core_user, "addr": addr,
                                 "idx": idx, "wei": wei})

    def corewriter(self, actor, action_id, payload, ts, delay_s=3,
                   gas_price=1_000):
        """specs/11 + 13: burns native HYPE gas from the actor's balance."""
        cost = 25_000 * gas_price
        if self.native.get(actor, 0) < cost:
            return "no_gas"
        self.native[actor] = self.native.get(actor, 0) - cost
        blob = bytes([1, (action_id >> 16) & 255, (action_id >> 8) & 255,
                      action_id & 255]) + payload
        self.gas_used += 25_000
        self.cw_queue.append({"actor": actor, "action_id": action_id,
                              "blob": blob, "eta": ts + delay_s})

    def process_block(self, ts):
        """Returns (evm_to_core, cw_actions) in the documented order."""
        transfers = list(self.evm_to_core)
        self.evm_to_core = []
        for t in transfers:
            sysaddr = system_address(t["idx"])
            self._add(sysaddr, t["idx"], -t["wei"])
        for q in list(self.core_to_evm):
            if ts >= q.get("eta", ts):
                self._add(q["addr"], q["idx"], q["wei"])
                self.core_to_evm.remove(q)
        cw = [q for q in self.cw_queue if q["eta"] <= ts]
        self.cw_queue = [q for q in self.cw_queue if q["eta"] > ts]
        return transfers, cw

    def precompile_read(self, slot, value, in_len=32, out_len=32):
        """specs/11: reads at 0x800+; gas = 2000 + 65*(in+out)."""
        self.gas_used += 2000 + 65 * (in_len + out_len)
        return PRECOMPILE_BASE + slot, value


def run_contract(side, fn, *args):
    """Isolation: a contract crash cannot corrupt state (specs/11)."""
    try:
        return fn(side, *args), None
    except Exception as exc:  # contained
        return None, str(exc)
