# Lunar replicator: a first design

A concept for the simplest robot system that can build copies of itself
on the Moon from local material, with as little as possible shipped from
Earth. Numbers marked *(est.)* are my estimates to be tested, not
measured values.

## 1. Design rules

1. **Few part types.** Every unique part needs its own process, mould or
   program. Aim for about 12 part types per robot. RepRap and Chirikjian's
   lunar self-replicating robot studies both found part count to be the
   main cost of closure.
2. **Coarse tolerances.** Design everything to ±1 mm so parts can be cast
   in regolith-sand moulds or printed, then fitted by the robot. Precision
   lives only in the few imported parts.
3. **No magnets, no plastics, no batteries in the replicated part.**
   - Magnets: the Moon has no economical rare earths.
   - Plastics: the Moon has almost no carbon outside polar ice.
   - Batteries: needs chemistry the colony can't make early on.
   - So the build uses iron, iron-silicon alloy, aluminium, glass and
     cast basalt.
4. **Tolerate dust.** Lunar dust is sharp, abrasive and statically charged.
   - Use no sliding seals and no exposed precision bearings.
   - Use flexures, sealed reducers, magnetic (iron, non-permanent)
     couplings and wide clearances.
5. **Make the factory replicable, not just the robot.** The Mars simulator
   showed that one fab gives linear growth. The furnaces, cells and moulds
   must be buildable by the robots too.
6. **Supervised from Earth, never teleoperated.** The Moon is 1.3 s away
   one way. In our arm benchmark, direct teleoperation fails at a 2 s
   delay, while supervisory control (send a goal, the robot does it) kept
   100% success. So every skill runs onboard, and Earth sends goals and
   approves risky steps.

## 2. Site: a lunar south-pole ridge

| Why | Detail |
|---|---|
| Power | Some rims near the south pole (e.g. around Shackleton crater) are sunlit about 80–90% of the time. That avoids the 14-day lunar night, which kills simple solar machines elsewhere. |
| Water | Permanently shadowed craters within a few km hold water ice (LCROSS measured ~5% by mass at Cabeus). Ice gives oxygen, hydrogen, and some carbon from other frozen volatiles. |
| Feedstock | Highland regolith is anorthosite, rich in calcium-aluminium silicate (~25–28% Al₂O₃), so a good aluminium source. Iron is lower than in mare basalt but present, including small native iron grains you can pull out with a magnet. |
| Comms | Earth stays near the horizon there; you need a relay or a mast. |

Main trade-off: iron-rich mare regolith (ilmenite, FeTiO₃) is far from the
pole. Start with highland aluminium plus magnetically separated iron, and
add iron from oxygen-extraction byproducts.

## 3. System: one robot plus four kinds of shared station

A single robot that can do everything would be far from simple. Instead:
one universal robot, plus four kinds of station. The robots build both
more robots and more stations.

```
            sunlight
               │
   ┌───────────▼────────────┐       ┌──────────────────────┐
   │ S1 Power mast          │──────▶│ S2 Melt & extract    │
   │ PV + concentrator      │ power │ solar furnace + MRE  │
   │ mirrors, power cable   │       │ → Fe-Si, Al, O₂, slag│
   └───────────▲────────────┘       └──────────┬───────────┘
               │ power                         │ metal ingots, slag
   ┌───────────┴────────────┐       ┌──────────▼───────────┐
   │ R  Universal robot     │◀─────▶│ S3 Forming cell      │
   │ digs, hauls, assembles,│ parts │ sand casting, wire   │
   │ welds, inspects        │       │ drawing, coil winding│
   └───────────▲────────────┘       └──────────┬───────────┘
               │ imported "vitamin" modules    │ cast & wound parts
   ┌───────────┴────────────┐                  │
   │ S4 Vitamin depot       │◀─────────────────┘ test fixtures
   │ electronics, sensors,  │
   │ anodes, from Earth     │
   └────────────────────────┘
```

### R: universal robot (about 300 kg)

| Subsystem | Design | Made from | Local? |
|---|---|---|---|
| Frame | Bolted box truss of 4 identical cast beams + 2 end plates | cast iron / Fe-Si | yes |
| Wheels (×4) | Open woven wire-mesh wheels, like the Apollo rover's, on cast hubs | drawn iron wire | yes |
| Drive motors (×4) | Switched-reluctance motors: stacked Fe-Si laminations, wound aluminium coils, no magnets | Fe-Si, Al wire, glass-fibre insulation | yes |
| Reducers (×8) | Cycloidal reducers: chunky cast discs and pins, ±0.3 mm fits after lapping | cast iron, lapped | yes (needs a lapping step) |
| Arm | 4 joints (yaw, shoulder, elbow, wrist): the arm we already simulate in MuJoCo | cast tubes + the same motor/reducer | yes |
| Tool changer | Passive cone + spring latch; tools: scoop, two-finger gripper, electron-beam welding gun, sintering head | iron, glass | mostly; welding-gun filament is a vitamin |
| Joining | Bolts (cast) and electron-beam welds. Vacuum makes EB welding easy. | iron | yes |
| Power | 100–300 V DC through a tether reel from the power mast; small supercapacitor buffer | Al cable, glass insulation; capacitor imported | cable yes, capacitor no |
| Thermal | Aluminium radiator fins + multi-layer insulation of aluminium-coated glass cloth | Al, glass | yes |
| Brain | Radiation-tolerant compute + motor drivers on one board | — | **vitamin** |
| Sensors | Stereo cameras, a lidar, joint encoders, motor current used as force sensing | — | **vitamin** (except current sensing) |
| Bearings | Plain bushings: iron shaft running in cast-basalt sleeve (different materials avoid vacuum cold welding) | iron, cast basalt | yes |

**Unique replicated part types (12):** beam, end plate, wheel hub, mesh
wheel, motor stator lamination, motor rotor lamination, coil, reducer
disc, reducer pin, arm tube, bolt, bushing.

**Imported vitamins per robot** *(est.)*: control board (1.5 kg), camera +
lidar head (2 kg), encoders and cabling connectors (1 kg), supercapacitor
(4 kg), welding-gun filaments. **About 9 kg of a 300 kg robot, so 97%
closure by mass.**

### S1: power mast

- **What it is:** vertical mast with photovoltaic panels and a ring of
  concentrator mirrors.
- **Mirrors:** easiest of all to make locally. Cast glass, then vacuum-
  deposit aluminium on it. The Moon is already a vacuum chamber.
- **Panels:** imported for generation 1.
- **Later:** grow silicon solar cells directly on regolith-glass sheets in
  vacuum, as proposed by Ignatiev et al. That turns the largest imported
  item into a local one.

### S2: melt and extract

- **Solar furnace:** concentrated sunlight melts regolith for cast basalt
  and glass. No electricity needed.
- **Molten regolith electrolysis (MRE) cell:** passes current through
  molten regolith.
  - Produces oxygen at the anode, and iron-silicon and aluminium-bearing
    alloys at the cathode.
  - Oxygen is a valuable byproduct: rocket propellant and life support.
    This is the colony's first product to sell.
  - Imported part: the inert anode (iridium or a newer alloy), a known hard
    problem. Replace when worn.
- **Magnetic separator:** a simple drum magnet. Pulls native iron grains
  from regolith before melting, giving early easy iron for the first parts.

### S3: forming cell

- **Sand casting:** regolith is fine-grained mould sand. Patterns for all
  12 part types are reused, and the robots ram the moulds.
- **Wire drawing:** iron wire for wheels and aluminium wire for motor
  coils, from cast rod through hardened dies (dies are a vitamin at first).
- **Coil winder:** a lathe-like spindle with a guide. Insulation is glass
  fibre drawn from the furnace melt, plus glass slurry baked on.
- **Lapping table:** brings reducer discs and bushings to fit. Slow, but
  needs no precision machine.
- **Lamination stamping:** a slow mechanical press stamps Fe-Si sheet into
  motor laminations.

### S4: vitamin depot

A shelf of Earth-made modules, and a test stand where robots check each
module before installing it. This is the same "chips are the only
vitamin" situation as our Mars model.

## 4. Replication loop

```
dig regolith ─▶ magnetic separation ─▶ furnace / MRE ─▶ ingots, glass, O₂
     ▲                                                     │
     │                                                     ▼
 new robot  ◀─ assemble + test ◀─ add vitamins ◀─ cast / draw / wind / lap
```

Rough throughput for one set of stations *(est.)*:

| Step | Rate | Per 300 kg robot |
|---|---|---|
| Regolith dug and hauled (1 robot) | ~2 t/day | 2–4 days (2.5 t of regolith, if ~10% becomes usable metal) |
| Metal from MRE (one ~20 kW cell) | ~10 kg/day | ~30 days, **the bottleneck** |
| Casting, drawing, winding, lapping | ~25 kg of parts/day | ~12 days, overlaps with MRE |
| Assembly and test | — | ~5 days |

**Doubling time ≈ 35–45 days per set of stations**, if the robots also
build more MRE cells (each needing an imported anode). Without that, the
fixed MRE output makes growth linear.

## 5. What to import, and when

| Generation | Shipped from Earth | Made locally | Closure by mass |
|---|---|---|---|
| 0 | 3 robots, 1 power mast, 1 MRE cell, forming cell tools, patterns | nothing | 0% |
| 1 | Electronics, sensors, supercapacitors, anodes, PV panels, wire dies | frames, wheels, motors, reducers, arms, cables, mirrors, castings | ~85% (PV and anodes are heavy) |
| 2 | Electronics, sensors, anodes | + PV on glass, dies, capacitors from aluminium foil and glass | ~97% |
| 3 | Electronics only | + anodes (if a regolith-derived anode works) | ~99% |

The last item is chips, as on Mars. A lunar chip fab is far harder than
anything above.

## 6. Hardest unknowns

1. **MRE anode lifetime.** It sets how much has to be imported per tonne of
   metal.
2. **Lapping reducers to fit with robots alone.** It needs force control;
   the arm benchmark showed that dust plus worn motors makes fine fits
   much harder.
3. **Switched-reluctance motor performance with aluminium coils and
   glass-fibre insulation** across the polar temperature range.
4. **Dust inside joints over months.** Plain bushings may wear quickly.
5. **Casting quality in 1/6 g.** Molten metal settles and flows more slowly
   than on Earth.

## 7. First steps on Earth

1. Build one robot on Earth from the 12 part types, using parts cast and
   wound by hand from lunar regolith simulant (e.g. LHS-1). This tests the
   design, not the replication.
2. Have that robot cast, lap and assemble one of each of its own part
   types, with humans only supplying vitamins and fixing failures. Count
   human interventions per kg, and drive that number down.
3. Add a Moon mode to the simulator: these materials and rates, no CO₂ or
   polymers, sunlight at 80–90% instead of dust storms, a 1.3 s delay and
   supervisory control. Then check the doubling time and closure numbers
   above against the model.

## Prior work worth reading

- Freitas & Gilbreath (eds.), *Advanced Automation for Space Missions*,
  NASA CP-2255, 1982. The original lunar self-replicating factory study.
- Chirikjian et al., NIAC studies on lunar self-replicating robots
  (Johns Hopkins, ~2002–2004).
- Freitas & Merkle, *Kinematic Self-Replicating Machines*, 2004.
- Ignatiev & Freundlich, papers on fabricating solar cells on the Moon from
  regolith.
- Sadoway et al., molten oxide electrolysis; MRE work by Lunar Resources
  and others.
- RepRap project (Bowyer, 2005 onward): practical lessons on "vitamins"
  and closure.
