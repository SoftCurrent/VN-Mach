// VN-Mach simulation engine: a JavaScript port of the Python model in vnmach/
// (body.py, bom.py, world.py, econ.py, robot.py, controller.py, sim.py, director.py).
// Same rules and numbers; a different RNG, so runs match statistically, not tick for tick.
// No DOM: runs in the browser and in node (for parity tests).

const VN = (() => {
  const R = (output, inputs, energy, ticks, kind, precision = 0, mass = 1, at_fab = false, byproducts = {}) =>
    ({ output, inputs, energy, ticks, kind, precision, mass, at_fab, byproducts });
  const defectProbability = (rec, base) => Math.min(0.95, base * (1 + 9 * rec.precision));
  const byOutput = list => Object.fromEntries(list.map(r => [r.output, r]));

  // ---- bodies (mirrors vnmach/body.py) -------------------------------------
  const MARS = {
    name: "mars", place: "Mars", day: "sol", site: "Jezero-class site",
    raw: ["hematite", "basalt", "ice", "co2"], everywhere: new Set(["basalt", "co2"]),
    deposit_min_dist: { hematite: 0, ice: 0 },
    recipes: byOutput([
      R("steel", { hematite: 2, ice: 1, carbon: 1 }, 6, 4, "refine"),
      R("aluminium", { basalt: 3 }, 10, 5, "refine"),
      R("silicon", { basalt: 2 }, 8, 4, "refine"),
      R("glass", { basalt: 1 }, 3, 2, "refine"),
      R("carbon", { co2: 2 }, 4, 3, "refine"),
      R("polymer", { carbon: 1, ice: 1 }, 4, 3, "refine"),
      R("propellant", { co2: 2, ice: 2 }, 8, 4, "refine"),
      R("frame", { steel: 2, aluminium: 2 }, 4, 8, "fabricate", 0.1, 8.0),
      R("motor", { steel: 1, aluminium: 1 }, 3, 6, "fabricate", 0.4, 2.0),
      R("wiring", { aluminium: 1, polymer: 1 }, 1, 3, "fabricate", 0.2, 1.0),
      R("solar_panel", { silicon: 2, glass: 1 }, 5, 8, "fabricate", 0.5, 3.0),
      R("battery", { aluminium: 1, polymer: 1, steel: 1 }, 3, 5, "fabricate", 0.3, 3.0),
      R("sensor", { silicon: 1, glass: 1, aluminium: 1 }, 3, 6, "fabricate", 0.6, 0.5),
    ]),
    robot_bom: { frame: 1, motor: 4, wiring: 2, solar_panel: 1, battery: 1, sensor: 1, chip: 1 },
    assembly_ticks: 20, assembly_energy: 10, fatal_parts: new Set(["chip", "frame"]),
    vitamin: "chip", vitamin_mass: 0.2,
    station: {
      name: "chip fab", kit: { steel: 4, glass: 3, aluminium: 3, sensor: 2, motor: 2, chip: 1 }, kits_required: 4,
      core: "fab_tooling",
      core_recipe: R("fab_tooling", { steel: 12, aluminium: 8, silicon: 10, glass: 6, sensor: 4, motor: 4, chip: 2 },
        40, 400, "fabricate", 0.7, 80.0, true),
      recipes: [R("chip", { silicon: 4, aluminium: 1 }, 30, 40, "fabricate", 0.9, 0.2, true)],
      import_first: new Set(["chip"]), makes_vitamin: true,
    },
    station_prebuilt: false, station_item: "chip_fab", sunlight: "sol", dust_storms: true,
    mtbf: 4000, mine_units_per_tick: 1, robots_per_station: 25, conjunction_days: 14, earth_latency_ticks: 1,
    kg_per_unit: 100,
    earth_prices: { chip: 100e3, motor: 20e3, sensor: 30e3, solar_panel: 15e3, battery: 25e3, wiring: 2e3,
      frame: 40e3, robot: 2e6 },
    special_items: { fab_tooling: [40e6, 8000], chip_fab: [60e6, 15000] },
    contract_types: {
      landing_pad: ["launch provider", { glass: 60, steel: 20 }, 300],
      habitat_shell: ["space agency", { steel: 30, aluminium: 25, glass: 15 }, 400],
      propellant: ["return-flight operator", { propellant: 120 }, 250],
      solar_farm: ["research station", { solar_panel: 20, wiring: 15 }, 350],
      ice_delivery: ["life-support contractor", { ice: 200 }, 150],
    },
    econ: { starting_cash: 600e6, launch_cost_per_kg: 5000, launch_cost_decline: 0.15, launch_cost_floor: 500,
      shipment_fixed_cost: 10e6, window_period_sols: 780, transit_sols: 200, ops_cost_per_sol: 50e3,
      mars_price_ratio: 0.5, contract_offer_every_sols: 20, demand_growth_per_window: 4.0 },
    labels: { chip: "chip", fab_tooling: "fab tooling", chip_fab: "complete chip fab" },
  };

  const MOON = {
    name: "moon", place: "the Moon", day: "day", site: "South-pole ridge",
    raw: ["regolith", "ice"], everywhere: new Set(["regolith"]), deposit_min_dist: { ice: 14 },
    recipes: byOutput([
      R("iron", { regolith: 12 }, 3, 4, "refine"),
      R("glass", { regolith: 2 }, 4, 2, "refine"),
      R("frame", { iron: 4, fesi: 4 }, 6, 20, "fabricate", 0.1, 8.0),
      R("wheel", { iron: 1 }, 2, 6, "fabricate", 0.1, 1.0),
      R("motor", { fesi: 1, aluminium: 1, glass: 1 }, 5, 10, "fabricate", 0.5, 2.0),
      R("reducer", { iron: 2 }, 4, 14, "fabricate", 0.6, 2.0),
      R("arm", { fesi: 2, aluminium: 1 }, 4, 10, "fabricate", 0.3, 3.0),
      R("harness", { aluminium: 1, glass: 1 }, 2, 5, "fabricate", 0.2, 2.0),
    ]),
    robot_bom: { frame: 1, wheel: 4, motor: 8, reducer: 8, arm: 1, harness: 1, chip: 1 },
    assembly_ticks: 30, assembly_energy: 10, fatal_parts: new Set(["chip", "frame"]),
    vitamin: "chip", vitamin_mass: 1.5,
    station: {
      name: "MRE cell", kit: { iron: 10, fesi: 4, glass: 6, motor: 2, harness: 1, chip: 1 }, kits_required: 3,
      core: "anode", core_recipe: null,
      recipes: [
        R("fesi", { regolith: 4 }, 20, 6, "refine", 0, 1, true, { oxygen: 2 }),
        R("aluminium", { regolith: 5 }, 24, 7, "refine", 0, 1, true, { oxygen: 2 }),
        R("oxygen", { regolith: 3 }, 14, 4, "refine", 0, 1, true, { oxygen: 1 }),
      ],
      import_first: new Set(), makes_vitamin: false,
    },
    station_prebuilt: true, station_item: "mre_cell", sunlight: "polar", dust_storms: false,
    mtbf: 3000, mine_units_per_tick: 30, robots_per_station: 6, conjunction_days: 0, earth_latency_ticks: 0,
    kg_per_unit: 6,
    earth_prices: { chip: 100e3, motor: 20e3, reducer: 15e3, wheel: 5e3, arm: 60e3, harness: 5e3, frame: 40e3, robot: 3e6 },
    special_items: { anode: [2e6, 50], mre_cell: [25e6, 2000] },
    contract_types: {
      oxygen_delivery: ["lander operator", { oxygen: 150 }, 120],
      landing_pad: ["launch provider", { glass: 120, regolith: 200 }, 200],
      shielding_berm: ["habitat operator", { regolith: 500 }, 150],
      ice_delivery: ["propellant depot", { ice: 200 }, 150],
    },
    econ: { starting_cash: 300e6, launch_cost_per_kg: 20000, launch_cost_decline: 0.03, launch_cost_floor: 1000,
      shipment_fixed_cost: 5e6, window_period_sols: 30, transit_sols: 5, ops_cost_per_sol: 30e3,
      mars_price_ratio: 0.5, contract_offer_every_sols: 15, demand_growth_per_window: 0.15 },
    labels: { chip: "avionics module", fesi: "iron-silicon", mre_cell: "complete MRE cell" },
  };
  const BODIES = { mars: MARS, moon: MOON };
  const STORM_SOLAR = 0.25, POLAR_LIT_OUTPUT = 1 / 0.85;

  for (const b of Object.values(BODIES)) {
    b.all_recipes = { ...b.recipes };
    for (const rec of b.station.recipes) b.all_recipes[rec.output] = rec;
    if (b.station.core_recipe) b.all_recipes[b.station.core] = b.station.core_recipe;
    const massKg = item => item === "robot"
      ? Object.entries(b.robot_bom).reduce((s, [p, q]) => s + massKg(p) * q, 0)
      : (b.all_recipes[item] ? b.all_recipes[item].mass : item === b.vitamin ? b.vitamin_mass : 1) * b.kg_per_unit;
    b.catalog = Object.fromEntries(Object.entries(b.earth_prices).map(([k, v]) => [k, { price: v, mass_kg: massKg(k) }]));
    for (const [k, [price, mass]] of Object.entries(b.special_items)) b.catalog[k] = { price, mass_kg: mass };
    b.itemMassKg = item => b.catalog[item] ? b.catalog[item].mass_kg : massKg(item);
    b.label = item => b.labels[item] || item.replace(/_/g, " ");
    b.parts = Object.entries(b.robot_bom).flatMap(([p, q]) => Array(q).fill(p));
    b.solarFactor = (t, storm) => {
      if (b.sunlight === "polar") {
        const cycle = Math.round(29.5 * SOL_TICKS);
        return (t % cycle) >= Math.round(0.15 * cycle) ? POLAR_LIT_OUTPUT : 0;
      }
      const phase = (t % SOL_TICKS) / SOL_TICKS;
      return Math.max(0, Math.sin(2 * Math.PI * phase)) * Math.PI * (storm ? STORM_SOLAR : 1);
    };
  }

  // ---- robot / world constants --------------------------------------------
  const BATTERY_CAPACITY = 60, SOLAR_OUTPUT = 1, MOVE_COST = 0.3, MINE_COST = 0.5, CHILD_ENERGY = 15;
  const SOL_TICKS = 10, STORM_CHANCE = 1 / 3000, STORM_DURATION = [150, 600];
  const INSPECTION_DETECT_RATE = 0.9, REPAIR_TICKS = 10;
  const BLOCKED_SLEEP_TICKS = 3, PROSPECT_FIND_CHANCE = 1 / 150;
  const WORKER_CARGO = 10, CONTRACT_CHUNK = 10;

  // ---- helpers ------------------------------------------------------------
  const g = (o, k) => o[k] || 0;
  const add = (o, k, n) => { o[k] = (o[k] || 0) + n; };
  const dist = (a, b) => Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);
  const same = (a, b) => a[0] === b[0] && a[1] === b[1];
  const key = p => p[0] * 1000 + p[1];
  const unkey = k => [Math.floor(k / 1000), k % 1000];
  function rng(seed) {
    let a = (seed * 2654435761 + 0x9e3779b9) >>> 0;
    const r = () => {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    r.int = (lo, hi) => lo + Math.floor(r() * (hi - lo + 1));
    r.choice = arr => arr[Math.floor(r() * arr.length)];
    r.uniform = (lo, hi) => lo + r() * (hi - lo);
    return r;
  }
  function topoOrder(recipes) {
    const seen = new Set(), out = [];
    const visit = item => {
      if (seen.has(item) || !recipes[item]) return;
      seen.add(item);
      for (const i in recipes[item].inputs) visit(i);
      out.push(item);
    };
    for (const item in recipes) visit(item);
    return out.reverse();
  }
  const bucket = (id, salt) => ((id * 7919 + salt * 104729) % 1000) / 1000;
  const stepToward = (a, b) => a[0] !== b[0] ? [a[0] + (b[0] > a[0] ? 1 : -1), a[1]]
    : a[1] !== b[1] ? [a[0], a[1] + (b[1] > a[1] ? 1 : -1)] : a;

  // ---- world --------------------------------------------------------------
  class World {
    constructor(r, c, body) {
      this.rng = r; this.size = c.size; this.body = body;
      this.depot = [Math.floor(c.size / 2), Math.floor(c.size / 2)];
      this.imports = { [body.vitamin]: c.seed_chips };
      this.stockpile = {};
      this.dust_storms = c.dust_storms && body.dust_storms; this.storm_until = -1;
      this.kits_required = body.station.kits_required; this.core = body.station.core;
      this.fab_kits = c.chip_fab ? this.kits_required : 0;
      this.fab_tooling = c.chip_fab ? 1 : 0;
      this.fab_busy = []; this.tooling_waiting_until = -1; this.finds = {};
      this.syncFabs();
      this.deposits = {};
      for (const rt of body.raw) if (!body.everywhere.has(rt)) this.deposits[rt] = new Map();
      for (const rt in this.deposits) {
        const minDist = body.deposit_min_dist[rt] || 0;
        for (let v = 0; v < Math.max(1, Math.floor(c.deposits_per_type / 4)); v++) {
          let cx, cy;
          do { cx = Math.floor(r() * c.size); cy = Math.floor(r() * c.size); } while (dist([cx, cy], this.depot) < minDist);
          this.addVein(rt, cx, cy, c.deposit_amount);
        }
      }
    }
    addVein(rt, cx, cy, amount) {
      for (let i = 0; i < 4; i++) {
        const p = [Math.min(this.size - 1, Math.max(0, cx + this.rng.int(-2, 2))),
                   Math.min(this.size - 1, Math.max(0, cy + this.rng.int(-2, 2)))];
        const k = key(p);
        this.deposits[rt].set(k, (this.deposits[rt].get(k) || 0) + amount);
      }
    }
    get n_fabs() { return Math.min(Math.floor(this.fab_kits / this.kits_required), this.fab_tooling); }
    get fab_built() { return this.n_fabs > 0; }
    get fab_shells_ready() { return Math.floor(this.fab_kits / this.kits_required) - this.fab_tooling; }
    installImportedTooling() {
      let n = 0;
      while (g(this.imports, this.core) > 0 && this.fab_shells_ready > 0) {
        this.imports[this.core] -= 1; this.fab_tooling += 1; n++;
      }
      this.syncFabs();
      return n;
    }
    syncFabs() { while (this.fab_busy.length < this.n_fabs) this.fab_busy.push(-1); }
    freeFab(t) { for (let i = 0; i < this.fab_busy.length; i++) if (this.fab_busy[i] <= t) return i; return null; }
    storm(t) { return t < this.storm_until; }
    solarFactor(t) { return this.body.solarFactor(t, this.storm(t)); }
    dark(t) { return this.body.sunlight === "polar" && this.solarFactor(t) === 0; }
    nearestDeposit(pos, rt) {
      if (this.body.everywhere.has(rt)) return pos;
      let best = null, bd = Infinity;
      for (const k of this.deposits[rt].keys()) {
        const p = unkey(k), d = dist(pos, p);
        if (d < bd) { bd = d; best = p; }
      }
      return best;
    }
    mine(pos, rt, amount = 1) {
      if (this.body.everywhere.has(rt)) return amount;
      const cells = this.deposits[rt], k = key(pos), have = cells.get(k) || 0, got = Math.min(have, amount);
      if (got) { if (have - got <= 0) cells.delete(k); else cells.set(k, have - got); }
      return got;
    }
    prospect(rt, amount) {
      this.finds[rt] = g(this.finds, rt) + 1;
      const radius = Math.min(Math.floor(this.size / 2) - 3, Math.max(6 + 3 * this.finds[rt], this.body.deposit_min_dist[rt] || 0));
      let cx, cy;
      for (;;) {
        cx = this.depot[0] + this.rng.int(-radius, radius);
        cy = this.depot[1] + this.rng.int(-radius, radius);
        if (this.inBounds([cx, cy]) && dist([cx, cy], this.depot) >= Math.floor(radius / 2)) break;
      }
      this.addVein(rt, cx, cy, amount);
      return [cx, cy];
    }
    inBounds(p) { return p[0] >= 0 && p[0] < this.size && p[1] >= 0 && p[1] < this.size; }
    remaining() {
      const out = {};
      for (const rt in this.deposits) { let s = 0; for (const v of this.deposits[rt].values()) s += v; out[rt] = s; }
      return out;
    }
    tick(t, log) {
      if (this.dust_storms && !this.storm(t) && this.rng() < STORM_CHANCE) {
        this.storm_until = t + this.rng.int(...STORM_DURATION);
        log("storm", `Dust storm: solar power down to 25% for ${Math.round((this.storm_until - t) / SOL_TICKS)} sols`);
      }
    }
  }

  // ---- economy ------------------------------------------------------------
  class Economy {
    constructor(cfg, r, size, body) {
      this.cfg = cfg; this.rng = r; this.size = size; this.body = body;
      this.cash = cfg.starting_cash; this.ledger = {}; this.manifest = {};
      this.in_transit = []; this.contracts = []; this.next_id = 0;
    }
    get window_ticks() { return this.cfg.window_period_sols * SOL_TICKS; }
    get transit_ticks() { return this.cfg.transit_sols * SOL_TICKS; }
    launchCostPerKg(t) {
      const w = Math.floor(t / this.window_ticks);
      return Math.max(this.cfg.launch_cost_floor, this.cfg.launch_cost_per_kg * Math.pow(1 - this.cfg.launch_cost_decline, w));
    }
    landedCost(item, t) { const c = this.body.catalog[item]; return c.price + c.mass_kg * this.launchCostPerKg(t); }
    nextWindow(t) { const w = this.window_ticks; return Math.ceil(t / w) * w; }
    book(cat, amount) { this.cash += amount; add(this.ledger, cat, amount); }
    order(items) {
      const ok = {};
      for (const [k, v] of Object.entries(items)) if (this.body.catalog[k] && Math.floor(v) > 0) { ok[k] = Math.floor(v); add(this.manifest, k, ok[k]); }
      return ok;
    }
    manifestCost(m, t) {
      const ks = Object.keys(m).filter(k => m[k] > 0);
      if (!ks.length) return 0;
      return this.cfg.shipment_fixed_cost + ks.reduce((s, k) => s + this.landedCost(k, t) * m[k], 0);
    }
    hardwareCost(m) { return Object.entries(m).reduce((s, [k, q]) => s + this.body.catalog[k].price * q, 0); }
    launch(t) {
      const m = { ...this.manifest }; this.manifest = {};
      for (const k in m) if (m[k] <= 0) delete m[k];
      while (Object.keys(m).length && this.manifestCost(m, t) > this.cash) {
        const worst = Object.keys(m).reduce((a, b) => this.landedCost(a, t) >= this.landedCost(b, t) ? a : b);
        m[worst] -= 1; if (m[worst] <= 0) delete m[worst];
      }
      if (!Object.keys(m).length) return null;
      const cost = this.manifestCost(m, t), hw = this.hardwareCost(m);
      this.book("earth_hardware", -hw); this.book("launch", -(cost - hw));
      const s = { items: m, departs: t, arrives: t + this.transit_ticks, cost };
      this.in_transit.push(s);
      return s;
    }
    arrivals(t) {
      const a = this.in_transit.filter(s => s.arrives === t);
      this.in_transit = this.in_transit.filter(s => s.arrives !== t);
      return a;
    }
    maybeOffer(t) {
      if (t % (this.cfg.contract_offer_every_sols * SOL_TICKS) !== 0) return [];
      const windows = t / this.window_ticks;
      const n = 2 + Math.floor(this.rng() + windows * this.cfg.demand_growth_per_window);
      const out = [];
      for (let i = 0; i < n; i++) out.push(this.offer(t));
      return out;
    }
    offer(t) {
      const types = this.body.contract_types;
      const kind = this.rng.choice(Object.keys(types).sort());
      const [client, reqs, deadlineSols] = types[kind];
      const mass = Object.entries(reqs).reduce((s, [i, q]) => s + this.body.itemMassKg(i) * q, 0);
      const payment = this.cfg.mars_price_ratio * mass * this.launchCostPerKg(t) * this.rng.uniform(0.8, 1.2);
      const site = [Math.floor(this.rng() * this.size), Math.floor(this.rng() * this.size)];
      const c = { id: this.next_id++, kind, client, requirements: { ...reqs }, payment, offered_at: t,
        deadline: t + deadlineSols * SOL_TICKS, site, delivered: {}, status: "offered" };
      this.contracts.push(c);
      return c;
    }
    remaining(c) {
      const out = {};
      for (const [k, v] of Object.entries(c.requirements)) if (v > g(c.delivered, k)) out[k] = v - g(c.delivered, k);
      return out;
    }
    accept(ids) {
      const ok = [];
      for (const c of this.contracts) if (ids.includes(c.id) && c.status === "offered") { c.status = "active"; ok.push(c); }
      return ok;
    }
    active() { return this.contracts.filter(c => c.status === "active"); }
    offers() { return this.contracts.filter(c => c.status === "offered"); }
    deliver(c, item, qty) {
      const n = Math.min(qty, g(this.remaining(c), item));
      add(c.delivered, item, n);
      if (!Object.keys(this.remaining(c)).length) { c.status = "done"; this.book("contract_revenue", c.payment); }
      return n;
    }
    tick(t, log) {
      if (t % SOL_TICKS === 0) this.book("operations", -this.cfg.ops_cost_per_sol);
      for (const c of this.contracts) {
        if (c.status === "offered" && t >= c.offered_at + 30 * SOL_TICKS) c.status = "expired";
        else if (c.status === "active" && t > c.deadline) {
          c.status = "failed"; this.book("contract_penalty", -0.1 * c.payment);
          log("contract-bad", `Missed deadline: ${label(c.kind)} for the ${c.client} (penalty ${money(0.1 * c.payment)})`);
        }
      }
    }
  }

  const label = s => s.replace(/_/g, " ");
  const money = v => {
    const a = Math.abs(v), sign = v < 0 ? "-" : "";
    if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(a >= 1e8 ? 0 : 1)}M`;
    if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(0)}k`;
    return `${sign}$${a.toFixed(0)}`;
  };

  // ---- controller -----------------------------------------------------------
  class Controller {
    constructor(body) {
      this.body = body; this.kit = body.station.kit; this.coreTarget = { [body.station.core]: 1 };
      this.variants = {};
    }
    variant(built, skip) {
      const k = `${built}|${[...skip].sort().join(",")}`;
      if (!this.variants[k]) {
        const r = { ...this.body.recipes }, st = this.body.station;
        if (built) {
          for (const rec of st.recipes) if (!skip.has(rec.output)) r[rec.output] = rec;
          if (st.core_recipe) r[st.core] = st.core_recipe;
        }
        this.variants[k] = [r, topoOrder(r)];
      }
      return this.variants[k];
    }
    importAvailable(sim, item, buildingFab) {
      let n = g(sim.world.imports, item);
      if (item === this.body.vitamin && !buildingFab) n -= sim.policy.chip_reserve || 0;
      return n;
    }
    recipesFor(sim, buildingFab) {
      const built = sim.world.fab_built;
      const skip = new Set([...this.body.station.import_first].filter(i => !built || this.importAvailable(sim, i, buildingFab) > 0));
      return this.variant(built, skip);
    }
    net(robot, target, recipes, order) {
      const inv = robot.inventory, req = { ...target }, net = {};
      for (const item of order) {
        const n = Math.max(0, g(req, item) - g(inv, item));
        if (n) { net[item] = n; for (const [i, q] of Object.entries(recipes[item].inputs)) add(req, i, n * q); }
      }
      for (const [item, q] of Object.entries(req)) if (!recipes[item]) { const n = Math.max(0, q - g(inv, item)); if (n) net[item] = n; }
      return net;
    }
    fabTask(robot, sim) {
      const p = sim.policy, w = sim.world, target = p.target_fabs ?? 1;
      if (!p.build_fab || w.n_fabs >= target) return null;
      if (bucket(robot.id, 1) >= (p.fab_share ?? 0.5)) return null;
      if (w.fab_kits < target * w.kits_required) return this.kit;
      if (this.body.station.core_recipe && w.fab_shells_ready > g(w.imports, w.core) && w.fab_built && (p.make_tooling ?? true))
        return this.coreTarget;
      return null;
    }
    contractTask(robot, sim, forced) {
      const active = sim.econ.active();
      if (!active.length || (!forced && bucket(robot.id, 2) >= (sim.policy.contract_share || 0))) return null;
      for (const c of active) for (const item in sim.econ.remaining(c)) if (g(robot.inventory, item) > 0) return [c, item];
      const c = active[robot.id % active.length], items = Object.keys(sim.econ.remaining(c)).sort();
      return [c, items[Math.floor(robot.id / active.length) % items.length]];
    }
    decide(robot, sim) {
      if (sim.config.self_repair) {
        const broken = Object.keys(robot.built_defects).filter(p => robot.built_defects[p] > 0 && p !== this.body.vitamin);
        if (broken.length) {
          const part = broken[0];
          robot.role = "repair";
          if (g(robot.inventory, part) > 0) return ["repair", part];
          return this.workToward(robot, sim, { [part]: 1 }, false);
        }
      }
      const fab = this.fabTask(robot, sim);
      if (fab) { robot.role = "fab"; return this.workToward(robot, sim, fab, true); }
      const canRep = sim.canReplicate(robot);
      const job = this.contractTask(robot, sim, !canRep);
      if (job) {
        const [c, item] = job, want = Math.min(CONTRACT_CHUNK, g(sim.econ.remaining(c), item));
        robot.role = "contract";
        if (g(robot.inventory, item) >= want) return same(robot.pos, c.site) ? ["deliver_contract", c.id, item] : ["move", c.site];
        return this.workToward(robot, sim, { [item]: want }, false);
      }
      if (canRep) { robot.role = "replicate"; return this.workToward(robot, sim, this.body.robot_bom, false); }
      robot.role = "harvest";
      return this.decideWorker(robot, sim);
    }
    workToward(robot, sim, target, buildingFab) {
      const w = sim.world, inv = robot.inventory, reserve = robot.genome.energy_reserve;
      const [recipes, order] = this.recipesFor(sim, buildingFab);
      const net = this.net(robot, target, recipes, order);
      if (!Object.keys(target).some(p => g(net, p))) {
        if (target === this.body.robot_bom) return robot.energy >= this.body.assembly_energy + reserve ? ["assemble"] : ["wait"];
        if (target === this.kit) return same(robot.pos, w.depot) ? ["deliver_kit"] : ["move", w.depot];
        if (target === this.coreTarget) return same(robot.pos, w.depot) ? ["deliver_tooling"] : ["move", w.depot];
        return ["wait"];
      }
      for (const item in target)
        if (g(net, item) && this.importAvailable(sim, item, buildingFab) > 0)
          return same(robot.pos, w.depot) ? ["take_import", item] : ["move", w.depot];
      for (const item of order) {
        const rec = recipes[item];
        if (g(net, item) && Object.entries(rec.inputs).every(([i, q]) => g(inv, i) >= q)) {
          if (rec.at_fab) {
            if (!same(robot.pos, w.depot)) return ["move", w.depot];
            if (item === w.core && w.freeFab(sim.t) === null) w.tooling_waiting_until = sim.t + 2;
            if (w.freeFab(sim.t) === null || (item !== w.core && w.tooling_waiting_until > sim.t)) continue;
          }
          return robot.energy >= rec.energy + reserve ? ["start", rec] : ["wait"];
        }
      }
      const wanted = this.body.raw.filter(r => g(net, r));
      const src = this.nearestRawSource(robot, wanted, sim);
      if (src) {
        const [pos, rt, fromStock] = src;
        if (same(robot.pos, pos)) return fromStock ? ["take_stock", rt, g(net, rt)] : ["mine", rt];
        return ["move", pos];
      }
      if (wanted.length) { sim.stats.starved_feedstock = g(sim.stats, "starved_feedstock") + 1; return ["prospect", wanted[0]]; }
      sim.stats.waiting_on_chips = g(sim.stats, "waiting_on_chips") + 1;
      return ["wait"];
    }
    nearestRawSource(robot, wanted, sim) {
      const w = sim.world;
      let best = null;
      for (const r of wanted) {
        const dep = w.nearestDeposit(robot.pos, r);
        if (dep) { const d = dist(robot.pos, dep) + (this.body.everywhere.has(r) ? 3 : 0); if (!best || d < best[0]) best = [d, dep, r, false]; }
        if (g(w.stockpile, r) > 0) { const d = dist(robot.pos, w.depot); if (!best || d < best[0]) best = [d, w.depot, r, true]; }
      }
      return best ? best.slice(1) : null;
    }
    decideWorker(robot, sim) {
      const w = sim.world, raw = this.body.raw, cargo = raw.reduce((s, r) => s + g(robot.inventory, r), 0);
      if (cargo >= WORKER_CARGO) return same(robot.pos, w.depot) ? ["deliver"] : ["move", w.depot];
      const mineable = raw.filter(r => w.nearestDeposit(robot.pos, r));
      if (!mineable.length) return cargo && same(robot.pos, w.depot) ? ["deliver"] : ["wait"];
      let focus = sim.policy.harvest_focus;
      if (!mineable.includes(focus)) focus = mineable.reduce((a, b) => g(w.stockpile, a) <= g(w.stockpile, b) ? a : b);
      const dep = w.nearestDeposit(robot.pos, focus);
      return same(robot.pos, dep) ? ["mine", focus] : ["move", dep];
    }
  }

  // ---- directors ------------------------------------------------------------
  const FAB_BUILD_SOLS = 150, MIN_PAYBACK_SOLS = 400;
  class HeuristicDirector {
    constructor(strategy = "auto", targetPopulation = 150, totalTicks = 20000, body = MARS, robotsPerFab = null) {
      Object.assign(this, { strategy, targetPopulation, totalTicks, body, fabOrdered: false, lastStarved: 0 });
      this.robotsPerFab = robotsPerFab || body.robots_per_station;
      const st = body.station;
      this.vitamin = body.vitamin; this.core = st.core;
      this.kitVitamins = g(st.kit, body.vitamin); this.kitsRequired = st.kits_required;
      this.fatalShare = body.fatal_parts.size / Object.values(body.robot_bom).reduce((a, b) => a + b, 0);
    }
    chipsPipeline(s) {
      return s.chips_in_depot + g(s.manifest, this.vitamin) + s.in_transit.reduce((a, x) => a + g(x.items, this.vitamin), 0);
    }
    toolingPipeline(s) {
      return s.fab_tooling + g(s.imports, this.core) + g(s.manifest, this.core)
        + s.in_transit.reduce((a, x) => a + g(x.items, this.core), 0);
    }
    chipsStillNeeded(s) {
      const remaining = Math.max(0, this.totalTicks - s.t), growth = Math.max(0, this.targetPopulation - s.population);
      return growth + Math.floor(this.targetPopulation * (this.fatalShare / s.mtbf) * remaining);
    }
    downtimeCost(s) {
      const robotSols = Math.max(1, s.population) * Math.max(1, s.sol);
      const perRobotSol = g(s.ledger, "contract_revenue") / robotSols;
      const remainingSols = Math.floor(Math.max(0, this.totalTicks - s.t) / SOL_TICKS);
      const windowSols = (s.next_window_sol - s.sol) || s.window_period_sols;
      const missing = this.targetPopulation * (this.fatalShare / s.mtbf * SOL_TICKS) * windowSols / 2;
      return missing * remainingSols * perRobotSol;
    }
    wantFab(s) {
      if (this.strategy === "import") return false;
      if (!this.body.station.makes_vitamin) return true;
      if (this.strategy === "fab" || this.fabOrdered || s.fabs > 0) return true;
      const ready = s.t === 0 ? 0 : s.next_window_sol + s.transit_sols;
      if (Math.floor(this.totalTicks / SOL_TICKS) - (ready + FAB_BUILD_SOLS) < MIN_PAYBACK_SOLS) return false;
      return this.chipsStillNeeded(s) * s.landed[this.vitamin] + this.downtimeCost(s) > s.landed[this.core];
    }
    acceptContracts(s) {
      const pop = s.population, starving = s.starved_feedstock - this.lastStarved > pop * 25;
      this.lastStarved = s.starved_feedstock;
      const capacity = starving ? 0 : Math.max(1, Math.floor(pop / 5)) - s.active.length;
      const minSols = Math.min(120, s.window_period_sols * 2);
      return [...s.offers].sort((a, b) => b.payment - a.payment)
        .filter(c => Math.floor((c.deadline - s.t) / SOL_TICKS) >= minSols).slice(0, Math.max(0, capacity)).map(c => c.id);
    }
    decide(s) {
      if (!this.body.station.makes_vitamin) return this.decideVitaminImported(s);
      const pop = s.population, fab = this.wantFab(s), orders = {};
      if (fab && !this.fabOrdered && this.toolingPipeline(s) === 0) { orders[this.core] = 1; this.fabOrdered = true; }
      if (!fab || s.fabs === 0) {
        const need = Math.min(this.chipsStillNeeded(s), Math.max(0, this.targetPopulation - pop));
        const kitsLeft = fab && s.fabs === 0 ? Math.max(0, this.kitsRequired - s.fab_kits) : 0;
        const short = need + kitsLeft * this.kitVitamins - this.chipsPipeline(s);
        if (short > 0) orders[this.vitamin] = short;
      }
      const accept = this.acceptContracts(s);
      const kitsLeft = s.fabs === 0 ? Math.max(0, this.kitsRequired - s.fab_kits) : 0;
      const u = {
        replicate: pop < this.targetPopulation, build_fab: fab,
        target_fabs: fab ? Math.max(1, Math.floor(pop / this.robotsPerFab)) : 0,
        fab_share: s.fabs === 0 ? 0.5 : 0.25, chip_reserve: fab ? kitsLeft * this.kitVitamins : 0,
        contract_share: pop < this.targetPopulation ? 0.15 : 0,
      };
      if (Object.keys(orders).length) u.orders = orders;
      if (accept.length) u.accept_contracts = accept;
      return u;
    }
    decideVitaminImported(s) {
      const pop = s.population, fab = this.wantFab(s);
      const targetFabs = fab ? Math.max(1, Math.floor(pop / this.robotsPerFab)) : s.fabs;
      const orders = {};
      const horizon = (s.window_period_sols + s.transit_sols) * SOL_TICKS;
      const replacements = this.targetPopulation * this.fatalShare / s.mtbf * horizon;
      const growth = Math.min(Math.max(0, this.targetPopulation - pop), Math.max(3, pop));
      const kitsNeeded = Math.max(0, targetFabs * this.kitsRequired - s.fab_kits);
      const short = growth + Math.floor(replacements) + 1 + kitsNeeded * this.kitVitamins - this.chipsPipeline(s);
      if (short > 0) orders[this.vitamin] = short;
      if (!this.body.station.core_recipe) {
        const coresShort = targetFabs - this.toolingPipeline(s);
        if (coresShort > 0) orders[this.core] = coresShort;
      }
      const u = {
        replicate: pop < this.targetPopulation, build_fab: fab && targetFabs > s.fabs, target_fabs: targetFabs,
        fab_share: 0.25, chip_reserve: 0, contract_share: pop < this.targetPopulation ? 0.15 : 0,
      };
      if (Object.keys(orders).length) u.orders = orders;
      const accept = this.acceptContracts(s);
      if (accept.length) u.accept_contracts = accept;
      return u;
    }
  }

  // ---- simulation -------------------------------------------------------------
  const DEFAULT_CONFIG = {
    body: "mars", seed: 0, ticks: 20000, size: 48, seed_robots: 3, seed_chips: 20, chip_fab: null, dust_storms: true,
    defect_rate: 0.02, max_children: 3, max_population: 400, mtbf: null, self_repair: true,
    deposits_per_type: 12, deposit_amount: 5000, sample_every: 50, director_interval: 250,
    director_location: "onboard", earth_latency_ticks: null, skill_success: 1.0, skill_time_multiplier: 1.0,
    assembly_insertions: 4,
  };

  class Sim {
    constructor(config = {}, econ = {}, director = null) {
      this.config = { ...DEFAULT_CONFIG, ...config };
      const c = this.config, body = this.body = BODIES[c.body];
      if (c.mtbf == null) c.mtbf = body.mtbf;
      if (c.chip_fab == null) c.chip_fab = body.station_prebuilt;
      if (c.earth_latency_ticks == null) c.earth_latency_ticks = body.earth_latency_ticks;
      this.rng = rng(c.seed);
      this.world = new World(this.rng, c, body);
      this.econ = new Economy({ ...body.econ, ...econ }, rng(c.seed + 10000), c.size, body);
      this.controller = new Controller(body);
      this.director = director;
      this.policy = { replicate: true, build_fab: false, target_fabs: 1, fab_share: 0.5, make_tooling: true,
        chip_reserve: 0, contract_share: 0, harvest_focus: null };
      this.pending = []; this.stats = {}; this.history = []; this.events = [];
      this.robots = []; this.next_id = 0; this.t = 0;
      for (let i = 0; i < c.seed_robots; i++) this.spawn(this.world.depot, 0, null);
      this.population = this.robots.length;
    }
    log(kind, text) { this.events.push({ t: this.t, sol: Math.floor(this.t / SOL_TICKS), kind, text }); }
    stat(k, n = 1) { this.stats[k] = g(this.stats, k) + n; }
    spawn(pos, generation, parent, defects = {}, energy = 30, genome = null) {
      const r = { id: this.next_id++, pos: [...pos], generation, parent,
        genome: genome || { max_children: this.config.max_children, energy_reserve: 6, inspect_parts: true },
        energy, inventory: {}, hidden_defects: {}, built_defects: { ...defects }, children: 0, alive: true,
        job: null, travel_to: null, sleep_until: -1, born: this.t, role: "replicate" };
      this.robots.push(r);
      return r;
    }
    get alive() { return this.robots.filter(r => r.alive); }
    canReplicate(r) {
      if (!(this.policy.replicate ?? true)) return false;
      if (r.children >= r.genome.max_children) return false;
      return this.population < this.config.max_population;
    }
    inConjunction(t) {
      if (!this.body.conjunction_days) return false;
      const w = this.econ.window_ticks, mid = Math.floor(w / 2);
      return Math.abs((t % w) - mid) <= Math.floor(this.body.conjunction_days * SOL_TICKS / 2);
    }
    // Player / external commands: from Earth they wait out conjunction and light time.
    command(update, fromEarth = true) {
      let at = this.t + (fromEarth && this.t > 0 ? this.config.earth_latency_ticks : 0);
      if (fromEarth && this.inConjunction(at)) {
        while (this.inConjunction(at)) at++;
        this.log("comms", `Solar conjunction: command queued until sol ${Math.floor(at / SOL_TICKS)}`);
      }
      this.pending.push([at, update]);
      if (this.t === 0 || at <= this.t) this.flushPending();
    }
    flushPending() {
      const due = this.pending.filter(([at]) => at <= this.t);
      this.pending = this.pending.filter(([at]) => at > this.t);
      for (const [, u] of due) this.applyUpdate(u);
    }
    applyUpdate(update) {
      const u = { ...update };
      const orders = u.orders || {}, accept = u.accept_contracts || [];
      delete u.orders; delete u.accept_contracts;
      if (Object.keys(orders).length) this.econ.order(orders);
      for (const c of this.econ.accept(accept)) this.log("contract", `Accepted: ${label(c.kind)} for the ${c.client} (${money(c.payment)})`);
      Object.assign(this.policy, u);
    }
    directorStep() {
      const c = this.config;
      if (this.director && this.t % c.director_interval === 0) {
        const earth = c.director_location === "earth";
        if (earth && this.inConjunction(this.t)) this.stat("director_blackouts");
        else {
          const u = this.director.decide(this.snapshot());
          if (u) this.pending.push([this.t + (earth && this.t > 0 ? c.earth_latency_ticks : 0), u]);
        }
      }
      this.flushPending();
    }
    land(items) {
      const b = this.body;
      for (const [item, q] of Object.entries(items)) {
        if (item === "robot") { for (let i = 0; i < q; i++) this.spawn(this.world.depot, 0, null); this.stat("robots_imported", q); }
        else if (item === b.station_item) {
          this.world.fab_kits += q * this.world.kits_required; this.world.fab_tooling += q;
          this.stat("stations_imported", q); this.afterFabChange();
        }
        else { add(this.world.imports, item, q); this.stat(`imported_${item}`, q); }
      }
      if (this.world.installImportedTooling()) this.afterFabChange();
    }
    economyStep() {
      const e = this.econ, t = this.t, b = this.body;
      if (t === 0) {
        const first = { robot: this.config.seed_robots, [b.vitamin]: this.config.seed_chips };
        if (this.config.chip_fab) add(first, b.station_item, 1);
        const extra = { ...e.manifest }; e.manifest = {};
        for (const [k, v] of Object.entries(extra)) add(first, k, v);
        const cost = e.manifestCost(first, 0), hw = e.hardwareCost(first);
        e.book("earth_hardware", -hw); e.book("launch", -(cost - hw));
        this.log("landing", `First landing: ${describe(first, b)} (${money(cost)})`);
        this.land(extra);
      } else if (t % e.window_ticks === 0) {
        const s = e.launch(t);
        if (s) { this.stat("shipments"); this.log("launch", `Launch window: ${describe(s.items, b)} departs Earth (${money(s.cost)}), lands ${b.day} ${Math.floor(s.arrives / SOL_TICKS)}`); }
        else if (b.name === "mars") this.log("launch", "Launch window opened: nothing on the manifest");
      }
      for (const s of e.arrivals(t)) { this.land(s.items); this.log("landing", `Cargo landed: ${describe(s.items, b)}`); }
      const offers = e.maybeOffer(t);
      if (offers.length) this.stat("offers", offers.length);
      e.tick(t, (k, m) => this.log(k, m));
    }
    step() {
      this.directorStep();
      this.economyStep();
      if (this.t % this.config.sample_every === 0) this.sample();
      this.world.tick(this.t, (k, m) => this.log(k, m));
      const order = this.alive;
      this.population = order.length;
      for (let i = order.length - 1; i > 0; i--) { const j = Math.floor(this.rng() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
      for (const r of order) this.tickRobot(r);
      this.t++;
    }
    tickRobot(r) {
      if (g(r.built_defects, this.body.vitamin)) { r.alive = false; this.stat("duds"); return; }
      if (this.rng() < 1 / this.config.mtbf) { this.componentFailure(r); if (!r.alive) return; }
      const solar = SOLAR_OUTPUT * (g(r.built_defects, "solar_panel") ? 0.4 : 1);
      r.energy = Math.min(BATTERY_CAPACITY, r.energy + solar * this.world.solarFactor(this.t));
      const n = Object.entries(r.built_defects).reduce((s, [k, v]) => s + (k === this.body.vitamin ? 0 : v), 0);
      if (this.rng() > Math.max(0.1, 1 - 0.15 * n)) { this.stat("lost_ticks"); return; }
      if (r.job) { if (--r.job.remaining <= 0) this.finishJob(r); return; }
      if (r.travel_to) {
        if (!same(r.pos, r.travel_to)) { this.execute(r, ["move", r.travel_to]); return; }
        r.travel_to = null;
      }
      if (r.sleep_until > this.t) return;
      const a = this.controller.decide(r, this);
      if (a[0] === "move") r.travel_to = a[1];
      else if (a[0] === "wait") r.sleep_until = this.t + BLOCKED_SLEEP_TICKS;
      this.execute(r, a);
    }
    takeParts(r, item, qty) {
      let bad = 0;
      for (let i = 0; i < qty; i++) {
        const total = g(r.inventory, item);
        if (g(r.hidden_defects, item) && this.rng() < r.hidden_defects[item] / total) { r.hidden_defects[item]--; bad++; }
        r.inventory[item] = total - 1;
      }
      return bad;
    }
    skillTicks(ticks, kind) { return kind === "refine" ? ticks : Math.max(1, Math.round(ticks * this.config.skill_time_multiplier)); }
    manipulationFailed(insertions = 1) {
      const p = Math.pow(this.config.skill_success, insertions);
      if (p < 1 && this.rng() > p) { this.stat("manipulation_failures"); return true; }
      return false;
    }
    execute(r, a) {
      const w = this.world, kind = a[0], b = this.body;
      if (kind === "move") { if (r.energy >= MOVE_COST) { r.energy -= MOVE_COST; r.pos = stepToward(r.pos, a[1]); } }
      else if (kind === "mine") { if (r.energy >= MINE_COST) { r.energy -= MINE_COST; add(r.inventory, a[1], w.mine(r.pos, a[1], b.mine_units_per_tick)); } }
      else if (kind === "take_stock") { const got = Math.min(a[2], g(w.stockpile, a[1])); w.stockpile[a[1]] -= got; add(r.inventory, a[1], got); }
      else if (kind === "take_import") { if (g(w.imports, a[1]) > 0) { w.imports[a[1]]--; add(r.inventory, a[1], 1); this.stat(`used_imported_${a[1]}`); } }
      else if (kind === "prospect") {
        if (r.energy >= MOVE_COST) {
          r.energy -= MOVE_COST;
          const [dx, dy] = this.rng.choice([[1, 0], [-1, 0], [0, 1], [0, -1]]);
          const p = [r.pos[0] + dx, r.pos[1] + dy];
          if (w.inBounds(p)) r.pos = p;
          this.stat("prospect_ticks");
          if (this.rng() < PROSPECT_FIND_CHANCE) {
            const at = w.prospect(a[1], this.config.deposit_amount);
            this.stat(`veins_found_${a[1]}`);
            this.log("prospect", `Prospectors found new ${a[1]} at ${at[0]},${at[1]}`);
          }
        }
      }
      else if (kind === "deliver") {
        for (const raw of b.raw) { const n = g(r.inventory, raw); if (n) { add(w.stockpile, raw, n); this.stat("harvested", n); r.inventory[raw] = 0; } }
      }
      else if (kind === "deliver_contract") {
        const c = this.econ.active().find(x => x.id === a[1]);
        if (c) {
          const n = this.econ.deliver(c, a[2], g(r.inventory, a[2]));
          this.takeParts(r, a[2], n);
          this.stat("contract_units", n);
          if (c.status === "done") { this.stat("contracts_done"); this.log("contract-good", `Delivered: ${label(c.kind)} for the ${c.client}, paid ${money(c.payment)}`); }
        }
      }
      else if (kind === "deliver_kit") {
        if (w.n_fabs >= (this.policy.target_fabs ?? 1)) return;
        for (const [item, q] of Object.entries(b.station.kit)) this.takeParts(r, item, q);
        w.fab_kits++; this.stat("fab_kits");
        this.afterFabChange();
      }
      else if (kind === "deliver_tooling") {
        this.takeParts(r, w.core, 1); w.fab_tooling++; this.stat("cores_made_locally");
        this.log("fab", `${b.label(w.core)} made locally: a ${b.station.name} built part of another`);
        this.afterFabChange();
      }
      else if (kind === "start") {
        const rec = a[1];
        if (rec.at_fab) w.fab_busy[w.freeFab(this.t)] = this.t + rec.ticks;
        r.energy -= rec.energy;
        for (const [i, q] of Object.entries(rec.inputs)) this.takeParts(r, i, q);
        r.job = { kind: rec.kind, output: rec.output, remaining: this.skillTicks(rec.ticks, rec.kind), recipe: rec };
      }
      else if (kind === "repair") {
        const bad = this.takeParts(r, a[1], 1);
        r.job = { kind: "repair", output: a[1], remaining: REPAIR_TICKS, bad };
      }
      else if (kind === "assemble") {
        r.energy -= b.assembly_energy + CHILD_ENERGY;
        const defects = {};
        for (const [p, q] of Object.entries(b.robot_bom)) { const bad = this.takeParts(r, p, q); if (bad) defects[p] = bad; }
        r.job = { kind: "assemble", output: "robot", remaining: this.skillTicks(b.assembly_ticks, "assemble"), defects };
      }
    }
    afterFabChange() {
      const w = this.world;
      w.installImportedTooling(); w.syncFabs();
      if (w.n_fabs > g(this.stats, "fabs_built")) {
        const first = this.stats.fabs_built === undefined;
        this.stats.fabs_built = w.n_fabs;
        if (this.t > 0 && this.stats.first_fab_at === undefined) this.stats.first_fab_at = this.t;
        if (!first || this.t > 0) this.log("fab", `${this.body.station.name} #${w.n_fabs} online`);
      }
    }
    componentFailure(r) {
      const part = this.rng.choice(this.body.parts);
      this.stat("component_failures");
      if (this.body.fatal_parts.has(part)) { r.alive = false; this.stat("failures"); }
      else add(r.built_defects, part, 1);
    }
    finishJob(r) {
      const job = r.job, b = this.body; r.job = null;
      if (job.kind === "repair") {
        if (job.bad) this.stat("repairs_failed"); else { r.built_defects[job.output]--; this.stat("repairs"); }
        return;
      }
      if (job.kind === "assemble") {
        if (this.manipulationFailed(this.config.assembly_insertions)) {
          const lost = this.rng.choice(b.parts);
          for (const [p, q] of Object.entries(b.robot_bom)) add(r.inventory, p, q - (p === lost ? 1 : 0));
          return;
        }
        this.spawn(r.pos, r.generation + 1, r.id, job.defects, CHILD_ENERGY, { ...r.genome });
        r.children++; this.stat("births");
        if (Object.keys(job.defects).length) this.stat("defective_births");
        return;
      }
      if (job.kind === "fabricate" && this.manipulationFailed()) return;
      if (job.output === b.vitamin) {
        this.stat("chips_made");
        if (this.stats.chips_made === 1) this.log("fab", `First ${b.label(b.vitamin)} made on ${b.place}`);
      }
      if (job.recipe.at_fab) {
        this.stat(`made_${job.output}`);
        for (const [item, q] of Object.entries(job.recipe.byproducts)) this.stat(`made_${item}`, q);
      }
      for (const [item, q] of Object.entries(job.recipe.byproducts)) add(r.inventory, item, q);
      if (job.kind === "fabricate" && this.rng() < defectProbability(job.recipe, this.config.defect_rate)) {
        this.stat("defects_made");
        if (r.genome.inspect_parts && !g(r.built_defects, "sensor") && this.rng() < INSPECTION_DETECT_RATE) { this.stat("defects_rejected"); return; }
        add(r.hidden_defects, job.output, 1);
      }
      add(r.inventory, job.output, 1);
    }
    fleetValue() { return this.alive.length * this.econ.landedCost("robot", this.t); }
    snapshot() {
      const alive = this.alive, e = this.econ, w = this.world, t = this.t, b = this.body;
      return {
        t, sol: Math.floor(t / SOL_TICKS), population: alive.length, mtbf: this.config.mtbf,
        chips_in_depot: g(w.imports, b.vitamin), imports: { ...w.imports }, chips_made: g(this.stats, "chips_made"),
        fabs: w.n_fabs, fab_kits: w.fab_kits, fab_tooling: w.fab_tooling, starved_feedstock: g(this.stats, "starved_feedstock"),
        cash: e.cash, ledger: { ...e.ledger }, manifest: { ...e.manifest },
        in_transit: e.in_transit.map(s => ({ items: s.items, arrives: s.arrives })),
        landed: Object.fromEntries(Object.keys(b.catalog).map(k => [k, e.landedCost(k, t)])),
        next_window_sol: Math.floor(e.nextWindow(t) / SOL_TICKS), transit_sols: e.cfg.transit_sols,
        window_period_sols: e.cfg.window_period_sols,
        offers: e.offers(), active: e.active(),
      };
    }
    sample() {
      const alive = this.alive;
      this.history.push({
        sol: Math.floor(this.t / SOL_TICKS), population: alive.length, fabs: this.world.n_fabs,
        chips_made: g(this.stats, "chips_made"), oxygen_t: g(this.stats, "made_oxygen") * this.body.kg_per_unit / 1000,
        cash: this.econ.cash, revenue: g(this.econ.ledger, "contract_revenue"),
        chips_in_depot: g(this.world.imports, this.body.vitamin), fleet_value: this.fleetValue(),
      });
    }
  }

  const describe = (items, body = MARS) => Object.entries(items).filter(([, q]) => q > 0)
    .map(([k, q]) => {
      const name = body.label(k);
      return `${q} ${name}${q > 1 && !/s$|tooling$|oxygen$|regolith$/.test(name) ? "s" : ""}`;
    }).join(", ") || "nothing";

  return { Sim, HeuristicDirector, BODIES, SOL_TICKS, money, label, describe };
})();

if (typeof module !== "undefined") module.exports = VN;
