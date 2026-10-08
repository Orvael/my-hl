# Created: WIB 2026-10-08 16:1x — HIP-3 builder-deployed perps (my-hl)
from .config import SCALE
from .num import qdiv

DEPLOY_STAKE = 500_000 * SCALE  # 500k HYPE-count units (specs/10)
MIN_LOCK_S = 183 * 24 * 3600
AUCTION_FLOOR = 500 * SCALE
AUCTION_WINDOW_S = 31 * 3600
DEX_FREE_ASSETS = 3


class Hip3Store:
    def __init__(self):
        self.dexes = {}   # name -> dex dict
        self.stakes = {}  # deployer -> staked amount
        self.auction = None
        self.n_auction_deployments = 0

    def stake(self, user, amt):
        self.stakes[user] = self.stakes.get(user, 0) + amt
        return True

    def unstake_check(self, user, amt, now_ts):
        """specs/10: the 500k requirement is locked 183d after dex deploy."""
        locked = 0
        for d in self.dexes.values():
            if d["deployer"] == user:
                if now_ts - d["deploy_ts"] < MIN_LOCK_S:
                    locked += DEPLOY_STAKE
        free = self.stakes.get(user, 0) - locked
        return amt <= free

    def deploy_dex(self, user, name, ts):
        if self.stakes.get(user, 0) < DEPLOY_STAKE:
            return False, "stake"
        if any(d["deployer"] == user for d in self.dexes.values()):
            return False, "one_dex"
        if name in self.dexes:
            return False, "name"
        self.dexes[name] = {"deployer": user, "staked": DEPLOY_STAKE,
                            "deploy_ts": ts, "fee_share_bps": 0,
                            "assets": [], "halted": {}}
        return True, "ok"

    def start_auction(self, asset, ts, last_px=None):
        """specs/10: shared Dutch auction, initial 2x last price (or floor),
        decays linearly to 500 HYPE over 31h."""
        start = 2 * last_px if last_px else 10 * AUCTION_FLOOR
        self.auction = {"asset": asset, "start_px": start, "start_ts": ts}

    def auction_px(self, ts):
        if not self.auction:
            return None
        el = ts - self.auction["start_ts"]
        frac = min(el, AUCTION_WINDOW_S)
        span = self.auction["start_px"] - AUCTION_FLOOR
        return self.auction["start_px"] - qdiv(span * frac, AUCTION_WINDOW_S)

    def list_asset(self, user, dex_name, coin, ts):
        d = self.dexes.get(dex_name)
        if d is None or d["deployer"] != user:
            return False, "no_dex"
        if len(d["assets"]) < DEX_FREE_ASSETS:
            d["assets"].append(coin)
            return True, "free"
        if not self.auction or self.auction.get("winner") != user:
            return False, "auction"
        d["assets"].append(coin)
        self.n_auction_deployments += 1
        self.auction = None
        return True, "auction"

    def set_fee_share(self, user, dex_name, bps):
        d = self.dexes.get(dex_name)
        if d is None or d["deployer"] != user:
            return False
        if bps < d["fee_share_bps"] or bps > 30_000:
            return False
        d["fee_share_bps"] = bps
        return True

    def taker_fee_for(self, dex_name):
        """specs/10: share >100% raises the protocol fee to match."""
        d = self.dexes.get(dex_name)
        if d is None:
            return None
        base = 45_000
        share = d["fee_share_bps"]
        if share > 10_000:
            return base * share // 10_000
        return base

    def fee_split(self, dex_name, fee):
        d = self.dexes.get(dex_name)
        if d is None:
            return fee, 0
        to_dep = qdiv(fee * d["fee_share_bps"], 30_000)
        return fee - to_dep, to_dep

    def halt(self, user, dex_name, coin):
        d = self.dexes.get(dex_name)
        if d is None or d["deployer"] != user:
            return False
        d["halted"][coin] = True
        return True

    def reserves_x10(self):
        """specs/10: 7 + 0.2 * n_auction_deployments, scaled by 10."""
        return 70 + 2 * self.n_auction_deployments

    def use_reserve(self, user, dex_name, coin, ts):
        """Reserve deployment: current auction px, bypasses the timer."""
        d = self.dexes.get(dex_name)
        if d is None or d["deployer"] != user:
            return False, "no_dex"
        if self.reserves_x10() < 10:
            return False, "no_reserves"
        if not self.auction:
            return False, "no_auction"
        px = self.auction_px(ts)
        self.auction = None
        self.n_auction_deployments += 1
        d["assets"].append(coin)
        return True, px

    def resume(self, user, dex_name, coin):
        d = self.dexes.get(dex_name)
        if d is None or d["deployer"] != user:
            return False
        d["halted"][coin] = False
        return True