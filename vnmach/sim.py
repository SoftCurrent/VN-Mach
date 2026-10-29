"""Simulation loop: physics of actions, births, deaths, and metrics."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from .bom import ASSEMBLY_ENERGY, ASSEMBLY_TICKS, FAB_KIT, RAW, ROBOT_BOM, default_recipes, defect_probability
from .controller import ScriptedController, step_toward
from .robot import BATTERY_CAPACITY, CHILD_ENERGY, MINE_COST, MOVE_COST, Genome, Job, Robot
from .world import World

INSPECTION_DETECT_RATE = 0.9
REPAIR_TICKS = 10
FATAL_PARTS = ("chip", "frame")


@dataclass
class Config:
    seed: int = 0
    ticks: int = 4000
    size: int = 48
    seed_robots: int = 3  # a single seed robot is a single point of failure
    seed_vitamins: int = 20
    vitamin_resupply_interval: int = 0  # 0 = no resupply from Earth
    vitamin_resupply_amount: int = 0
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
    deposits_per_type: int = 12
    deposit_amount: int = 2000
    sample_every: int = 50
    director_interval: int = 250


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
        self.recipes = default_recipes(chip_fab=False)  # chips become makeable once the fab exists
        self.controller = ScriptedController(self.recipes)
        self.world = World(
            self.rng, c.size, c.deposits_per_type, c.deposit_amount, c.seed_vitamins,
            c.vitamin_resupply_interval, c.vitamin_resupply_amount, c.dust_storms, c.chip_fab,
        )
        self.robots = []
        self.next_id = 0
        self.policy: dict = {"replicate": True, "build_fab": False, "target_fabs": 1, "fab_share": 0.5, "chip_reserve": 0, "harvest_focus": None}
        self.policy_log: list[tuple[int, dict]] = []
        self.stats: Counter = Counter()
        self.history: list[dict] = []
        for _ in range(c.seed_robots):
            self.spawn(self.world.depot, Genome(max_children=c.max_children), generation=0, parent=None)

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
        return len(self.alive) < c.max_population

    # ---- main loop --------------------------------------------------------

    def run(self) -> list[dict]:
        for _ in range(self.config.ticks):
            self.step()
            if not self.alive:
                break
        self.sample()
        return self.history

    def step(self) -> None:
        c = self.config
        if self.director and self.t % c.director_interval == 0:
            update = self.director.decide(self.snapshot())
            if update:
                self.policy.update(update)
                self.policy_log.append((self.t, dict(update)))
        if self.t % c.sample_every == 0:
            self.sample()
        self.world.tick(self.t)
        order = self.alive
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
        self.execute(r, self.controller.decide(r, self))

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
        elif kind == "take_vitamin":
            if w.vitamins[action[1]] > 0:
                w.vitamins[action[1]] -= 1
                r.inventory[action[1]] += 1
                self.stats["vitamins_used"] += 1
        elif kind == "deliver":
            for raw in RAW:
                n = r.inventory[raw]
                if n:
                    w.stockpile[raw] += n
                    r.work_done += n
                    self.stats["harvested"] += n
                    r.inventory[raw] = 0
        elif kind == "deliver_kit":
            if w.n_fabs >= self.policy.get("target_fabs", 1):
                return  # target already met; controller will retarget next tick
            for item, q in FAB_KIT.items():
                r.take_parts(item, q, self.rng)
            before = w.n_fabs
            w.fab_kits += 1
            w.sync_fabs()
            self.stats["fab_kits"] += 1
            if w.n_fabs > before:
                self.stats["fabs_built"] += 1
                self.stats.setdefault("first_fab_at", self.t)
        elif kind == "start":
            rec = action[1]
            if rec.at_fab:
                w.fab_busy[w.free_fab(self.t)] = self.t + rec.ticks
            r.energy -= rec.energy
            for i, q in rec.inputs.items():
                r.take_parts(i, q, self.rng)
            r.job = Job(rec.kind, rec.output, rec.ticks, rec)
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
            r.job = Job("assemble", "robot", ASSEMBLY_TICKS, child_defects=defects)

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

    def snapshot(self) -> dict:
        alive = self.alive
        replicators = [r for r in alive if self.can_replicate(r)]
        return {
            "t": self.t,
            "population": len(alive),
            "replicators": len(replicators),
            "workers": len(alive) - len(replicators),
            "births": self.stats["births"],
            "defective_births": self.stats["defective_births"],
            "duds": self.stats["duds"],
            "failures": self.stats["failures"],
            "repairs": self.stats["repairs"],
            "chips_in_depot": self.world.vitamins["chip"],
            "chips_made": self.stats["chips_made"],
            "fabs": self.world.n_fabs,
            "fab_kits": self.world.fab_kits,
            "dust_storm": self.world.storm(self.t),
            "stockpile": dict(self.world.stockpile),
            "deposits_remaining": self.world.remaining(),
            "harvested": self.stats["harvested"],
            "waiting_on_chips": self.stats["waiting_on_chips"],
            "mean_efficiency": round(sum(r.efficiency for r in alive) / len(alive), 3) if alive else 0.0,
            "max_generation": max((r.generation for r in alive), default=0),
            "max_children_genome": max((r.genome.max_children for r in alive), default=0),
            "policy": dict(self.policy),
        }

    def sample(self) -> None:
        s = self.snapshot()
        s.pop("policy")
        s.pop("stockpile")
        s.pop("deposits_remaining")
        self.history.append(s)
