"""Planetary bodies: everything that differs between a Mars colony and a Moon colony.

A Body bundles the feedstocks, recipes, robot design, the colony's bottleneck
station, sunlight, wear, Earth logistics and the local market. The rest of the
simulator reads these instead of hard-coding Mars.

The "station" is the shared facility robots build from kits plus one core
item, and that runs station-only recipes:
  Mars: chip fab  = 4 kits + fab tooling (importable, later made in a fab); makes chips
  Moon: MRE cell  = 3 kits + an anode (imported only); turns regolith into metal and oxygen
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import bom
from .bom import Recipe


@dataclass(frozen=True)
class Station:
    name: str
    kit: dict[str, int]
    kits_required: int
    core: str  # item that completes a station: imported, or made by core_recipe
    core_recipe: Recipe | None  # local manufacture of the core inside an existing station
    recipes: tuple[Recipe, ...]  # jobs that can only run inside a station
    import_first: frozenset[str] = frozenset()  # station outputs taken from imports while any are available
    makes_vitamin: bool = False  # does the station produce the robot's vitamin (chips)?


@dataclass(frozen=True)
class Body:
    name: str
    day_name: str  # "sol" or "day"; 10 ticks = 1 of these
    raw: tuple[str, ...]
    everywhere: tuple[str, ...]  # feedstocks harvestable on any cell
    deposit_min_dist: dict[str, int]  # finite deposits: minimum distance from the base
    recipes: dict[str, Recipe]  # recipes any robot can run anywhere
    robot_bom: dict[str, int]
    assembly_ticks: int
    assembly_energy: float
    fatal_parts: tuple[str, ...]
    vitamin: str  # the part robots can't make without a station (or at all)
    vitamin_mass: float  # in inventory units, for closure by mass
    station: Station
    station_prebuilt: bool  # does the first landing bring a working station?
    station_item: str  # catalog name for a complete station shipped from Earth
    sunlight: str  # "sol" (day/night sine) or "polar" (near-constant, monthly eclipses)
    dust_storms: bool
    mtbf: float  # mean ticks between component failures per robot
    mine_units_per_tick: int  # one dig action; Mars unit 100 kg (1 t/sol), Moon unit 6 kg
    robots_per_station: int  # heuristic directors add a station per this many robots
    conjunction_days: int  # comms blackout per window for an Earth-hosted director
    earth_latency_ticks: int
    kg_per_unit: float
    earth_prices: dict[str, float]  # catalog items priced by bom mass
    special_items: dict[str, tuple[float, float]]  # name -> (price, mass_kg)
    contract_types: dict[str, tuple[str, dict[str, int], int]]
    econ_defaults: dict = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    def all_recipes(self) -> dict[str, Recipe]:
        """Every recipe that can ever exist here (base + station + core), for masses and closure."""
        r = dict(self.recipes)
        for rec in self.station.recipes:
            r[rec.output] = rec
        if self.station.core_recipe:
            r[self.station.core] = self.station.core_recipe
        return r

    def label(self, item: str) -> str:
        return self.labels.get(item, item.replace("_", " "))

    def solar_factor(self, t: int, storm: bool, day_ticks: int) -> float:
        """Power available per tick, mean ~1.0 over a day (or lunar month) in clear conditions."""
        if self.sunlight == "polar":
            # A south-pole ridge: sunlit ~85% of each 29.5-day lunar cycle, dark when terrain shadows it.
            cycle = round(29.5 * day_ticks)
            lit = (t % cycle) >= round(0.15 * cycle)
            return POLAR_LIT_OUTPUT if lit else 0.0
        phase = (t % day_ticks) / day_ticks
        sun = max(0.0, math.sin(2 * math.pi * phase)) * math.pi
        return sun * (STORM_SOLAR if storm else 1.0)


STORM_SOLAR = 0.25
POLAR_LIT_OUTPUT = 1.0 / 0.85  # keeps the monthly mean at 1.0

# ---- Mars -------------------------------------------------------------------

MARS = Body(
    name="mars",
    day_name="sol",
    raw=bom.RAW,
    everywhere=bom.EVERYWHERE,
    deposit_min_dist={"hematite": 0, "ice": 0},
    recipes=bom.default_recipes(chip_fab=False),
    robot_bom=bom.ROBOT_BOM,
    assembly_ticks=bom.ASSEMBLY_TICKS,
    assembly_energy=bom.ASSEMBLY_ENERGY,
    fatal_parts=("chip", "frame"),
    vitamin="chip",
    vitamin_mass=0.2,
    station=Station(
        name="chip fab",
        kit=bom.FAB_KIT,
        kits_required=bom.FAB_KITS_REQUIRED,
        core="fab_tooling",
        core_recipe=bom.TOOLING_RECIPE,
        recipes=(bom.CHIP_RECIPE,),
        import_first=frozenset({"chip"}),
        makes_vitamin=True,
    ),
    station_prebuilt=False,
    station_item="chip_fab",
    sunlight="sol",
    dust_storms=True,
    mtbf=4000.0,
    mine_units_per_tick=1,
    robots_per_station=25,
    conjunction_days=14,
    earth_latency_ticks=1,
    kg_per_unit=100.0,
    earth_prices={
        "chip": 100_000, "motor": 20_000, "sensor": 30_000, "solar_panel": 15_000, "battery": 25_000,
        "wiring": 2_000, "frame": 40_000, "robot": 2_000_000,
    },
    special_items={"fab_tooling": (40_000_000, 8_000), "chip_fab": (60_000_000, 15_000)},
    contract_types={
        "landing_pad": ("launch provider", {"glass": 60, "steel": 20}, 300),
        "habitat_shell": ("space agency", {"steel": 30, "aluminium": 25, "glass": 15}, 400),
        "propellant": ("return-flight operator", {"propellant": 120}, 250),
        "solar_farm": ("research station", {"solar_panel": 20, "wiring": 15}, 350),
        "ice_delivery": ("life-support contractor", {"ice": 200}, 150),
    },
    econ_defaults=dict(
        starting_cash=600_000_000, launch_cost_per_kg=5_000, launch_cost_decline=0.15, launch_cost_floor=500,
        shipment_fixed_cost=10_000_000, window_period_sols=780, transit_sols=200, ops_cost_per_sol=50_000,
        mars_price_ratio=0.5, contract_offer_every_sols=20, demand_growth_per_window=4.0,
    ),
    labels={"chip": "chip", "fab_tooling": "fab tooling", "chip_fab": "complete chip fab"},
)

# ---- Moon -------------------------------------------------------------------
# A south-pole ridge. Highland regolith (anorthosite: Ca-Al silicate) everywhere;
# water ice in permanently shadowed craters further out. No atmosphere, so no
# CO2: no carbon, no polymers. Metal comes from molten regolith electrolysis
# (MRE), which also releases oxygen. See docs/LUNAR_REPLICATOR.md.
# One inventory unit is 6 kg, so the robot below is ~300 kg.

_R = Recipe
MOON_RECIPES = {r.output: r for r in [
    # magnetic separation of native iron fines (plus iron from regolith processing), anywhere
    _R("iron", {"regolith": 12}, 3, 4, "refine"),
    # solar-furnace melt cast into glass / fibre
    _R("glass", {"regolith": 2}, 4, 2, "refine"),
    # parts: coarse tolerances, cast in regolith-sand moulds, wound, lapped
    _R("frame", {"iron": 4, "fesi": 4}, 6, 20, "fabricate", 0.1, mass=8.0),
    _R("wheel", {"iron": 1}, 2, 6, "fabricate", 0.1, mass=1.0),  # woven iron-wire mesh
    _R("motor", {"fesi": 1, "aluminium": 1, "glass": 1}, 5, 10, "fabricate", 0.5, mass=2.0),  # switched reluctance
    _R("reducer", {"iron": 2}, 4, 14, "fabricate", 0.6, mass=2.0),  # cast cycloidal, lapped to fit
    _R("arm", {"fesi": 2, "aluminium": 1}, 4, 10, "fabricate", 0.3, mass=3.0),
    _R("harness", {"aluminium": 1, "glass": 1}, 2, 5, "fabricate", 0.2, mass=2.0),  # Al cable, glass insulation
]}
MRE_RECIPES = (
    _R("fesi", {"regolith": 4}, 20, 6, "refine", at_fab=True, byproducts={"oxygen": 2}),
    _R("aluminium", {"regolith": 5}, 24, 7, "refine", at_fab=True, byproducts={"oxygen": 2}),
    _R("oxygen", {"regolith": 3}, 14, 4, "refine", at_fab=True, byproducts={"oxygen": 1}),
)

MOON = Body(
    name="moon",
    day_name="day",
    raw=("regolith", "ice"),
    everywhere=("regolith",),
    deposit_min_dist={"ice": 14},
    recipes=MOON_RECIPES,
    robot_bom={"frame": 1, "wheel": 4, "motor": 8, "reducer": 8, "arm": 1, "harness": 1, "chip": 1},
    assembly_ticks=30,
    assembly_energy=10.0,
    fatal_parts=("chip", "frame"),
    vitamin="chip",
    vitamin_mass=1.5,
    station=Station(
        name="MRE cell",
        kit={"iron": 10, "fesi": 4, "glass": 6, "motor": 2, "harness": 1, "chip": 1},
        kits_required=3,
        core="anode",
        core_recipe=None,  # inert anodes can't be made locally yet
        recipes=MRE_RECIPES,
    ),
    station_prebuilt=True,  # generation 0 lands with one working MRE cell
    station_item="mre_cell",
    sunlight="polar",
    dust_storms=False,
    mtbf=3000.0,  # abrasive, charged dust wears joints faster
    mine_units_per_tick=30,  # 180 kg per tick, ~1.8 t/day for a 300 kg excavating robot
    robots_per_station=6,  # metal from MRE is the bottleneck, so stations scale fast
    conjunction_days=0,  # Earth is always in view (via relay); 1.3 s light delay
    earth_latency_ticks=0,
    kg_per_unit=6.0,
    earth_prices={
        "chip": 100_000, "motor": 20_000, "reducer": 15_000, "wheel": 5_000, "arm": 60_000,
        "harness": 5_000, "frame": 40_000, "robot": 3_000_000,
    },
    special_items={"anode": (2_000_000, 50), "mre_cell": (25_000_000, 2_000)},
    contract_types={
        "oxygen_delivery": ("lander operator", {"oxygen": 150}, 120),
        "landing_pad": ("launch provider", {"glass": 120, "regolith": 200}, 200),
        "shielding_berm": ("habitat operator", {"regolith": 500}, 150),
        "ice_delivery": ("propellant depot", {"ice": 200}, 150),
    },
    econ_defaults=dict(
        starting_cash=300_000_000, launch_cost_per_kg=20_000, launch_cost_decline=0.03, launch_cost_floor=1_000,
        shipment_fixed_cost=5_000_000, window_period_sols=30, transit_sols=5, ops_cost_per_sol=30_000,
        mars_price_ratio=0.5, contract_offer_every_sols=15, demand_growth_per_window=0.15,
    ),
    labels={"chip": "avionics module", "fesi": "iron-silicon", "mre_cell": "complete MRE cell"},
)

BODIES = {"mars": MARS, "moon": MOON}
