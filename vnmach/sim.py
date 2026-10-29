"""Simulation loop: physics of actions, births, deaths, economy, and metrics."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from .bom import ASSEMBLY_ENERGY, ASSEMBLY_TICKS, FAB_KIT, RAW, ROBOT_BOM, default_recipes, defect_probability
from .controller import ScriptedController, step_toward
from .econ import EconConfig, Economy
from .robot import BATTERY_CAPACITY, CHILD_ENERGY, MINE_COST, MOVE_COST, Genome, Job, Robot
from .world import SOL_TICKS, World

INSPECTION_DETECT_RATE = 0.9
REPAIR_TICKS = 10
FATAL_PARTS = ("chip", "frame")
BLOCKED_SLEEP_TICKS = 3
PROSPECT_FIND_CHANCE = 1 / 150  # per robot-tick spent surveying
CONJUNCTION_SOLS = 14  # Sun between Earth and Mars: no comms


@dataclass
class Config:
    seed: int = 0
    ticks: int = 20000  # 2000 sols, ~2.5 synodic periods
    size: int = 48
    seed_robots: int = 3  # a single seed robot is a single point of failure
    seed_chips: int = 20
    chip_fab: bool = False  # base starts with a working chip fab
    dust_storms: bool = True
    defect_rate: float = 0.02
    mutation_rate: float = 0.0  # probability the child's genome differs from the parent's
    governor_protected: bool = True  # can mutations change max_children?
    max_children: int = 3
    max_generation: int | None = None
    max_population: int = 400
    mtbf: float = 4000.0  # mean ticks between component failures per robot
    self_repair: bool = True  # robots fabricate and swap in replacements for broken parts
    # Manipulation skill, measured by the arm benchmark (vnmach.arm.skill_profile):
    skill_success: float = 1.0  # chance a fabricate/assemble manipulation succeeds
    skill_time_multiplier: float = 1.0  # duration vs. nominal scripted control
    assembly_insertions: int = 4  # a robot assembly is several insertions; each must succeed
    deposits_per_type: int = 12
    deposit_amount: int = 5000
    sample_every: int = 100
    director_interval: int = 250
    director_location: str = "mars"  # "earth": blacked out during solar conjunction, decisions arrive late
    earth_latency_ticks: int = 1  # light time (4-24 min) + human/ops turnaround, in ticks
    econ: EconConfig = field(default_factory=EconConfig)


@dataclass
class Simulation:
    config: Config
    director: object | None = None
    rng: random.Random = field(init=False)
    world: World = field(init=False)
    robots: list[Robot] = field(init=False)
    t: int = 0

    def __post_init__(self):
        c = self.config
        self.rng = random.Random(c.seed)
        self.recipes = default_recipes(chip_fab=False)  # chips become makeable once a fab exists
        self.controller = ScriptedController(self.recipes)
        self.world = World(self.rng, c.size, c.deposits_per_type, c.deposit_amount, c.seed_chips,
                           c.dust_storms, c.chip_fab)
        self.econ = Economy(c.econ, random.Random(c.seed + 10_000), c.size)
        self.robots = []
        self.next_id = 0
        self.policy: dict = {
            "replicate": True, "build_fab": False, "target_fabs": 1, "fab_share": 0.5, "make_tooling": True,
            "chip_reserve": 0, "contract_share": 0.0, "harvest_focus": None,
        }
        self.policy_log: list[tuple[int, dict]] = []
        self.pending: list[tuple[int, dict]] = []  # director updates in flight from Earth
        self.stats: Counter = Counter()
        self.history: list[dict] = []
        for _ in range(c.seed_robots):
            self.spawn(self.world.depot, Genome(max_children=c.max_children), generation=0, parent=None)
        self.population = len(self.robots)  # refreshed each tick

    # ---- population -------------------------------------------------------

    def spawn(self, pos, genome, generation, parent, defects=None, energy=30.0) -> Robot:
        r = Robot(self.next_id, pos, generation, parent, genome, energy=energy,
                  built_defects=Counter(defects or {}), born=self.t)
        self.next_id += 1
        self.robots.append(r)
        return r

    @property
    def alive(self) -> list[Robot]:
        return [r for r in self.robots if r.alive]

    def can_replicate(self, robot: Robot) -> bool:
        c = self.config
        if not self.policy.get("replicate", True):
            return False
        if robot.children >= robot.genome.max_children:
            return False
        if c.max_generation is not None and robot.generation >= c.max_generation:
            return False
        return self.population < c.max_population

    # ---- comms --------------------------------------------------------------

    def in_conjunction(self, t: int) -> bool:
        """Solar conjunction falls roughly midway between launch windows."""
        w = self.econ.cfg.window_ticks
        mid = w // 2
        return abs((t % w) - mid) <= CONJUNCTION_SOLS * SOL_TICKS // 2

    def director_step(self) -> None:
        c = self.config
        if self.director and self.t % c.director_interval == 0:
            earth = c.director_location == "earth"
            if earth and self.in_conjunction(self.t):
                self.stats["director_blackouts"] += 1
            else:
                update = self.director.decide(self.snapshot())
                if update:
                    delay = c.earth_latency_ticks if earth and self.t > 0 else 0
                    self.pending.append((self.t + delay, update))
        due = [u for at, u in self.pending if at <= self.t]
        self.pending = [(at, u) for at, u in self.pending if at > self.t]
        for update in due:
            self.apply_update(update)

    def apply_update(self, update: dict) -> None:
        update = dict(update)
        orders = update.pop("orders", None) or {}
        accept = update.pop("accept_contracts", None) or []
        if orders:
            self.econ.order(orders)
        if accept:
            self.econ.accept(accept)
        self.policy.update(update)
        self.policy_log.append((self.t, {**update, **({"orders": orders} if orders else {}),
                                         **({"accept_contracts": accept} if accept else {})}))

    # ---- supply chain -------------------------------------------------------

    def land(self, items: Counter) -> None:
        for item, q in items.items():
            if item == "robot":
                for _ in range(q):
                    self.spawn(self.world.depot, Genome(max_children=self.config.max_children), 0, None)
                self.stats["robots_imported"] += q
            else:
                self.world.imports[item] += q
                self.stats[f"imported_{item}"] += q
        if self.world.install_imported_tooling():
            self.stats["fabs_built"] = self.world.n_fabs

    def economy_step(self) -> None:
        e, t = self.econ, self.t
        if t == 0:
            # First landing: seed robots and chips, plus whatever the director put on the manifest.
            first = Counter({"robot": self.config.seed_robots, "chip": self.config.seed_chips})
            extra = Counter(e.manifest)
            e.manifest.clear()
            first.update(extra)
            cost = e.manifest_cost(first, 0)
            hardware = sum(e.cfg.catalog[k].price * q for k, q in first.items())
            e.book("earth_hardware", -hardware)
            e.book("launch", -(cost - hardware))
            self.land(extra)
        elif t % e.cfg.window_ticks == 0:
            s = e.launch(t)
            if s:
                self.stats["shipments"] += 1
        for s in e.arrivals(t):
            self.land(s.items)
        e.maybe_offer(t)
        e.tick(t)

    # ---- main loop --------------------------------------------------------

    def run(self) -> list[dict]:
        for _ in range(self.config.ticks):
            self.step()
            if not self.population and not self.econ.in_transit:
                break
        self.sample()
        return self.history

    def step(self) -> None:
        self.director_step()
        self.economy_step()
        if self.t % self.config.sample_every == 0:
            self.sample()
        self.world.tick(self.t)
        order = self.alive
        self.population = len(order)
        self.rng.shuffle(order)
        for r in order:
            self.tick_robot(r)
        self.t += 1

    def tick_robot(self, r: Robot) -> None:
        if r.dud:
            r.alive = False
            self.stats["duds"] += 1
            return
        if self.rng.random() < 1.0 / self.config.mtbf:
            self.component_failure(r)
            if not r.alive:
                return
        r.energy = min(BATTERY_CAPACITY, r.energy + r.solar_output * self.world.solar_factor(self.t))
        if self.rng.random() > r.efficiency:
            self.stats["lost_ticks"] += 1  # defective parts waste time
            return
        if r.job:
            r.job.remaining -= 1
            if r.job.remaining <= 0:
                self.finish_job(r)
            return
        if r.travel_to is not None:
            if r.pos != r.travel_to:
                self.execute(r, ("move", r.travel_to))
                return
            r.travel_to = None
        if r.sleep_until > self.t:
            return
        action = self.controller.decide(r, self)
        if action[0] == "move":
            r.travel_to = action[1]
        elif action[0] == "wait":
            r.sleep_until = self.t + BLOCKED_SLEEP_TICKS
        self.execute(r, action)

    def execute(self, r: Robot, action: tuple) -> None:
        kind, w = action[0], self.world
        if kind == "move":
            if r.energy >= MOVE_COST:
                r.energy -= MOVE_COST
                r.pos = step_toward(r.pos, action[1])
        elif kind == "mine":
            if r.energy >= MINE_COST:
                r.energy -= MINE_COST
                r.inventory[action[1]] += w.mine(r.pos, action[1])
        elif kind == "take_stock":
            got = min(action[2], w.stockpile[action[1]])
            w.stockpile[action[1]] -= got
            r.inventory[action[1]] += got
        elif kind == "take_import":
            if w.imports[action[1]] > 0:
                w.imports[action[1]] -= 1
                r.inventory[action[1]] += 1
                self.stats[f"used_imported_{action[1]}"] += 1
        elif kind == "prospect":
            if r.energy >= MOVE_COST:
                r.energy -= MOVE_COST
                dx, dy = self.rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
                p = (r.pos[0] + dx, r.pos[1] + dy)
                if w.in_bounds(p):
                    r.pos = p
                self.stats["prospect_ticks"] += 1
                if self.rng.random() < PROSPECT_FIND_CHANCE:
                    w.prospect(action[1], self.config.deposit_amount)
                    self.stats[f"veins_found_{action[1]}"] += 1
        elif kind == "deliver":
            for raw in RAW:
                n = r.inventory[raw]
                if n:
                    w.stockpile[raw] += n
                    r.work_done += n
                    self.stats["harvested"] += n
                    r.inventory[raw] = 0
        elif kind == "deliver_contract":
            contract = next((c for c in self.econ.active_contracts() if c.id == action[1]), None)
            if contract is not None:
                item = action[2]
                n = self.econ.deliver(contract, item, r.inventory[item], self.t)
                r.take_parts(item, n, self.rng)
                self.stats["contract_units"] += n
                if contract.status == "done":
                    self.stats["contracts_done"] += 1
        elif kind == "deliver_kit":
            if w.n_fabs >= self.policy.get("target_fabs", 1):
                return  # target already met; controller will retarget next tick
            for item, q in FAB_KIT.items():
                r.take_parts(item, q, self.rng)
            w.fab_kits += 1
            self.stats["fab_kits"] += 1
            self.after_fab_change()
        elif kind == "deliver_tooling":
            r.take_parts("fab_tooling", 1, self.rng)
            w.fab_tooling += 1
            self.stats["tooling_made_on_mars"] += 1
            self.after_fab_change()
        elif kind == "start":
            rec = action[1]
            if rec.at_fab:
                w.fab_busy[w.free_fab(self.t)] = self.t + rec.ticks
            r.energy -= rec.energy
            for i, q in rec.inputs.items():
                r.take_parts(i, q, self.rng)
            r.job = Job(rec.kind, rec.output, self.skill_ticks(rec.ticks, rec.kind), rec)
        elif kind == "repair":
            part = action[1]
            bad = r.take_parts(part, 1, self.rng)
            r.job = Job("repair", part, REPAIR_TICKS, child_defects=Counter({part: bad}))
        elif kind == "assemble":
            r.energy -= ASSEMBLY_ENERGY + CHILD_ENERGY
            defects = Counter()
            for part, q in ROBOT_BOM.items():
                bad = r.take_parts(part, q, self.rng)
                if bad:
                    defects[part] += bad
            r.job = Job("assemble", "robot", self.skill_ticks(ASSEMBLY_TICKS, "assemble"), child_defects=defects)

    def skill_ticks(self, ticks: int, kind: str) -> int:
        if kind == "refine":  # process chemistry, not manipulation
            return ticks
        return max(1, round(ticks * self.config.skill_time_multiplier))

    def manipulation_failed(self, insertions: int = 1) -> bool:
        p = self.config.skill_success ** insertions
        if p < 1.0 and self.rng.random() > p:
            self.stats["manipulation_failures"] += 1
            return True
        return False

    def after_fab_change(self) -> None:
        w = self.world
        w.install_imported_tooling()
        w.sync_fabs()
        if w.n_fabs > self.stats["fabs_built"]:
            self.stats["fabs_built"] = w.n_fabs
            self.stats.setdefault("first_fab_at", self.t)

    def component_failure(self, r: Robot) -> None:
        """A random component wears out. Losing the chip or the frame is fatal; anything else degrades the robot."""
        parts = [p for p, q in ROBOT_BOM.items() for _ in range(q)]
        part = self.rng.choice(parts)
        self.stats["component_failures"] += 1
        if part in FATAL_PARTS:
            r.alive = False
            self.stats["failures"] += 1
        else:
            r.built_defects[part] += 1

    def finish_job(self, r: Robot) -> None:
        job, r.job = r.job, None
        if job.kind == "repair":
            if job.child_defects[job.output]:
                self.stats["repairs_failed"] += 1  # swapped in a part that was itself defective
            else:
                r.built_defects[job.output] -= 1
                self.stats["repairs"] += 1
            return
        if job.kind == "assemble":
            if self.manipulation_failed(self.config.assembly_insertions):
                # Botched assembly: one part is destroyed, the rest go back into inventory.
                parts = [p for p, q in ROBOT_BOM.items() for _ in range(q)]
                lost = self.rng.choice(parts)
                for p, q in ROBOT_BOM.items():
                    r.inventory[p] += q - (1 if p == lost else 0)
                return
            genome = r.genome
            if self.rng.random() < self.config.mutation_rate:
                genome = genome.mutate(self.rng, self.config.governor_protected)
                self.stats["mutations"] += 1
            self.spawn(r.pos, genome, r.generation + 1, r.id, job.child_defects, energy=CHILD_ENERGY)
            r.children += 1
            self.stats["births"] += 1
            if job.child_defects:
                self.stats["defective_births"] += 1
            return
        if job.kind == "fabricate" and self.manipulation_failed():
            return  # part scrapped; inputs are lost
        if job.output == "chip":
            self.stats["chips_made"] += 1
        if job.kind == "fabricate" and self.rng.random() < defect_probability(job.recipe, self.config.defect_rate):
            self.stats["defects_made"] += 1
            if r.can_inspect and self.rng.random() < INSPECTION_DETECT_RATE:
                self.stats["defects_rejected"] += 1
                return
            r.hidden_defects[job.output] += 1
        r.inventory[job.output] += 1

    # ---- observation ------------------------------------------------------

    def fleet_value(self) -> float:
        """Replacement value of the fleet: what the same robots would cost landed from Earth today."""
        return len(self.alive) * self.econ.landed_cost("robot", self.t)

    def snapshot(self) -> dict:
        alive = self.alive
        replicators = sum(self.can_replicate(r) for r in alive)
        w = self.world
        return {
            "t": self.t,
            "sol": self.t // SOL_TICKS,
            "population": len(alive),
            "replicators": replicators,
            "workers": len(alive) - replicators,
            "births": self.stats["births"],
            "defective_births": self.stats["defective_births"],
            "duds": self.stats["duds"],
            "failures": self.stats["failures"],
            "repairs": self.stats["repairs"],
            "chips_in_depot": w.imports["chip"],
            "imports_in_depot": {k: v for k, v in w.imports.items() if v},
            "chips_made": self.stats["chips_made"],
            "fabs": w.n_fabs,
            "fab_kits": w.fab_kits,
            "fab_tooling": w.fab_tooling,
            "dust_storm": w.storm(self.t),
            "comms_blackout": self.in_conjunction(self.t),
            "stockpile": dict(w.stockpile),
            "deposits_remaining": w.remaining(),
            "harvested": self.stats["harvested"],
            "waiting_on_chips": self.stats["waiting_on_chips"],
            "starved_feedstock": self.stats["starved_feedstock"],
            "mean_efficiency": round(sum(r.efficiency for r in alive) / len(alive), 3) if alive else 0.0,
            "max_generation": max((r.generation for r in alive), default=0),
            "max_children_genome": max((r.genome.max_children for r in alive), default=0),
            "fleet_value_musd": round(self.fleet_value() / 1e6, 1),
            "economy": self.econ.snapshot(self.t),
            "policy": dict(self.policy),
        }

    def sample(self) -> None:
        s = self.snapshot()
        econ = s.pop("economy")
        for k in ("policy", "stockpile", "deposits_remaining", "imports_in_depot"):
            s.pop(k)
        s["cash_musd"] = econ["cash_musd"]
        s["revenue_musd"] = econ["ledger_musd"].get("contract_revenue", 0.0)
        s["contracts_done"] = econ["contracts_done"]
        s["net_value_musd"] = round(s["cash_musd"] + s["fleet_value_musd"], 1)
        self.history.append(s)
