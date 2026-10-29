# VN-Mach

A simulator for a self-replicating robot colony on Mars: robots that mine
Martian feedstock, refine it, fabricate parts, assemble copies of
themselves, repair themselves, and bootstrap their own chip fabs. AI
planners, including Claude, run the colony.

```
pip install -e '.[dev]'          # add ,claude for the Claude director
python -m vnmach closure         # how self-sufficient is the robot design?
python -m vnmach run --map       # one colony, heuristic director
python -m vnmach compare --seeds 6
python -m vnmach run --director claude --ticks 4000   # needs Anthropic API credentials
pytest
```

## Model

| Layer | File | What it does |
|---|---|---|
| Bill of materials | `vnmach/bom.py` | Mars feedstocks (hematite, basalt, ice, CO₂) → refined materials → parts → robot. Closure analysis. |
| World | `vnmach/world.py` | Grid with ore/ice deposits, basalt + CO₂ everywhere, sols, dust storms, the base with chip fabs. |
| Robot | `vnmach/robot.py` | Energy, inventory, inherited genome (replication governor), hidden manufacturing defects. |
| Skills (fast loop) | `vnmach/controller.py` | Scripted per-tick control: MRP netting of what's missing, then mine / refine / fabricate / assemble / repair. |
| Planner (slow loop) | `vnmach/director.py`, `vnmach/llm.py` | Every N ticks sets colony policy: replicate?, build fabs?, how many?, chip reserve. Heuristic or Claude. |

The planner never drives motors. It sets policy for a reliable skill layer
underneath, and its output is clamped (`sanitize`). If the API fails or
refuses, the current policy stays in place.

## What the simulation shows so far

Seed: 3 robots + 20 chips from Earth, 8000 ticks, 6 seeds:

| Director | Final pop | Fabs | Chips made on Mars |
|---|---|---|---|
| none | 16 | 0 | 0 |
| static (build 1 fab) | 129 | 1 | 163 |
| heuristic (fabs scale with pop) | 172 | 11.5 | 321 |

1. **Chips are the only vitamin** (99% closure by mass, but you can't
   replicate past the chips you brought). Without a fab, population caps at
   seed + imported chips.
2. **Spend vitamins to build the vitamin factory.** A fab costs 4 imported
   chips. Spend them on robots first and the colony can never close the loop.
   This is a planning decision, not a control one.
3. **One fab gives linear growth.** Exponential growth needs the colony to
   replicate its *factories*, not just its robots.
4. **A single seed robot is a single point of failure.** Seeds die to a random
   fatal fault before replicating.
5. **Self-repair keeps a long-lived colony working.** Under heavy wear,
   colonies without it fall to ~55% efficiency; with it they stay at ~100%.
6. **Replication governors must be protected from copy errors.** With
   `--mutation-rate 1 --unprotected-governor`, the max-children limit drifts.
