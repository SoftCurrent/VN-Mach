"""Low-level robot controller: a scripted skill executor.

On real hardware this layer is motion planning, grasping and machine-tool
control, running every tick. A slower high-level planner (director.py, which
can be Claude) only sets the colony policy this layer reads.

Each tick a robot takes one role, in priority order:
  repair      - fix its own broken parts
  fab builder - build fab kits / fab tooling (policy.fab_share)
  contractor  - make and deliver goods for paying contracts (policy.contract_share)
  replicator  - build a child
  worker      - (can't replicate) contracts if any are active, else harvest feedstock
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from .bom import ASSEMBLY_ENERGY, CHIP_RECIPE, EVERYWHERE, FAB_KIT, FAB_KITS_REQUIRED, RAW, ROBOT_BOM, TOOLING_RECIPE, Recipe
from .robot import Robot
from .world import Pos, dist

if TYPE_CHECKING:
    from .sim import Simulation

Action = tuple

WORKER_CARGO = 10
CONTRACT_CHUNK = 10
TOOLING_TARGET = {"fab_tooling": 1}


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


def bucket(robot_id: int, salt: int) -> float:
    """Stable pseudo-random value in [0, 1) per robot, for share-based role assignment."""
    return ((robot_id * 7919 + salt * 104729) % 1000) / 1000


class ScriptedController:
    def __init__(self, recipes: dict[str, Recipe]):
        self.base_recipes = recipes
        self.variants: dict[tuple[bool, bool], tuple[dict, list]] = {}
        for chip in (False, True):
            for tooling in (False, True):
                r = dict(recipes)
                if chip:
                    r["chip"] = CHIP_RECIPE
                if tooling:
                    r["fab_tooling"] = TOOLING_RECIPE
                self.variants[(chip, tooling)] = (r, topo_order(r))

    # ---- helpers ------------------------------------------------------------

    def import_available(self, sim: "Simulation", item: str, building_fab: bool) -> int:
        n = sim.world.imports[item]
        if item == "chip" and not building_fab:
            n -= sim.policy.get("chip_reserve", 0)
        return n

    def recipes_for(self, sim: "Simulation", building_fab: bool) -> tuple[dict, list]:
        """Fabricate chips only when a fab exists and no imported chip is available to us."""
        fab = sim.world.fab_built
        chip = fab and self.import_available(sim, "chip", building_fab) <= 0
        return self.variants[(chip, fab)]

    def net_requirements(self, robot: Robot, target: dict[str, int], recipes: dict, order: list) -> Counter:
        """MRP-style netting: what is still missing to complete `target`."""
        inv = robot.inventory
        req = Counter(target)
        net = Counter()
        for item in order:
            n = max(0, req[item] - inv[item])
            if n:
                net[item] = n
                for i, q in recipes[item].inputs.items():
                    req[i] += n * q
        for item, q in req.items():
            if item not in recipes:  # raw or imported-only
                n = max(0, q - inv[item])
                if n:
                    net[item] = n
        return net

    # ---- roles --------------------------------------------------------------

    def fab_task(self, robot: Robot, sim: "Simulation") -> dict[str, int] | None:
        p, w = sim.policy, sim.world
        target_fabs = p.get("target_fabs", 1)
        if not p.get("build_fab") or w.n_fabs >= target_fabs:
            return None
        if bucket(robot.id, 1) >= p.get("fab_share", 0.5):
            return None
        if w.fab_kits < target_fabs * FAB_KITS_REQUIRED:
            return FAB_KIT
        if w.fab_shells_ready > w.imports["fab_tooling"] and w.fab_built and p.get("make_tooling", True):
            return TOOLING_TARGET
        return None

    def contract_task(self, robot: Robot, sim: "Simulation", forced: bool):
        active = sim.econ.active_contracts()
        if not active or (not forced and bucket(robot.id, 2) >= sim.policy.get("contract_share", 0.0)):
            return None
        # Keep working on a contract we already carry goods for.
        for c in active:
            for item in c.remaining:
                if robot.inventory[item] > 0:
                    return c, item
        c = active[robot.id % len(active)]
        items = sorted(c.remaining)
        return c, items[(robot.id // len(active)) % len(items)]

    def decide(self, robot: Robot, sim: "Simulation") -> Action:
        broken = [p for p, n in robot.built_defects.items() if n and p != "chip"]
        if broken and sim.config.self_repair:
            part = broken[0]
            if robot.inventory[part] > 0:
                return ("repair", part)
            return self.work_toward(robot, sim, {part: 1}, building_fab=False)

        fab = self.fab_task(robot, sim)
        if fab is not None:
            return self.work_toward(robot, sim, fab, building_fab=True)

        can_rep = sim.can_replicate(robot)
        job = self.contract_task(robot, sim, forced=not can_rep)
        if job is not None:
            c, item = job
            want = min(CONTRACT_CHUNK, c.remaining[item])
            if robot.inventory[item] >= want:
                return ("deliver_contract", c.id, item) if robot.pos == c.site else ("move", c.site)
            return self.work_toward(robot, sim, {item: want}, building_fab=False)

        if can_rep:
            return self.work_toward(robot, sim, ROBOT_BOM, building_fab=False)
        return self.decide_worker(robot, sim)

    def work_toward(self, robot: Robot, sim: "Simulation", target: dict[str, int], building_fab: bool) -> Action:
        """Next action to complete `target`: finish, use imports, fabricate, refine, or mine."""
        world, inv, reserve = sim.world, robot.inventory, robot.genome.energy_reserve
        recipes, order = self.recipes_for(sim, building_fab)
        net = self.net_requirements(robot, target, recipes, order)

        if not any(net[p] for p in target):
            if target is ROBOT_BOM:
                return ("assemble",) if robot.energy >= ASSEMBLY_ENERGY + reserve else ("wait",)
            if target is FAB_KIT:
                return ("deliver_kit",) if robot.pos == world.depot else ("move", world.depot)
            if target is TOOLING_TARGET:
                return ("deliver_tooling",) if robot.pos == world.depot else ("move", world.depot)
            return ("wait",)  # repair / contract targets are completed by decide()

        for item in target:
            if net[item] and self.import_available(sim, item, building_fab) > 0:
                return ("take_import", item) if robot.pos == world.depot else ("move", world.depot)

        for item in order:
            rec = recipes[item]
            if net[item] and all(inv[i] >= q for i, q in rec.inputs.items()):
                if rec.at_fab:
                    if robot.pos != world.depot:
                        return ("move", world.depot)
                    if item == "fab_tooling" and world.free_fab(sim.t) is None:
                        world.tooling_waiting_until = sim.t + 2  # hold the next free fab for tooling
                    if world.free_fab(sim.t) is None or (item == "chip" and world.tooling_waiting_until > sim.t):
                        continue
                return ("start", rec) if robot.energy >= rec.energy + reserve else ("wait",)

        source = self.nearest_raw_source(robot, [r for r in RAW if net[r]], sim)
        if source:
            pos, rtype, from_stock = source
            if robot.pos == pos:
                return ("take_stock", rtype, net[rtype]) if from_stock else ("mine", rtype)
            return ("move", pos)
        if any(net[r] for r in RAW):
            sim.stats["starved_feedstock"] += 1  # needed deposits are exhausted
        else:
            sim.stats["waiting_on_chips"] += 1  # only imports missing: none available and fabs busy/absent
        return ("wait",)

    def nearest_raw_source(self, robot: Robot, wanted: list[str], sim: "Simulation"):
        world, best = sim.world, None
        for r in wanted:
            options = []
            dep = world.nearest_deposit(robot.pos, r)
            if dep is not None:
                # harvesting the atmosphere/regolith in place is slow; treat it as a few cells of travel
                options.append((dist(robot.pos, dep) + (3 if r in EVERYWHERE else 0), dep, r, False))
            if world.stockpile[r] > 0:
                options.append((dist(robot.pos, world.depot), world.depot, r, True))
            for o in options:
                if best is None or o[0] < best[0]:
                    best = o
        return best[1:] if best else None

    def decide_worker(self, robot: Robot, sim: "Simulation") -> Action:
        """Idle non-replicators harvest feedstock into the base stockpile."""
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
