"""Mars bill of materials for a self-replicating robot, and closure analysis.

Feedstocks are what Mars actually offers in situ:
  hematite  - concentrated iron oxide deposits
  basalt    - the ubiquitous crust (silica + alumina), dug anywhere
  ice       - buried water ice (hydrogen for reduction, polymer feedstock)
  co2       - the atmosphere, harvestable anywhere at an energy cost

Copper is scarce on Mars, so windings and conductors are aluminium.

Closure = what fraction of the machine it can make from those. Anything it
can't is a "vitamin" that has to come from Earth. Chips are the hard one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

RAW = ("hematite", "basalt", "ice", "co2")
EVERYWHERE = ("basalt", "co2")  # regolith and atmosphere: harvestable at any cell


@dataclass(frozen=True)
class Recipe:
    output: str
    inputs: dict[str, int]
    energy: float
    ticks: int
    kind: str  # "refine" | "fabricate"
    precision: float = 0.0  # 0..1; higher precision -> more defect-prone
    mass: float = 1.0
    at_fab: bool = False  # only possible inside the chip fab at the base


ROBOT_BOM: dict[str, int] = {
    "frame": 1,
    "motor": 4,
    "wiring": 2,
    "solar_panel": 1,
    "battery": 1,
    "sensor": 1,
    "chip": 1,
}
ASSEMBLY_TICKS = 20
ASSEMBLY_ENERGY = 10.0

# The chip fab is built from kits that robots deliver to the base. Each kit
# contains an imported chip (the fab's own controllers): spend vitamins to
# build the machine that makes the vitamin.
FAB_KIT: dict[str, int] = {"steel": 4, "glass": 3, "aluminium": 3, "sensor": 2, "motor": 2, "chip": 1}
FAB_KITS_REQUIRED = 4

CHIP_RECIPE = Recipe("chip", {"silicon": 4, "aluminium": 1}, 30, 40, "fabricate", 0.9, mass=0.2, at_fab=True)


def default_recipes(chip_fab: bool = False) -> dict[str, Recipe]:
    r = [
        # refining (ISRU)
        Recipe("steel", {"hematite": 2, "ice": 1, "carbon": 1}, 6, 4, "refine"),  # H2 reduction + carburising
        Recipe("aluminium", {"basalt": 3}, 10, 5, "refine"),  # molten-salt electrolysis: energy hungry
        Recipe("silicon", {"basalt": 2}, 8, 4, "refine"),
        Recipe("glass", {"basalt": 1}, 3, 2, "refine"),
        Recipe("carbon", {"co2": 2}, 4, 3, "refine"),  # Sabatier / Bosch
        Recipe("polymer", {"carbon": 1, "ice": 1}, 4, 3, "refine"),
        # fabrication
        Recipe("frame", {"steel": 2, "aluminium": 2}, 4, 8, "fabricate", 0.1, mass=8.0),
        Recipe("motor", {"steel": 1, "aluminium": 1}, 3, 6, "fabricate", 0.4, mass=2.0),
        Recipe("wiring", {"aluminium": 1, "polymer": 1}, 1, 3, "fabricate", 0.2, mass=1.0),
        Recipe("solar_panel", {"silicon": 2, "glass": 1}, 5, 8, "fabricate", 0.5, mass=3.0),
        Recipe("battery", {"aluminium": 1, "polymer": 1, "steel": 1}, 3, 5, "fabricate", 0.3, mass=3.0),
        Recipe("sensor", {"silicon": 1, "glass": 1, "aluminium": 1}, 3, 6, "fabricate", 0.6, mass=0.5),
    ]
    if chip_fab:
        r.append(CHIP_RECIPE)
    return {x.output: x for x in r}


VITAMIN_MASS = {"chip": 0.2}


def defect_probability(rec: Recipe, base_rate: float) -> float:
    """Defect chance grows with the precision a part demands."""
    return min(0.95, base_rate * (1 + 9 * rec.precision))


@dataclass
class Expansion:
    raw: Counter = field(default_factory=Counter)
    vitamins: Counter = field(default_factory=Counter)
    energy: float = 0.0
    ticks: int = 0


def expand(item: str, qty: int, recipes: dict[str, Recipe], acc: Expansion | None = None) -> Expansion:
    """Recursively expand an item into raw materials, vitamins, energy and serial build time."""
    acc = acc or Expansion()
    if item in RAW:
        acc.raw[item] += qty
        return acc
    rec = recipes.get(item)
    if rec is None:
        acc.vitamins[item] += qty
        return acc
    acc.energy += rec.energy * qty
    acc.ticks += rec.ticks * qty
    for inp, q in rec.inputs.items():
        expand(inp, q * qty, recipes, acc)
    return acc


def expand_bom(bom: dict[str, int], recipes: dict[str, Recipe], energy=0.0, ticks=0) -> Expansion:
    acc = Expansion(energy=energy, ticks=ticks)
    for part, q in bom.items():
        expand(part, q, recipes, acc)
    return acc


def expand_robot(recipes: dict[str, Recipe]) -> Expansion:
    return expand_bom(ROBOT_BOM, recipes, ASSEMBLY_ENERGY, ASSEMBLY_TICKS)


@dataclass
class ClosureReport:
    parts_closure: float
    mass_closure: float
    vitamins: dict[str, int]
    raw_per_robot: dict[str, int]
    energy_per_robot: float
    serial_ticks_per_robot: int
    defect_free_probability: float


def part_mass(item: str, recipes: dict[str, Recipe]) -> float:
    if item in recipes:
        return recipes[item].mass
    return VITAMIN_MASS.get(item, 1.0)


def closure(recipes: dict[str, Recipe], defect_rate: float = 0.02) -> ClosureReport:
    total_parts = sum(ROBOT_BOM.values())
    local_parts = sum(q for p, q in ROBOT_BOM.items() if p in recipes)
    total_mass = sum(part_mass(p, recipes) * q for p, q in ROBOT_BOM.items())
    local_mass = sum(part_mass(p, recipes) * q for p, q in ROBOT_BOM.items() if p in recipes)
    exp = expand_robot(recipes)
    p_ok = 1.0
    for p, q in ROBOT_BOM.items():
        if p in recipes:
            p_ok *= (1 - defect_probability(recipes[p], defect_rate)) ** q
    return ClosureReport(
        parts_closure=local_parts / total_parts,
        mass_closure=local_mass / total_mass,
        vitamins=dict(exp.vitamins),
        raw_per_robot=dict(exp.raw),
        energy_per_robot=exp.energy,
        serial_ticks_per_robot=exp.ticks,
        defect_free_probability=p_ok,
    )
