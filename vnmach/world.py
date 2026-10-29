"""Martian surface: deposits, sols, dust storms, the base (depot + chip fab) and a shared stockpile."""

from __future__ import annotations

import math
import random
from collections import Counter

from .bom import EVERYWHERE, FAB_KITS_REQUIRED, RAW

Pos = tuple[int, int]

SOL_TICKS = 40  # one Martian day
STORM_CHANCE = 1 / 3000  # per tick
STORM_DURATION = (150, 600)
STORM_SOLAR = 0.25  # fraction of sunlight reaching the panels in a storm


def dist(a: Pos, b: Pos) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class World:
    def __init__(
        self,
        rng: random.Random,
        size: int = 48,
        deposits_per_type: int = 12,
        deposit_amount: int = 400,
        seed_vitamins: int = 20,
        vitamin_resupply_interval: int = 0,
        vitamin_resupply_amount: int = 0,
        dust_storms: bool = True,
        fab_built: bool = False,
    ):
        self.rng = rng
        self.size = size
        self.depot: Pos = (size // 2, size // 2)
        self.vitamins: Counter = Counter({"chip": seed_vitamins})
        self.stockpile: Counter = Counter()
        self.resupply_interval = vitamin_resupply_interval
        self.resupply_amount = vitamin_resupply_amount
        self.dust_storms = dust_storms
        self.storm_until = -1
        self.fab_kits = FAB_KITS_REQUIRED if fab_built else 0
        self.fab_busy: list[int] = []  # busy-until tick per fab
        self.sync_fabs()
        self.deposits: dict[str, dict[Pos, int]] = {r: {} for r in RAW if r not in EVERYWHERE}
        for r in self.deposits:
            for _ in range(max(1, deposits_per_type // 4)):
                cx, cy = rng.randrange(size), rng.randrange(size)
                for _ in range(4):
                    p = (
                        min(size - 1, max(0, cx + rng.randint(-2, 2))),
                        min(size - 1, max(0, cy + rng.randint(-2, 2))),
                    )
                    self.deposits[r][p] = self.deposits[r].get(p, 0) + deposit_amount

    @property
    def n_fabs(self) -> int:
        return self.fab_kits // FAB_KITS_REQUIRED

    @property
    def fab_built(self) -> bool:
        return self.n_fabs > 0

    def sync_fabs(self) -> None:
        while len(self.fab_busy) < self.n_fabs:
            self.fab_busy.append(-1)

    def free_fab(self, t: int) -> int | None:
        for i, until in enumerate(self.fab_busy):
            if until <= t:
                return i
        return None

    def storm(self, t: int) -> bool:
        return t < self.storm_until

    def solar_factor(self, t: int) -> float:
        """0..~1.6 insolation over a sol, mean ~1.0 in clear weather."""
        phase = (t % SOL_TICKS) / SOL_TICKS
        sun = max(0.0, math.sin(2 * math.pi * phase)) * math.pi  # mean over a sol = 1.0
        return sun * (STORM_SOLAR if self.storm(t) else 1.0)

    def nearest_deposit(self, pos: Pos, rtype: str) -> Pos | None:
        if rtype in EVERYWHERE:
            return pos
        cells = self.deposits[rtype]
        if not cells:
            return None
        return min(cells, key=lambda c: dist(pos, c))

    def mine(self, pos: Pos, rtype: str, amount: int = 1) -> int:
        if rtype in EVERYWHERE:
            return amount
        cells = self.deposits[rtype]
        have = cells.get(pos, 0)
        got = min(have, amount)
        if got:
            if have - got <= 0:
                del cells[pos]
            else:
                cells[pos] = have - got
        return got

    def remaining(self) -> dict[str, int]:
        return {r: sum(c.values()) for r, c in self.deposits.items()}

    def tick(self, t: int) -> None:
        if self.resupply_interval and t > 0 and t % self.resupply_interval == 0:
            self.vitamins["chip"] += self.resupply_amount
        if self.dust_storms and not self.storm(t) and self.rng.random() < STORM_CHANCE:
            self.storm_until = t + self.rng.randint(*STORM_DURATION)
