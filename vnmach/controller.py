"""Low-level robot controller: a scripted skill executor.

On real hardware this layer is motion planning, grasping and machine-tool
control, running every tick. A slower high-level planner (director.py, which
can be Claude) only sets the colony policy this layer reads.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from .bom import ASSEMBLY_ENERGY, CHIP_RECIPE, FAB_KIT, RAW, ROBOT_BOM, Recipe
from .robot import Robot
from .world import Pos, dist

if TYPE_CHECKING:
    from .sim import Simulation

Action = tuple

WORKER_CARGO = 10


def step_toward(a: Pos, b: Pos) -> Pos:
    if a[0] != b[0]:
        return (a[0] + (1 if b[0] > a[0] else -1), a[1])
    if a[1] != b[1]:
        return (a[0], a[1] + (1 if b[1] > a[1] else -1))
    return a


def topo_order(recipes: dict[str, Recipe]) -> list[str]:
    """Items ordered so every item comes before its inputs (for MRP netting)."""
    seen, out = set(), []

    def visit(item):
        if item in seen or item not in recipes:
            return
        seen.add(item)
        for i in recipes[item].inputs:
            visit(i)
        out.append(item)

    for item in recipes:
        visit(item)
    return out[::-1]


class ScriptedController:
    def __init__(self, recipes: dict[str, Recipe]):
        self.base_recipes = recipes
        self.with_chip = {**recipes, "chip": CHIP_RECIPE}
        self.orders = {id(recipes): topo_order(recipes), id(self.with_chip): topo_order(self.with_chip)}

    def recipes_for(self, sim: "Simulation", building_fab: bool) -> dict[str, Recipe]:
        """Fabricate chips only when the base has a fab and no imported chip is available to us."""
        w = sim.world
        if "chip" not in self.base_recipes and w.fab_built and self.chips_available(sim, building_fab) <= 0:
            return self.with_chip
        return self.base_recipes

    def chips_available(self, sim: "Simulation", building_fab: bool) -> int:
        return sim.world.vitamins["chip"] - (0 if building_fab else sim.policy.get("chip_reserve", 0))

    def is_fab_builder(self, robot: Robot, sim: "Simulation") -> bool:
        p = sim.policy
        want_more = sim.world.n_fabs < p.get("target_fabs", 1)
        return bool(p.get("build_fab")) and want_more and (robot.id * 7919 % 100) < 100 * p.get("fab_share", 0.5)

    def net_requirements(self, robot: Robot, target: dict[str, int], recipes: dict[str, Recipe]) -> Counter:
        """MRP-style netting: what is still missing to complete `target`."""
        inv = robot.inventory
        req = Counter(target)
        net = Counter()
        order = self.orders.get(id(recipes)) or topo_order(recipes)
        for item in order:
            n = max(0, req[item] - inv[item])
            if n:
                net[item] = n
                for i, q in recipes[item].inputs.items():
                    req[i] += n * q
        for item, q in req.items():
            if item not in recipes:  # raw or vitamin
                n = max(0, q - inv[item])
                if n:
                    net[item] = n
        return net

    def decide(self, robot: Robot, sim: "Simulation") -> Action:
        broken = [p for p, n in robot.built_defects.items() if n and p != "chip"]
        if broken and sim.config.self_repair:
            part = broken[0]
            if robot.inventory[part] > 0:
                return ("repair", part)
            return self.work_toward(robot, sim, {part: 1}, building_fab=False)
        if not sim.can_replicate(robot):
            return self.decide_worker(robot, sim)
        building_fab = self.is_fab_builder(robot, sim)
        return self.work_toward(robot, sim, FAB_KIT if building_fab else ROBOT_BOM, building_fab)

    def work_toward(self, robot: Robot, sim: "Simulation", target: dict[str, int], building_fab: bool) -> Action:
        """Next action to complete `target`: assemble/deliver, fabricate, refine, fetch chips, or mine."""
        world, inv, reserve = sim.world, robot.inventory, robot.genome.energy_reserve
        recipes = self.recipes_for(sim, building_fab)
        net = self.net_requirements(robot, target, recipes)

        if not any(net[p] for p in target):
            if target is not ROBOT_BOM and not building_fab:
                return ("wait",)  # repair target met; decide() issues the swap next tick
            if building_fab:
                return ("deliver_kit",) if robot.pos == world.depot else ("move", world.depot)
            return ("assemble",) if robot.energy >= ASSEMBLY_ENERGY + reserve else ("wait",)

        for item in self.orders[id(recipes)]:
            rec = recipes[item]
            if net[item] and all(inv[i] >= q for i, q in rec.inputs.items()):
                if rec.at_fab:
                    if robot.pos != world.depot:
                        return ("move", world.depot)
                    if world.free_fab(sim.t) is None:
                        continue
                return ("start", rec) if robot.energy >= rec.energy + reserve else ("wait",)

        for item in target:
            if net[item] and item not in recipes and item not in RAW and self.chips_available(sim, building_fab) > 0:
                return ("take_vitamin", item) if robot.pos == world.depot else ("move", world.depot)

        source = self.nearest_raw_source(robot, [r for r in RAW if net[r]], sim)
        if source:
            pos, rtype, from_stock = source
            if robot.pos == pos:
                return ("take_stock", rtype, net[rtype]) if from_stock else ("mine", rtype)
            return ("move", pos)
        if any(net[r] for r in RAW):
            sim.stats["starved_feedstock"] += 1  # needed deposits are exhausted
        else:
            sim.stats["waiting_on_chips"] += 1  # only chips missing: none available and fabs busy/absent
        return ("wait",)

    def nearest_raw_source(self, robot: Robot, wanted: list[str], sim: "Simulation"):
        world, best = sim.world, None
        for r in wanted:
            options = []
            dep = world.nearest_deposit(robot.pos, r)
            if dep is not None:
                # harvesting the atmosphere is slow; treat it as a few cells of travel
                options.append((dist(robot.pos, dep) + (3 if dep == robot.pos and r == "co2" else 0), dep, r, False))
            if world.stockpile[r] > 0:
                options.append((dist(robot.pos, world.depot), world.depot, r, True))
            for o in options:
                if best is None or o[0] < best[0]:
                    best = o
        return best[1:] if best else None

    def decide_worker(self, robot: Robot, sim: "Simulation") -> Action:
        """Non-replicating robots do useful work: harvest feedstock into the base stockpile."""
        world = sim.world
        cargo = sum(robot.inventory[r] for r in RAW)
        if cargo >= WORKER_CARGO:
            return ("deliver",) if robot.pos == world.depot else ("move", world.depot)
        mineable = [r for r in RAW if world.nearest_deposit(robot.pos, r) is not None]
        if not mineable:
            return ("deliver",) if cargo and robot.pos == world.depot else ("wait",)
        focus = sim.policy.get("harvest_focus")
        if focus not in mineable:
            focus = min(mineable, key=lambda r: world.stockpile[r])
        dep = world.nearest_deposit(robot.pos, focus)
        return ("mine", focus) if robot.pos == dep else ("move", dep)
