# Created: WIB 2026-10-08 16:6x — staking + delegation (my-hl, ref section 7.2)
from .num import qdiv
from .config import SCALE

SELF_DELEGATE_MIN = 10_000 * SCALE   # validators self-delegate 10k HYPE
APR_NUM = 22                          # ~2.2% APR (ref 6.4, order-of-magnitude)
APR_DEN = 1000


class Staking:
    """Delegation to validators. Jailing excludes from rewards (no slashing).
    Commission is the validator's cut of rewards."""

    def __init__(self):
        self.validators = {}   # name -> {self_stake, commission_pct, jailed}
        self.delegations = {}  # user -> {validator: wei}
        self.rewards = {}      # user -> wei accrued
        self.last_ts = 0

    def register(self, name, self_stake, commission_pct):
        if self_stake < SELF_DELEGATE_MIN:
            return False
        self.validators[name] = {"self_stake": self_stake,
                                 "commission_pct": commission_pct,
                                 "jailed": False}
        return True

    def total_stake(self, name):
        v = self.validators.get(name)
        if v is None:
            return 0
        tot = v["self_stake"]
        for user, dels in self.delegations.items():
            tot += dels.get(name, 0)
        return tot

    def delegate(self, user, name, wei):
        if name not in self.validators:
            return False
        self.delegations.setdefault(user, {})
        self.delegations[user][name] = self.delegations[user].get(name, 0) + wei
        return True

    def accrue(self, ts):
        """Reward stream: APR on total stake; jailed validators earn nothing.
        Commission goes to the validator's own rewards."""
        dt = ts - self.last_ts
        if dt <= 0:
            return
        self.last_ts = ts
        year = 365 * 24 * 3600
        for name, v in self.validators.items():
            if v["jailed"]:
                continue
            tot = self.total_stake(name)
            pool = qdiv(tot * APR_NUM * dt, APR_DEN * year)
            comm = qdiv(pool * v["commission_pct"], 100)
            self.rewards[name] = self.rewards.get(name, 0) + comm
            rest = pool - comm
            for user, dels in self.delegations.items():
                d = dels.get(name, 0)
                if d and tot:
                    self.rewards[user] = self.rewards.get(user, 0) \
                        + qdiv(rest * d, tot)

    def jail(self, name):
        if name in self.validators:
            self.validators[name]["jailed"] = True

    def unjail(self, name):
        if name in self.validators:
            self.validators[name]["jailed"] = False
