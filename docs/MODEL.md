# VN-Mach model reference

Every formula and number the simulator uses, with the file and name to
edit. All values are assumptions unless marked *measured*.

The browser UI runs a JavaScript copy of the same model (`ui/engine.js`).
If you change a number in the Python files, change it in `ui/engine.js`
too, then run `python ui/build.py`.

## 1. Time and units

| Quantity | Value | Where |
|---|---|---|
| Tick | 1/10 sol (~2.5 h) | `world.SOL_TICKS = 10` |
| Default mission | 20,000 ticks = 2,000 sols | `sim.Config.ticks` |
| Mass unit | 1 inventory unit = 100 kg | `econ.KG_PER_UNIT` |
| Energy | abstract units; a robot battery holds 60 | `robot.BATTERY_CAPACITY` |
| Map | 48 × 48 cells (the UI labels them 1 km) | `sim.Config.size` |

## 2. Robot design (bill of materials)

One robot (`bom.ROBOT_BOM`): 1 frame, 4 motors, 2 wiring, 1 solar panel,
1 battery, 1 sensor, 1 chip. That's 11 part units.

Assembly takes 20 ticks and 10 energy (`ASSEMBLY_TICKS`, `ASSEMBLY_ENERGY`).
The parent also hands the child 15 energy (`robot.CHILD_ENERGY`).

### Recipes (`bom.default_recipes`)

Feedstocks: hematite and ice come from finite deposits. Basalt and CO₂ can
be harvested on any cell.

| Output | Inputs | Energy | Ticks | Kind | Precision | Mass (units) |
|---|---|---|---|---|---|---|
| steel | 2 hematite, 1 ice, 1 carbon | 6 | 4 | refine | 0 | 1 |
| aluminium | 3 basalt | 10 | 5 | refine | 0 | 1 |
| silicon | 2 basalt | 8 | 4 | refine | 0 | 1 |
| glass | 1 basalt | 3 | 2 | refine | 0 | 1 |
| carbon | 2 CO₂ | 4 | 3 | refine | 0 | 1 |
| polymer | 1 carbon, 1 ice | 4 | 3 | refine | 0 | 1 |
| propellant | 2 CO₂, 2 ice | 8 | 4 | refine | 0 | 1 |
| frame | 2 steel, 2 aluminium | 4 | 8 | fabricate | 0.1 | 8 |
| motor | 1 steel, 1 aluminium | 3 | 6 | fabricate | 0.4 | 2 |
| wiring | 1 aluminium, 1 polymer | 1 | 3 | fabricate | 0.2 | 1 |
| solar panel | 2 silicon, 1 glass | 5 | 8 | fabricate | 0.5 | 3 |
| battery | 1 aluminium, 1 polymer, 1 steel | 3 | 5 | fabricate | 0.3 | 3 |
| sensor | 1 silicon, 1 glass, 1 aluminium | 3 | 6 | fabricate | 0.6 | 0.5 |
| chip *(needs a fab)* | 4 silicon, 1 aluminium | 30 | 40 | fabricate, at fab | 0.9 | 0.2 |
| fab tooling *(needs a fab)* | 12 steel, 8 aluminium, 10 silicon, 6 glass, 4 sensor, 4 motor, 2 chip | 40 | 400 | fabricate, at fab | 0.7 | 80 |

Each job's energy must be at most 54 (battery 60 minus the 6 reserve), or
no robot can ever start it. A test enforces this.

### Derived per robot (`python -m vnmach closure`)

| | Without a fab | With a fab |
|---|---|---|
| Closure by part count | 10/11 = 90.9% | 100% |
| Closure by mass | 99.2% | 100% |
| Vitamins | 1 chip | none |
| Feedstock | 14 hematite, 10 ice, 20 CO₂, 38 basalt | + 11 basalt for the chip |
| Energy | 263 | 335 |
| Serial build time | 210 ticks (21 sols) | 271 ticks |

The formulas:

- closure by mass = Σ(mass × qty of locally makeable parts) ÷ Σ(mass × qty of all parts)
- feedstock, energy and time come from expanding every input recursively; time is the sum of all job ticks done serially by one robot

## 3. Chip fabs

- **What a fab is:** 4 fab kits plus 1 set of tooling. `n_fabs = min(floor(kits ÷ 4), tooling_sets)`.
- **Fab kit** (`bom.FAB_KIT`): 4 steel, 3 glass, 3 aluminium, 2 sensors, 2 motors, 1 chip. Robots build kits and deliver them to the base.
- **Tooling:** the first set has to be imported from Earth. After that, a working fab can make tooling (recipe above), so fabs can replicate.
- **Throughput:** each fab runs one job at a time. Waiting tooling jobs take priority over chip jobs.

## 4. Robot physics per tick (`sim.tick_robot`)

Every tick, each live robot goes through these steps in order:

1. **Dud check:** a robot with a defective chip dies immediately (dead on arrival).
2. **Wear:** with probability `1 / mtbf` (mtbf = 4,000 ticks), one random part unit fails, chosen uniformly from the 11.
   - If it's the chip or the frame, the robot dies.
   - Otherwise that part counts as broken.
   - So the fatal rate is (2/11) ÷ 4000 per tick: one death per 22,000 robot-ticks, or 2,200 robot-sols.
3. **Recharge:** `energy = min(60, energy + solar)`, where
   - `solar = 1.0 × max(0, sin(2π·phase)) × π × (0.25 in a dust storm) × (0.4 if the solar panel is broken)`
   - `phase = (t mod 10) / 10`. This averages 1.0 per tick over a clear sol and is zero at night.
4. **Efficiency:** the robot loses the tick with probability `1 − efficiency`, where `efficiency = max(0.1, 1 − 0.15 × broken non-chip parts)`.
5. **Act:** advance the current job, or keep walking to its destination, or choose a new action.

### Action costs

| Action | Cost | Effect |
|---|---|---|
| Move one cell | 0.3 energy | Walks along x first, then y |
| Mine | 0.5 energy | 1 unit per tick |
| Prospect | 0.3 energy | Random step; finds a new vein with p = 1/150 per tick |
| Repair | 10 ticks | Swaps in one replacement part |
| Blocked | none | Sleeps 3 ticks before choosing again |

When a robot stands on a basalt or CO₂ cell, that source counts as 3 cells
further away than it is when choosing where to mine. This stands in for
how slow in-place harvesting is.

### Starting energy and reserve

A robot starts with 30 energy. It won't start a job unless
`energy ≥ job energy + 6` (the energy reserve).

### Defects (`bom.defect_probability`)

- `P(defect) = min(0.95, 0.02 × (1 + 9 × precision))`. Base rate: `Config.defect_rate = 0.02`.
  - motor: 0.02 × 4.6 = 9.2%
  - sensor: 12.8%
  - chip: 18.2%
- **Inspection:** a robot with a working sensor rejects a defective part with probability 0.9. Rejected parts are scrapped. Undetected bad parts stay hidden until used.
- When parts are consumed, each one is defective with probability `hidden_bad ÷ held`. Defective parts built into a child become broken parts on that child.

### Manipulation skill (connects to the arm benchmark)

- A fabricate job fails with probability `1 − skill_success`. The inputs are lost.
- An assembly fails with probability `1 − skill_success⁴` (`assembly_insertions = 4`). One random part is destroyed and the rest go back to inventory.
- Fabricate and assemble times are multiplied by `skill_time_multiplier`. Refining isn't affected.

### Replication and genome

- **Governor:** each robot builds at most 3 children (`max_children`). Population is capped at 400 (`max_population`).
- **Copy errors:** a child's genome mutates with probability `mutation_rate` (default 0). A mutation picks one field:
  - energy reserve × uniform(0.6, 1.4)
  - flip the inspect-parts setting
  - only if `governor_protected = False`: max children +(−1, +1 or +2)

## 5. World (`world.py`, `sim.Config`)

### Deposits

- **Initial veins:** 3 hematite veins and 3 ice veins at random places. Each vein is 4 cells within ±2 of its centre, with 5,000 units per cell (`Config.deposit_amount`).
  - This 5,000 overrides the 2,000 default in `World`.
- **Prospecting:** the *n*-th new vein of a type is placed within `min(21, 6 + 3n)` cells of the base, and at least half that distance away. Finds get further out each time.

### Dust storms and conjunction

- **Dust storms:** start with probability 1/3000 per tick (about every 300 sols), last 150–600 ticks (15–60 sols), and cut sunlight to 25%.
- **Solar conjunction:** a 14-sol comms blackout centred midway between launch windows. It only affects a director hosted on Earth.

## 6. Economy (`econ.py`)

### Earth catalog

Landed cost = price + mass × launch $/kg. The masses come from the BOM
mass × 100 kg.

| Item | Price | Mass | Landed at $5,000/kg |
|---|---|---|---|
| chip (electronics module) | $100,000 | 20 kg | $200,000 |
| motor | $20,000 | 200 kg | $1.02M |
| sensor | $30,000 | 50 kg | $280,000 |
| solar panel | $15,000 | 300 kg | $1.52M |
| battery | $25,000 | 300 kg | $1.53M |
| wiring | $2,000 | 100 kg | $502,000 |
| frame | $40,000 | 800 kg | $4.04M |
| robot (complete) | $2,000,000 | 2,470 kg | $14.35M |
| fab tooling | $40,000,000 | 8,000 kg | $80M |

### Formulas

- **Launch cost:** `launch $/kg(t) = max(500, 5000 × (1 − 0.15)^floor(t / window))`, where a window is 780 sols. The 15% is `launch_cost_decline`; $500 is the floor.
- **Shipment cost:** `$10M fixed + Σ landed cost × quantity`.
  - Orders fly only at a launch window (every 780 sols) and land 200 sols later.
  - Orders placed at t = 0 land immediately with the first landing.
  - If cash runs short, units of the most expensive line are dropped until the shipment is affordable.
- **Booking:** the hardware price goes to the ledger as `earth_hardware`; the rest goes to `launch`.
- **Operations:** $50,000 per sol.
- **Starting cash:** $600M.
- **First landing** (default): 3 robots + 20 chips = 3 × 14.35M + 20 × 0.2M + 10M = **$57.05M**.

### Contracts

- **Offers:** a new batch every 20 sols. Batch size = `2 + floor(U(0,1) + 4 × t / window)`, so demand grows with every window.
- **Price:** `payment = 0.5 × mass_kg × launch $/kg(t) × U(0.8, 1.2)`. Clients pay half of what shipping the same mass from Earth would cost them.
- **Expiry:** offers expire after 30 sols. Missing an accepted contract's deadline costs 10% of its payment. Payment arrives only when everything is delivered.
- **Delivery:** robots carry at most 10 units per trip (`controller.CONTRACT_CHUNK`).

| Contract | Client | Goods | Mass | Deadline | Pay at $5,000/kg (before ±20%) |
|---|---|---|---|---|---|
| landing pad | launch provider | 60 glass, 20 steel | 8 t | 300 sols | $20M |
| habitat shell | space agency | 30 steel, 25 aluminium, 15 glass | 7 t | 400 sols | $17.5M |
| propellant | return-flight operator | 120 propellant | 12 t | 250 sols | $30M |
| solar farm | research station | 20 solar panels, 15 wiring | 7.5 t | 350 sols | $18.75M |
| ice delivery | life-support contractor | 200 ice | 20 t | 150 sols | $50M |

### Scores

- **Cash** = starting cash + all ledger entries (main score).
- **Fleet value** = live robots × landed robot cost today.
- **Net value** = cash + fleet value.

## 7. Robot roles (`controller.py`)

Each tick a robot takes the first role that applies:

1. **Repair:** it has a broken part.
2. **Fab crew:** chosen if `bucket(id, 1) < fab_share` and fabs < target. It builds kits, or tooling once kits are complete and a fab exists.
3. **Contract work:** chosen if `bucket(id, 2) < contract_share`. Any robot that can't replicate also does contract work when contracts are active.
4. **Replicate:** build a child.
5. **Harvest:** dig feedstock into the stockpile, 10 units per trip.

`bucket(id, salt) = ((id × 7919 + salt × 104729) mod 1000) / 1000`, a
fixed per-robot number between 0 and 1.

**How a robot works toward a goal:**

1. **Netting:** it works out what's missing, top-down through the recipes, after subtracting what it already holds (MRP-style netting).
2. **Choosing the next step**, in priority order:
   - use an imported item at the base
   - fabricate or refine anything whose inputs it has
   - fetch feedstock from the nearest deposit or the stockpile
   - prospect, if no source exists
   - otherwise wait

`chip_reserve` imported chips can be taken only by fab crew.

## 8. Heuristic directors (`director.py`)

These run every 250 ticks (25 sols), on Mars, so they have no blackout.
They assume a target population of 150.

### Earth-supply rules

- **chips still needed** = `max(0, 150 − population) + floor(150 × (2/11/4000) × ticks remaining)`.
- **downtime cost** = `150 × fatal_per_sol × window_sols / 2 × sols remaining × revenue per robot-sol`. It estimates revenue lost to robots that died and are waiting for the next shipment.
  - `fatal_per_sol = 2/11/4000 × 10`
  - `window_sols` = sols until the next window, or 780
  - `revenue per robot-sol = revenue so far ÷ (population × sols elapsed)`. This is crude: it uses today's population for the whole history.
- **Auto builds a fab when both hold:**
  - `chips still needed × landed chip + downtime cost > landed fab tooling`
  - `total sols − (ready sol + 150) ≥ 400` (payback). `ready sol` is 0 at t = 0, otherwise next window + 200 sols of transit.
- **The three strategies:**
  - `import` never builds a fab.
  - `fab` builds from t = 0.
  - Once a fab is wanted, the director orders 1 tooling set.
- **Chip orders:** while there's no fab, it orders `max(0, 150 − population) + (kits left × 1) − chips already in the pipeline (at the base, on the manifest, in transit)`.

### Colony policy rules

- **Policy each decision:**
  - replicate while population < 150
  - target fabs = `max(1, floor(population / 25))`
  - fab share 0.5 until the first fab, then 0.25
  - chip reserve = kits still needed (before the first fab)
  - contract share 0.15 while growing
- **Contracts:** the director accepts offers with at least 120 sols left, highest pay first, up to `floor(population / 5)` active contracts.
  - It accepts none while the colony is short of feedstock. "Short" means more than 25 × population feedstock-starved robot-ticks since the last decision.

## 9. Robot arm (`vnmach/arm/`, MuJoCo)

### Setup

- **Arm:** 4 joints (yaw, shoulder, elbow, wrist pitch). Joint torques are 10, 20, 15 and 5 N·m at full command.
- **Simulation:** 1 ms physics step, 100 Hz control, gravity 3.71 m/s².
- **Part and socket:** the peg has a 10 mm radius and is fixed to the wrist. The square socket has a 0.5 mm chamfer and is 35 mm deep.
- **Outcomes:**
  - Success: the peg tip is 25 mm below the rim.
  - Overforce: more than 40 N for 30 ms.
  - Jam: engaged, more than 15 N, and less than 0.5 mm of progress in 2 s.
  - Time limit: 60 s per attempt.

### Conditions

| Condition | Clearance | Camera error | Motor torque |
|---|---|---|---|
| clean | 0.5 mm | 0.5 mm | 100% |
| dusty | 0.5 mm | 3 mm | 100% |
| worn | 0.5 mm | 0.5 mm | 25% |
| tight | 0.1 mm | 0.5 mm | 100% |
| harsh | 0.1 mm | 3 mm | 25% |

### Controllers

- **Scripted:** inverse kinematics plus PD control, push with limited force, then a spiral search when the peg hits the rim.
- **Learned:** the cross-entropy method (CEM) tunes 11 skill settings plus a 3 × 14 linear correction, bounded to ±2 mm × tanh.
  - Training: 20 iterations, population 24, 6 elite, 8 episodes per candidate.
  - Training conditions are random: clearance 0.1–0.6 mm, camera error 0–3 mm, torque 25–100%.
- **Teleop:** a human-like operator with 0.3 s reaction time and 0.3 mm visual error, seeing everything delayed.
- **Supervisory:** Earth sends a goal and the onboard skill does the work, so delay only adds latency.

### Measured results

Success over 30 trials. Time multiplier = time per attempt ÷ 3.91 s (the
scripted clean average).

| Controller | Condition | Seats part | Time × | Robot assembly (success⁴) |
|---|---|---|---|---|
| scripted | clean | 100% | 1.00 | 100% |
| scripted | dusty | 100% | 1.95 | 100% |
| scripted | worn | 100% | 2.12 | 100% |
| scripted | tight | 96.7% | 1.82 | 87% |
| scripted | harsh | 36.7% | 10.36 | 1.8% |
| learned | clean | 100% | 0.97 | 100% |
| learned | dusty | 100% | 2.02 | 100% |
| learned | worn | 100% | 0.84 | 100% |
| learned | tight | 93.3% | 3.62 | 76% |
| learned | harsh | 90.0% | 3.11 | 66% |
| teleop, 0 s | clean | 96.7% | 3.33 | 87% |
| teleop, 0.5 s | clean | 86.7% | 9.53 | 56% |
| teleop, 2 s | clean | 0% | 15.33 | 0% |

## 10. Numbers most worth challenging

These drive the results most, and I'm least sure of them:

1. **Fab tooling: $40M, 8 t.** A modern chip fab costs billions. This
   assumes a small, older-process fab making simple controller chips. If
   tooling costs 10× more, importing chips may win; try it with
   `EARTH_PRICES` / `default_catalog`.
2. **Chip: $100k, 20 kg.** This covers a robot's whole electronics module.
3. **Contract demand and price** (`mars_price_ratio`, `demand_growth_per_window`,
   offer interval). Revenue dominates every outcome, and nobody knows what
   the Mars market will be. Pegging prices to launch cost is my
   assumption.
4. **Part masses via 100 kg/unit.** A 200 kg motor and an 800 kg frame make
   a 2.5 t construction robot. A lighter robot makes Earth imports cheaper.
5. **Fatal failure rate** (mtbf 4,000 ticks × 2/11). This sets how often
   robots need new chips, which is the main argument for a fab.
6. **Energy units** are relative, not kWh. Aluminium (10) and chips (30)
   are set to be the expensive steps.
7. **Launch:** $5,000/kg, falling 15% per window to a $500 floor.
