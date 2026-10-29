"""Robot state: inventory, energy, in-progress job, inherited genome and hidden defects."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field, replace

from .bom import Recipe
from .world import Pos

BATTERY_CAPACITY = 60.0
SOLAR_OUTPUT = 1.0
MOVE_COST = 0.3
MINE_COST = 0.5
CHILD_ENERGY = 15.0


@dataclass
class Genome:
    """The 'tape' copied into each child. Mutations model copy errors in the control program."""

    max_children: int = 3  # replication governor
    energy_reserve: float = 6.0  # don't start jobs below this
    inspect_parts: bool = True  # use the sensor to reject defective parts

    def mutate(self, rng: random.Random, governor_protected: bool) -> "Genome":
        fields = ["energy_reserve", "inspect_parts"]
        if not governor_protected:
            fields.append("max_children")
        f = rng.choice(fields)
        if f == "max_children":
            return replace(self, max_children=max(0, self.max_children + rng.choice((-1, 1, 2))))
        if f == "energy_reserve":
            return replace(self, energy_reserve=max(0.0, self.energy_reserve * rng.uniform(0.6, 1.4)))
        return replace(self, inspect_parts=not self.inspect_parts)


@dataclass
class Job:
    kind: str  # "refine" | "fabricate" | "assemble"
    output: str
    remaining: int
    recipe: Recipe | None = None
    child_defects: Counter = field(default_factory=Counter)


@dataclass
class Robot:
    id: int
    pos: Pos
    generation: int = 0
    parent: int | None = None
    genome: Genome = field(default_factory=Genome)
    energy: float = 30.0
    inventory: Counter = field(default_factory=Counter)
    hidden_defects: Counter = field(default_factory=Counter)  # defective items in inventory, not yet detected
    built_defects: Counter = field(default_factory=Counter)  # defective parts inside this robot's own body
    children: int = 0
    alive: bool = True
    job: Job | None = None
    role: str = "replicator"  # "replicator" | "worker"
    born: int = 0
    work_done: int = 0  # raw units delivered to the stockpile
    travel_to: Pos | None = None  # committed destination: walk without re-planning
    sleep_until: int = -1  # blocked: don't re-plan before this tick

    @property
    def dud(self) -> bool:
        """A defective control chip means the robot never boots."""
        return self.built_defects["chip"] > 0

    @property
    def efficiency(self) -> float:
        n = sum(v for k, v in self.built_defects.items() if k != "chip")
        return max(0.1, 1.0 - 0.15 * n)

    @property
    def solar_output(self) -> float:
        return SOLAR_OUTPUT * (0.4 if self.built_defects["solar_panel"] else 1.0)

    @property
    def can_inspect(self) -> bool:
        return self.genome.inspect_parts and not self.built_defects["sensor"]

    def take_parts(self, item: str, qty: int, rng: random.Random) -> int:
        """Remove qty of item from inventory; returns how many of those were (hidden) defective."""
        bad = 0
        for _ in range(qty):
            total = self.inventory[item]
            if self.hidden_defects[item] and rng.random() < self.hidden_defects[item] / total:
                self.hidden_defects[item] -= 1
                bad += 1
            self.inventory[item] -= 1
        return bad
