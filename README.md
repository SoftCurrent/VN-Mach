# VN-Mach

A simulator for a self-replicating robot business on Mars. Robots mine
Martian feedstock, refine it, fabricate parts, assemble copies of
themselves, repair themselves, bootstrap and replicate their own chip fabs,
and sell what they make to other Mars operators. Every part, launch and
sol of mission control is costed. AI planners, including Claude, run the
colony on top of a scripted skill layer. A MuJoCo arm benchmark measures
what the manipulation underneath can actually do.

```
pip install -e '.[dev]'          # add ,claude for the Claude director; mujoco for the arm
python -m vnmach closure         # how self-sufficient is the robot design?
python -m vnmach run --map       # one colony, 'auto' director
python -m vnmach compare         # none / import / fab / auto over seeds
python -m vnmach econ            # launch-cost sweep: ship chips vs build fabs
python -m vnmach run --director claude --ticks 4000   # needs Anthropic API credentials
python -m vnmach.arm bench       # manipulation benchmark (MuJoCo)
pytest
```

Time: 10 ticks = 1 sol. Default mission 2000 sols (~2.5 launch windows).

## Layers

| Layer | File | What it does |
|---|---|---|
| Bill of materials | `vnmach/bom.py` | Mars feedstocks (hematite, basalt, ice, CO₂) → materials → parts → robot. Fab kits, fab tooling, propellant. Closure analysis. |
| World | `vnmach/world.py` | Ore/ice veins (finite, more found by prospecting), basalt + CO₂ everywhere, sols, dust storms, base with imports depot and fabs. |
| Economy | `vnmach/econ.py` | Earth catalog (price + mass), landed cost with falling $/kg, launch windows every 780 sols + 200-sol transit, contracts, ops cost, cash ledger. |
| Robot | `vnmach/robot.py` | Energy, inventory, inherited genome (replication governor), hidden defects, broken parts. |
| Skills (fast loop) | `vnmach/controller.py` | Per-tick roles: repair → fab builder → contractor → replicator → worker. MRP netting, then mine / refine / fabricate / assemble / deliver / prospect. |
| Manipulation | `vnmach/arm/` | MuJoCo peg-in-hole arm: scripted, learned, teleoperated-with-delay, supervisory controllers. Feeds `skill_success` / `skill_time_multiplier` into the colony. |
| Planner (slow loop) | `vnmach/director.py`, `vnmach/llm.py` | Every 25 sols: replicate?, build fabs?, how many?, chip reserve, contract share, **Earth orders**, **contract acceptance**. Heuristic (`import` / `fab` / `auto`) or Claude. |

The planner never drives motors, and its output is clamped (`sanitize`).
An Earth-hosted planner (`--director-location earth`, the default for
Claude) is blacked out for 14 sols at each solar conjunction, and its
decisions arrive with latency. If it fails or refuses, the current policy
stays in place.

## Economics

Assumptions live in `econ.py`. Defaults: $5,000/kg landed, falling 15% per
window to a $500/kg floor; robot electronics module ("chip") $100k + 20 kg;
whole robot $2M + 2.5 t; fab tooling $40M + 8 t; $10M fixed per shipment;
$50k/sol mission control. Contracts (landing pads, habitat shells,
propellant, solar farms, ice) pay 50% of what shipping the same mass from
Earth would cost, so **cheaper launch makes Mars-made goods worth less**.

A fab = 4 kits built on Mars (each uses one chip) + 1 tooling set. The first
tooling must come from Earth. After that, a working fab can make tooling,
so factories replicate.

Strategies:
- `import`: never build a fab; order chips every window.
- `fab`: land tooling with the first landing.
- `auto`: import while cheaper. Switch when (chips still needed × landed
  chip cost + revenue lost waiting for windows) > landed tooling cost, and
  only if the fab has time to pay back.

### Results (2 seeds each)

Final cash, $M, 2000 sols:

| $/kg landed | import | fab | auto |
|---|---|---|---|
| 20,000 | 46,257 | 48,772 | **48,909** |
| 5,000 | 11,895 | 12,507 | **12,536** |
| 1,000 | 2,732 | 2,836 | **2,837** |
| 250 | 1,760 | 1,797 | **1,808** |

Mission length at $5,000/kg:

| sols | import | fab | auto |
|---|---|---|---|
| 800 | 4,130 | **4,204** | 4,130 (stays on imports) |
| 2000 | 11,895 | 12,507 | **12,536** (fab at sol ~1145) |
| 4000 | 22,410 | **35,009** | **35,009** (fab at t=0) |

What this says:
1. **Shipping chips is right early; a fab wins as the mission gets longer.**
   `auto` imports first and switches around sol 1145 on a 2000-sol mission.
   Over 4000 sols, fabs are worth +56% cash, because imports only arrive
   once per 780-sol window, and robots that die between windows aren't
   earning.
2. **Launch cost moves revenue far more than strategy.** Contract prices are
   pegged to launch cost, so at $250/kg everything Mars-made is worth ~25×
   less than at $20,000/kg.
3. **One fab gives linear growth.** Fabs must make tooling for more fabs,
   and tooling has to get priority on the fab or chip demand starves it.
4. **Local feedstock runs out.** Without prospecting, the ice veins ran dry
   around sol 2000 and the colony collapsed (with $750M of contract
   penalties). Prospecting finds veins progressively further out.
5. **Chips are the only vitamin** (99% closure by mass), **a single seed
   robot is a single point of failure**, **self-repair keeps a long-lived
   fleet at ~100% efficiency** (vs ~55% without), and **replication governors
   must be protected from copy errors**.

The `auto` rule isn't optimal: on the 800-sol mission, landing tooling at
t=0 beats it by ~2%. Whether Claude can beat these rules is what
`compare --directors auto,claude` is for.
