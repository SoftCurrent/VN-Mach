"""Planet surface: deposits, days, sunlight, dust storms, the base (imports depot + stations) and a shared stockpile."""

from __future__ import annotations

import random
from collections import Counter

from .body import MARS, Body

Pos = tuple[int, int]

SOL_TICKS = 10  # one sol on Mars, one Earth day on the Moon (~2.4 h per tick)
STORM_CHANCE = 1 / 3000  # per tick
STORM_DURATION = (150, 600)


def dist(a: Pos, b: Pos) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class World:
    def __init__(
        self,
        rng: random.Random,
        body: Body = MARS,
        size: int = 48,
        deposits_per_type: int = 12,
        deposit_amount: int = 2000,
        seed_chips: int = 20,
        dust_storms: bool = True,
        fab_built: bool = False,
    ):
        self.rng = rng
        self.body = body
        self.size = size
        self.depot: Pos = (size // 2, size // 2)
        self.imports: Counter = Counter({"chip": seed_chips})  # Earth-made items waiting at the base
        self.stockpile: Counter = Counter()  # Mars feedstock delivered by worker robots
        self.dust_storms = dust_storms and body.dust_storms
        self.storm_until = -1
        self.kits_required = body.station.kits_required
        self.core = body.station.core
        self.fab_kits = self.kits_required if fab_built else 0
        self.fab_tooling = 1 if fab_built else 0
        self.fab_busy: list[int] = []  # busy-until tick per fab
        self.tooling_waiting_until = -1  # a robot is waiting to make fab tooling: chips yield
        self.sync_fabs()
        self.deposits: dict[str, dict[Pos, int]] = {r: {} for r in body.raw if r not in body.everywhere}
        for r in self.deposits:
            min_dist = body.deposit_min_dist.get(r, 0)
            for _ in range(max(1, deposits_per_type // 4)):
                cx, cy = rng.randrange(size), rng.randrange(size)
                while dist((cx, cy), self.depot) < min_dist:  # e.g. lunar ice sits in far shadowed craters
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
        return min(self.fab_kits // self.kits_required, self.fab_tooling)

    @property
    def fab_built(self) -> bool:
        return self.n_fabs > 0

    @property
    def fab_shells_ready(self) -> int:
        """Station structures built from kits that are still waiting for their core (tooling / anode)."""
        return self.fab_kits // self.kits_required - self.fab_tooling

    def install_imported_tooling(self) -> int:
        n = 0
        while self.imports[self.core] > 0 and self.fab_shells_ready > 0:
            self.imports[self.core] -= 1
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
        """Power per tick; mean 1.0 in clear conditions (see Body.solar_factor)."""
        return self.body.solar_factor(t, self.storm(t), SOL_TICKS)

    def nearest_deposit(self, pos: Pos, rtype: str) -> Pos | None:
        if rtype in self.body.everywhere:
            return pos
        cells = self.deposits[rtype]
        if not cells:
            return None
        return min(cells, key=lambda c: dist(pos, c))

    def mine(self, pos: Pos, rtype: str, amount: int = 1) -> int:
        if rtype in self.body.everywhere:
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

    def prospect(self, rtype: str, amount: int) -> Pos:
        """Discover a new vein. Easy finds are near the base; each later find is further out."""
        self.finds = getattr(self, "finds", Counter())
        self.finds[rtype] += 1
        radius = min(self.size // 2 - 3, max(6 + 3 * self.finds[rtype], self.body.deposit_min_dist.get(rtype, 0)))
        while True:
            cx = self.depot[0] + self.rng.randint(-radius, radius)
            cy = self.depot[1] + self.rng.randint(-radius, radius)
            if self.in_bounds((cx, cy)) and dist((cx, cy), self.depot) >= radius // 2:
                break
        for _ in range(4):
            p = (min(self.size - 1, max(0, cx + self.rng.randint(-2, 2))),
                 min(self.size - 1, max(0, cy + self.rng.randint(-2, 2))))
            self.deposits[rtype][p] = self.deposits[rtype].get(p, 0) + amount
        return (cx, cy)

    def in_bounds(self, p: Pos) -> bool:
        return 0 <= p[0] < self.size and 0 <= p[1] < self.size

    def remaining(self) -> dict[str, int]:
        return {r: sum(c.values()) for r, c in self.deposits.items()}

    def tick(self, t: int) -> None:
        if self.dust_storms and not self.storm(t) and self.rng.random() < STORM_CHANCE:
            self.storm_until = t + self.rng.randint(*STORM_DURATION)
