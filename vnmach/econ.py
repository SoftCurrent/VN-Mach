"""Economics: Earth supply chain, launch windows, contracts and the cash ledger.

All money is USD. Every number here is an assumption, kept in one place so it
can be argued with and swept:

  - Launch: Starship-class cargo to the Martian surface. Early-flight cost
    per kg landed is very uncertain; $5,000/kg is a middle guess, falling
    ~15% per launch window as flight rate grows, floored at $500/kg.
  - Windows: Earth-Mars launch windows open every synodic period (~780
    sols); transit ~200 sols. Cargo can only leave Earth in a window.
  - Prices: Earth list prices for space-qualified hardware. A robot "chip"
    is the whole electronics module (compute, motor drivers, comms).
  - Contracts: other Mars operators buy locally made goods. They would
    otherwise ship them from Earth, so the price they'll pay is pegged to
    `mars_price_ratio` x (mass x launch cost/kg). Cheaper launch lowers the
    value of everything made on Mars.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from .body import BODIES, MARS, Body
from .world import SOL_TICKS

KG_PER_UNIT = MARS.kg_per_unit  # Mars: one inventory unit weighs ~100 kg; a robot is ~2.5 t (construction-class)


@dataclass(frozen=True)
class CatalogItem:
    price: float  # Earth purchase price, USD
    mass_kg: float


def bom_mass_kg(item: str, body: Body = MARS) -> float:
    if item == "robot":
        return sum(bom_mass_kg(p, body) * q for p, q in body.robot_bom.items())
    rec = body.all_recipes().get(item)
    if rec:
        mass = rec.mass
    else:
        mass = body.vitamin_mass if item == body.vitamin else 1.0
    return mass * body.kg_per_unit


EARTH_PRICES = MARS.earth_prices  # chip = rad-tolerant compute + drivers + comms module


def catalog_for(body: Body) -> dict[str, CatalogItem]:
    """Earth catalog: parts priced per body, masses from the robot design; special items (tooling, anodes,
    complete stations) carry their own mass."""
    cat = {k: CatalogItem(v, bom_mass_kg(k, body)) for k, v in body.earth_prices.items()}
    for k, (price, mass) in body.special_items.items():
        cat[k] = CatalogItem(price, mass)
    return cat


def default_catalog() -> dict[str, CatalogItem]:
    # Mars fab tooling is the imported half of a chip fab: deposition, lithography, etch, test.
    # The other half (structure, motion stages) is built on Mars from fab kits.
    return catalog_for(MARS)


@dataclass
class EconConfig:
    starting_cash: float = 600_000_000
    launch_cost_per_kg: float = 5_000
    launch_cost_decline: float = 0.15  # per window
    launch_cost_floor: float = 500
    shipment_fixed_cost: float = 10_000_000  # per window with any cargo (integration, ops)
    window_period_sols: int = 780
    transit_sols: int = 200
    ops_cost_per_sol: float = 50_000  # mission control on Earth
    mars_price_ratio: float = 0.5  # contract price vs shipping the same goods from Earth
    contract_offer_every_sols: int = 20
    demand_growth_per_window: float = 4.0  # extra offers per period, per launch window elapsed
    catalog: dict[str, CatalogItem] = field(default_factory=default_catalog)
    body: str = "mars"
    contract_types: dict = field(default_factory=lambda: dict(MARS.contract_types))

    @classmethod
    def for_body(cls, body: Body, **overrides) -> "EconConfig":
        """The body's default economics, with any non-None overrides applied."""
        kw = dict(body.econ_defaults)
        kw.update({k: v for k, v in overrides.items() if v is not None})
        return cls(catalog=catalog_for(body), body=body.name, contract_types=dict(body.contract_types), **kw)

    def item_mass(self, item: str) -> float:
        return item_mass_kg(item, self.catalog, BODIES[self.body])

    @property
    def window_ticks(self) -> int:
        return self.window_period_sols * SOL_TICKS

    @property
    def transit_ticks(self) -> int:
        return self.transit_sols * SOL_TICKS


def item_mass_kg(item: str, catalog: dict[str, CatalogItem], body: Body = MARS) -> float:
    if item in catalog:
        return catalog[item].mass_kg
    return bom_mass_kg(item, body)


# ---- contracts --------------------------------------------------------------

CONTRACT_TYPES = MARS.contract_types  # name: (client, requirements, deadline_sols)


@dataclass
class Contract:
    id: int
    kind: str
    client: str
    requirements: dict[str, int]
    payment: float
    offered_at: int
    deadline: int  # tick
    site: tuple[int, int]
    delivered: Counter = field(default_factory=Counter)
    status: str = "offered"  # offered | active | done | failed | expired

    @property
    def remaining(self) -> Counter:
        return Counter({k: v - self.delivered[k] for k, v in self.requirements.items() if v > self.delivered[k]})

    def summary(self, t: int) -> dict:
        return {"id": self.id, "kind": self.kind, "client": self.client, "requirements": self.requirements,
                "remaining": dict(self.remaining), "payment_musd": round(self.payment / 1e6, 2),
                "sols_left": (self.deadline - t) // SOL_TICKS, "status": self.status}


# ---- ledger and supply chain -------------------------------------------------

@dataclass
class Shipment:
    items: Counter
    departs: int
    arrives: int
    cost: float


class Economy:
    def __init__(self, cfg: EconConfig, rng: random.Random, map_size: int):
        self.cfg = cfg
        self.rng = rng
        self.map_size = map_size
        self.cash = cfg.starting_cash
        self.ledger: Counter = Counter()  # category -> USD (spend negative, income positive)
        self.manifest: Counter = Counter()  # ordered, waiting for the next window
        self.in_transit: list[Shipment] = []
        self.contracts: list[Contract] = []
        self.next_contract_id = 0
        self.windows_flown = 0

    # -- prices
    def launch_cost_per_kg(self, t: int) -> float:
        windows = t // self.cfg.window_ticks
        c = self.cfg.launch_cost_per_kg * (1 - self.cfg.launch_cost_decline) ** windows
        return max(self.cfg.launch_cost_floor, c)

    def landed_cost(self, item: str, t: int) -> float:
        ci = self.cfg.catalog[item]
        return ci.price + ci.mass_kg * self.launch_cost_per_kg(t)

    def next_window(self, t: int) -> int:
        w = self.cfg.window_ticks
        return ((t + w - 1) // w) * w

    def book(self, category: str, amount: float) -> None:
        self.cash += amount
        self.ledger[category] += amount

    # -- orders
    def order(self, items: dict[str, int]) -> dict[str, int]:
        accepted = {k: int(v) for k, v in items.items() if k in self.cfg.catalog and int(v) > 0}
        self.manifest.update(accepted)
        return accepted

    def manifest_cost(self, manifest: Counter, t: int) -> float:
        if not manifest:
            return 0.0
        return self.cfg.shipment_fixed_cost + sum(self.landed_cost(k, t) * q for k, q in manifest.items())

    def launch(self, t: int) -> Shipment | None:
        """At a window: buy and launch the manifest, trimmed to what we can afford."""
        manifest = Counter(self.manifest)
        self.manifest.clear()
        while manifest and self.manifest_cost(manifest, t) > self.cash:
            # drop the single most expensive line item unit until affordable
            worst = max(manifest, key=lambda k: self.landed_cost(k, t))
            manifest[worst] -= 1
            if manifest[worst] <= 0:
                del manifest[worst]
        if not manifest:
            return None
        cost = self.manifest_cost(manifest, t)
        hardware = sum(self.cfg.catalog[k].price * q for k, q in manifest.items())
        self.book("earth_hardware", -hardware)
        self.book("launch", -(cost - hardware))
        s = Shipment(manifest, t, t + self.cfg.transit_ticks, cost)
        self.in_transit.append(s)
        self.windows_flown += 1
        return s

    def arrivals(self, t: int) -> list[Shipment]:
        arrived = [s for s in self.in_transit if s.arrives == t]
        self.in_transit = [s for s in self.in_transit if s.arrives != t]
        return arrived

    # -- contracts
    def maybe_offer(self, t: int) -> list[Contract]:
        """New offers each period; the Mars economy (and demand) grows with every launch window."""
        if t % (self.cfg.contract_offer_every_sols * SOL_TICKS) != 0:
            return []
        windows = t / self.cfg.window_ticks
        n = 2 + int(self.rng.random() + windows * self.cfg.demand_growth_per_window)
        return [self.offer(t) for _ in range(n)]

    def offer(self, t: int) -> Contract:
        types = self.cfg.contract_types
        kind = self.rng.choice(sorted(types))
        client, reqs, deadline_sols = types[kind]
        mass = sum(self.cfg.item_mass(i) * q for i, q in reqs.items())
        payment = self.cfg.mars_price_ratio * mass * self.launch_cost_per_kg(t) * self.rng.uniform(0.8, 1.2)
        site = (self.rng.randrange(self.map_size), self.rng.randrange(self.map_size))
        c = Contract(self.next_contract_id, kind, client, dict(reqs), payment, t,
                     t + deadline_sols * SOL_TICKS, site)
        self.next_contract_id += 1
        self.contracts.append(c)
        return c

    def accept(self, ids: list[int]) -> list[int]:
        ok = []
        for c in self.contracts:
            if c.id in ids and c.status == "offered":
                c.status = "active"
                ok.append(c.id)
        return ok

    def active_contracts(self) -> list[Contract]:
        return [c for c in self.contracts if c.status == "active"]

    def deliver(self, contract: Contract, item: str, qty: int, t: int) -> int:
        n = min(qty, contract.remaining[item])
        contract.delivered[item] += n
        if not contract.remaining:
            contract.status = "done"
            self.book("contract_revenue", contract.payment)
        return n

    def tick_contracts(self, t: int) -> None:
        for c in self.contracts:
            if c.status == "offered" and t >= c.offered_at + 30 * SOL_TICKS:
                c.status = "expired"
            elif c.status == "active" and t > c.deadline:
                c.status = "failed"
                self.book("contract_penalty", -0.1 * c.payment)

    def tick(self, t: int) -> None:
        if t % SOL_TICKS == 0:
            self.book("operations", -self.cfg.ops_cost_per_sol)
        self.tick_contracts(t)

    def snapshot(self, t: int) -> dict:
        return {
            "cash_musd": round(self.cash / 1e6, 2),
            "ledger_musd": {k: round(v / 1e6, 2) for k, v in self.ledger.items()},
            "launch_cost_per_kg": round(self.launch_cost_per_kg(t)),
            "landed_cost_usd": {k: round(self.landed_cost(k, t)) for k in self.cfg.catalog},
            "next_window_sol": self.next_window(t) // SOL_TICKS,
            "window_period_sols": self.cfg.window_period_sols,
            "transit_sols": self.cfg.transit_sols,
            "manifest": dict(self.manifest),
            "in_transit": [{"items": dict(s.items), "arrives_sol": s.arrives // SOL_TICKS} for s in self.in_transit],
            "contract_offers": [c.summary(t) for c in self.contracts if c.status == "offered"],
            "active_contracts": [c.summary(t) for c in self.active_contracts()],
            "contracts_done": sum(c.status == "done" for c in self.contracts),
            "contracts_failed": sum(c.status == "failed" for c in self.contracts),
        }


def initial_landing_cost(econ: Economy, seed_robots: int, seed_chips: int, extra: dict[str, int] | None = None) -> float:
    """What the first landing cost, booked at t=0."""
    m = Counter({"robot": seed_robots, "chip": seed_chips})
    m.update(extra or {})
    return econ.manifest_cost(m, 0)

