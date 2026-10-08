# Created: WIB 2026-10-08 16:2x — validator-signed bridge sim (my-hl, specs/11)
from .config import SCALE

WITHDRAW_FEE = 1 * SCALE  # 1 USDC (ref 5.3)


class Bridge:
    """Validator-signed bridge: deposits credit at >2/3 stake signatures;
    withdrawals debit now, release after 2/3 signatures + dispute window."""

    def __init__(self, total_stake):
        self.total_stake = total_stake
        self.deposit_sigs = {}   # (user, nonce) -> {validator: stake}
        self.deposits = {}       # (user, nonce) -> usd
        self.nonce = 0
        self.withdrawals = []    # {user, usd, sigs, ts, locked}

    def deposit_sig(self, user, usd, validator, stake):
        k = (user, self.nonce)
        self.deposits[k] = self.deposits.get(k, 0) + 0  # ensure key
        self.deposits[k] = usd
        self.deposit_sigs.setdefault(k, {})[validator] = stake
        if sum(self.deposit_sigs[k].values()) * 3 > self.total_stake * 2:
            self.nonce += 1
            return usd  # credited
        return 0

    def request_withdrawal(self, user, usd, ts):
        """Debit immediately (caller clears); fee taken; release later."""
        fee = WITHDRAW_FEE
        if usd <= fee:
            return None
        w = {"user": user, "usd": usd - fee, "fee": fee, "sigs": {},
             "ts": ts, "locked": False, "released": False}
        self.withdrawals.append(w)
        return w

    def sign_withdrawal(self, idx, validator, stake, ts, dispute_s=3600):
        w = self.withdrawals[idx]
        if w["locked"] or w["released"]:
            return "locked" if w["locked"] else "done"
        w["sigs"][validator] = stake
        if sum(w["sigs"].values()) * 3 > self.total_stake * 2:
            if ts - w["ts"] >= dispute_s:
                w["released"] = True
                return "released"
            return "pending_dispute"
        return "signed"

    def dispute(self, idx):
        """Challenger locks a pending release (ref 5.3 dispute window)."""
        w = self.withdrawals[idx]
        if not w["released"]:
            w["locked"] = True
            return True
        return False
