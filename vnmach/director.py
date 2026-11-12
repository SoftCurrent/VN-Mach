"""High-level colony planners ("directors").

The director runs every `director_interval` ticks, reads a colony snapshot,
and returns policy updates. It never touches motors directly - the
hierarchical split real AI robot stacks use: a slow, smart planner on top of
fast, reliable low-level skills.

Policy keys (persist until changed):
  replicate      bool  - may robots build children at all
  build_fab      bool  - divert some robots to building chip fabs
  target_fabs    int   - how many fabs to have
  fab_share      float - fraction of robots assigned to fab work while fabs < target_fabs
  make_tooling   bool  - make fab tooling in an existing fab (vs waiting for imports)
  chip_reserve   int   - imported chips held back for fab kits
  contract_share float - fraction of replicators diverted to paid contracts
  harvest_focus  str|None - feedstock idle workers prioritise
Actions (one-shot):
  orders            {item: qty} - buy from Earth; flies at the next launch window
                                  (orders at t=0 land with the first landing)
  accept_contracts  [id, ...]   - take on offered contracts
"""

from __future__ import annotations

from .body import BODIES, MARS, Body
from .world import SOL_TICKS

FAB_BUILD_SOLS = 150  # kits + install, observed in runs
MIN_PAYBACK_SOLS = 400
CATALOG_ITEMS = tuple(dict.fromkeys(k for b in BODIES.values() for k in [*b.earth_prices, *b.special_items]))


class StaticDirector:
    """Applies one fixed policy at t=0 and never changes it (the no-planner baseline)."""

    def __init__(self, **policy):
        self.policy = policy
        self.done = False

    def decide(self, snap: dict) -> dict:
        if self.done:
            return {}
        self.done = True
        return dict(self.policy)


class HeuristicDirector:
    """Hand-written planner with three supply strategies.

    import - never build a fab; ship chips from Earth every window.
    fab    - land fab tooling with the first landing; scale fabs with population.
    auto   - import chips while that's cheaper; switch to a fab when the chips
             still needed over the rest of the mission cost more landed than
             fab tooling does.
    """

    def __init__(self, strategy: str = "auto", target_population: int = 150, total_ticks: int = 20000,
                 robots_per_fab: int | None = None, body: Body = MARS):
        assert strategy in ("import", "fab", "auto")
        self.body = body
        st = body.station
        self.vitamin, self.core = body.vitamin, st.core
        self.kit_vitamins = st.kit.get(body.vitamin, 0)
        self.kits_required = st.kits_required
        self.fatal_share = len(body.fatal_parts) / sum(body.robot_bom.values())
        self.strategy = strategy
        self.target_population = target_population
        self.total_ticks = total_ticks
        self.robots_per_fab = robots_per_fab or body.robots_per_station
        self.fab_ordered = False
        self.last_starved = 0

    def chips_pipeline(self, snap: dict) -> int:
        e = snap["economy"]
        transit = sum(s["items"].get(self.vitamin, 0) for s in e["in_transit"])
        return snap["chips_in_depot"] + e["manifest"].get(self.vitamin, 0) + transit

    def tooling_pipeline(self, snap: dict) -> int:
        e = snap["economy"]
        transit = sum(s["items"].get(self.core, 0) for s in e["in_transit"])
        return snap["fab_tooling"] + snap["imports_in_depot"].get(self.core, 0) + \
            e["manifest"].get(self.core, 0) + transit

    def chips_still_needed(self, snap: dict) -> int:
        """Robots to build plus replacements for fatal failures over the rest of the mission."""
        remaining_ticks = max(0, self.total_ticks - snap["t"])
        growth = max(0, self.target_population - snap["population"])
        fatal_rate = self.fatal_share / snap["mtbf"]  # fatal share of failures / mtbf
        replacements = int(self.target_population * fatal_rate * remaining_ticks)
        return growth + replacements

    def import_downtime_cost(self, snap: dict) -> float:
        """Revenue lost to robots that are dead or unbuilt while waiting for the next launch window.

        Chips only arrive once per synodic period, so on average a replacement
        waits half a window (plus transit for the first one). A fab removes that wait.
        """
        e = snap["economy"]
        robot_sols = max(1, snap["population"]) * max(1, snap["sol"])
        revenue_per_robot_sol = e["ledger_musd"].get("contract_revenue", 0.0) * 1e6 / robot_sols
        remaining_sols = max(0, self.total_ticks - snap["t"]) // SOL_TICKS
        window_sols = (e["next_window_sol"] - snap["sol"]) or e["window_period_sols"]
        fatal_per_sol = self.fatal_share / snap["mtbf"] * SOL_TICKS
        missing = self.target_population * fatal_per_sol * window_sols / 2  # average fleet shortfall
        return missing * remaining_sols * revenue_per_robot_sol

    def want_fab(self, snap: dict) -> bool:
        if self.strategy == "import":
            return False
        if not self.body.station.makes_vitamin:
            return True  # Moon: stations make metal, not the vitamin; more stations is the only way to grow
        if self.strategy == "fab" or self.fab_ordered or snap["fabs"] > 0:
            return True
        e = snap["economy"]
        # Payback: tooling lands with the first landing at t=0, else at the next window + transit,
        # then the fab still has to be built. Too close to the end, it never pays back.
        ready_sol = 0 if snap["t"] == 0 else e["next_window_sol"] + e["transit_sols"]
        if self.total_ticks // SOL_TICKS - (ready_sol + FAB_BUILD_SOLS) < MIN_PAYBACK_SOLS:
            return False
        landed = e["landed_cost_usd"]
        import_cost = self.chips_still_needed(snap) * landed[self.vitamin] + self.import_downtime_cost(snap)
        return import_cost > landed[self.core]

    def decide(self, snap: dict) -> dict:
        if not self.body.station.makes_vitamin:
            return self.decide_vitamin_imported(snap)
        e = snap["economy"]
        pop = snap["population"]
        fab = self.want_fab(snap)
        orders: dict[str, int] = {}

        if fab and not self.fab_ordered and self.tooling_pipeline(snap) == 0:
            orders[self.core] = 1
            self.fab_ordered = True

        # Chips: cover growth until the next shipment could arrive (or until a fab is producing).
        if not fab or snap["fabs"] == 0:
            need = min(self.chips_still_needed(snap), max(0, self.target_population - pop))
            kits_left = max(0, self.kits_required - snap["fab_kits"]) if fab and snap["fabs"] == 0 else 0
            short = need + kits_left * self.kit_vitamins - self.chips_pipeline(snap)
            if short > 0:
                orders[self.vitamin] = short

        accept = self.accept_contracts(snap)
        kits_left = max(0, self.kits_required - snap["fab_kits"]) if snap["fabs"] == 0 else 0
        update = {
            "replicate": pop < self.target_population,
            "build_fab": fab,
            "target_fabs": max(1, pop // self.robots_per_fab) if fab else 0,
            "fab_share": 0.5 if snap["fabs"] == 0 else 0.25,
            "chip_reserve": kits_left * self.kit_vitamins if fab else 0,
            "contract_share": 0.15 if pop < self.target_population else 0.0,
        }
        if orders:
            update["orders"] = orders
        if accept:
            update["accept_contracts"] = accept
        return update

    def accept_contracts(self, snap: dict) -> list[int]:
        """Take what the fleet can plausibly deliver; nothing while starved of feedstock."""
        e, pop = snap["economy"], snap["population"]
        starving = snap["starved_feedstock"] - self.last_starved > pop * 25
        self.last_starved = snap["starved_feedstock"]
        capacity = 0 if starving else max(1, pop // 5) - len(e["active_contracts"])
        min_sols = min(120, e["window_period_sols"] * 2)  # Mars 120 sols, Moon 60 days
        return [c["id"] for c in sorted(e["contract_offers"], key=lambda c: -c["payment_musd"])
                if c["sols_left"] >= min_sols][:max(0, capacity)]

    def decide_vitamin_imported(self, snap: dict) -> dict:
        """Bodies whose station doesn't make the vitamin (Moon: MRE cells make metal; avionics come from Earth).

        Every robot needs an imported vitamin, so order enough for growth plus replacements until the
        next shipment could land. Stations need an imported core each when it can't be made locally.
        'import' keeps only the stations the colony started with; 'fab' and 'auto' add one per
        robots_per_fab robots.
        """
        e, pop = snap["economy"], snap["population"]
        fab = self.want_fab(snap)
        target_fabs = max(1, pop // self.robots_per_fab) if fab else snap["fabs"]
        orders: dict[str, int] = {}

        horizon_ticks = (e["window_period_sols"] + e["transit_sols"]) * SOL_TICKS
        replacements = self.target_population * self.fatal_share / snap["mtbf"] * horizon_ticks
        growth = max(0, self.target_population - pop)
        kits_needed = max(0, target_fabs * self.kits_required - snap["fab_kits"])
        need = growth + int(replacements) + 1 + kits_needed * self.kit_vitamins
        short = need - self.chips_pipeline(snap)
        if short > 0:
            orders[self.vitamin] = short
        if self.body.station.core_recipe is None:
            cores_short = target_fabs - self.tooling_pipeline(snap)
            if cores_short > 0:
                orders[self.core] = cores_short

        update = {
            "replicate": pop < self.target_population,
            "build_fab": fab and target_fabs > snap["fabs"],
            "target_fabs": target_fabs,
            "fab_share": 0.25,
            "chip_reserve": 0,
            "contract_share": 0.15 if pop < self.target_population else 0.0,
        }
        if orders:
            update["orders"] = orders
        accept = self.accept_contracts(snap)
        if accept:
            update["accept_contracts"] = accept
        return update


def sanitize(update: dict) -> dict:
    """Clamp a planner's output to valid policy values. Never trust a planner blindly."""
    out = {}
    for key in ("replicate", "build_fab", "make_tooling"):
        if key in update:
            out[key] = bool(update[key])
    if "target_fabs" in update:
        out["target_fabs"] = max(0, min(50, int(update["target_fabs"])))
    for key in ("fab_share", "contract_share"):
        if key in update:
            out[key] = max(0.0, min(1.0, float(update[key])))
    if "chip_reserve" in update:
        out["chip_reserve"] = max(0, min(100, int(update["chip_reserve"])))
    if "harvest_focus" in update:
        f = update["harvest_focus"]
        out["harvest_focus"] = f if any(f in b.raw for b in BODIES.values()) else None
    if isinstance(update.get("orders"), dict):
        orders = {k: max(0, min(500, int(v))) for k, v in update["orders"].items() if k in CATALOG_ITEMS}
        orders = {k: v for k, v in orders.items() if v}
        if orders:
            out["orders"] = orders
    if isinstance(update.get("accept_contracts"), list):
        out["accept_contracts"] = [int(i) for i in update["accept_contracts"]][:20]
    return out


def sols(ticks: int) -> int:
    return ticks // SOL_TICKS
