"""Martian surface: deposits, sols, dust storms, the base (imports depot + chip fabs) and a shared stockpile."""

from __future__ import annotations

import math
import random
from collections import Counter

from .bom import EVERYWHERE, FAB_KITS_REQUIRED, RAW

Pos = tuple[int, int]

SOL_TICKS = 10  # one Martian day (~2.5 h per tick)
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
        deposit_amount: int = 2000,
        seed_chips: int = 20,
        dust_storms: bool = True,
        fab_built: bool = False,
    ):
        self.rng = rng
        self.size = size
        self.depot: Pos = (size // 2, size // 2)
        self.imports: Counter = Counter({"chip": seed_chips})  # Earth-made items waiting at the base
        self.stockpile: Counter = Counter()  # Mars feedstock delivered by worker robots
        self.dust_storms = dust_storms
        self.storm_until = -1
        self.fab_kits = FAB_KITS_REQUIRED if fab_built else 0
        self.fab_tooling = 1 if fab_built else 0
        self.fab_busy: list[int] = []  # busy-until tick per fab
        self.tooling_waiting_until = -1  # a robot is waiting to make fab tooling: chips yield
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

    # ---- fabs ---------------------------------------------------------------

    @property
    def n_fabs(self) -> int:
        return min(self.fab_kits // FAB_KITS_REQUIRED, self.fab_tooling)

    @property
    def fab_built(self) -> bool:
        return self.n_fabs > 0

    @property
    def fab_shells_ready(self) -> int:
        """Fab structures built from kits that are still waiting for tooling."""
        return self.fab_kits // FAB_KITS_REQUIRED - self.fab_tooling

    def install_imported_tooling(self) -> int:
        n = 0
        while self.imports["fab_tooling"] > 0 and self.fab_shells_ready > 0:
            self.imports["fab_tooling"] -= 1
            self.fab_tooling += 1
            n += 1
        self.sync_fabs()
        return n

    def sync_fabs(self) -> None:
        while len(self.fab_busy) < self.n_fabs:
            self.fab_busy.append(-1)

    def free_fab(self, t: int) -> int | None:
        for i, until in enumerate(self.fab_busy):
            if until <= t:
                return i
        return None

    # ---- environment --------------------------------------------------------

    def storm(self, t: int) -> bool:
        return t < self.storm_until

    def solar_factor(self, t: int) -> float:
        """0..~3.1 insolation over a sol, mean 1.0 in clear weather."""
        phase = (t % SOL_TICKS) / SOL_TICKS
        sun = max(0.0, math.sin(2 * math.pi * phase)) * math.pi
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
        if self.dust_storms and not self.storm(t) and self.rng.random() < STORM_CHANCE:
            self.storm_until = t + self.rng.randint(*STORM_DURATION)
