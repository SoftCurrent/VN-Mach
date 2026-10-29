"""Command line: `python -m vnmach <command>`."""

from __future__ import annotations

import argparse
import json
import statistics
import sys

from .bom import closure, default_recipes
from .director import HeuristicDirector, StaticDirector
from .sim import Config, Simulation
from .viz import render_map, sparkline


def make_director(name: str, args) -> object | None:
    if name == "none":
        return None
    if name == "static":
        return StaticDirector(build_fab=True, target_fabs=1, chip_reserve=4)
    if name == "heuristic":
        return HeuristicDirector(target_population=args.target_population)
    if name == "claude":
        from .llm import ClaudeDirector

        return ClaudeDirector(model=args.model, effort=args.effort, total_ticks=args.ticks)
    raise SystemExit(f"unknown director {name}")


def config_from(args, seed: int | None = None) -> Config:
    return Config(
        seed=args.seed if seed is None else seed,
        ticks=args.ticks,
        seed_vitamins=args.chips,
        chip_fab=args.chip_fab,
        dust_storms=not args.no_storms,
        defect_rate=args.defect_rate,
        mutation_rate=args.mutation_rate,
        governor_protected=not args.unprotected_governor,
        max_population=args.max_population,
        director_interval=args.director_interval,
    )


def cmd_closure(args) -> None:
    for fab in (False, True):
        rep = closure(default_recipes(chip_fab=fab), args.defect_rate)
        print(f"\n== {'with' if fab else 'without'} chip fab ==")
        print(f"part-count closure : {rep.parts_closure:.1%}")
        print(f"mass closure       : {rep.mass_closure:.1%}")
        print(f"vitamins per robot : {rep.vitamins or 'none'}")
        print(f"feedstock per robot: {rep.raw_per_robot}")
        print(f"energy per robot   : {rep.energy_per_robot:.0f}")
        print(f"serial build ticks : {rep.serial_ticks_per_robot}")
        print(f"P(no defects)      : {rep.defect_free_probability:.1%} (before inspection)")


def cmd_run(args) -> None:
    sim = Simulation(config_from(args), director=make_director(args.director, args))
    hist = sim.run()
    pops = [h["population"] for h in hist]
    print(f"population  {sparkline(pops)}  peak={max(pops)} final={pops[-1]}")
    print(f"chips made  {sparkline([h['chips_made'] for h in hist])}  total={hist[-1]['chips_made']}")
    print(f"fabs        {sparkline([h['fabs'] for h in hist])}  final={hist[-1]['fabs']}")
    if args.map:
        print(render_map(sim))
    snap = sim.snapshot()
    print(json.dumps({k: snap[k] for k in ("t", "population", "replicators", "workers", "fabs", "chips_in_depot",
                                           "chips_made", "max_generation", "max_children_genome", "mean_efficiency")}))
    print(json.dumps(dict(sim.stats)))
    if sim.policy_log:
        print(f"policy changes: {len(sim.policy_log)}; last: {sim.policy_log[-1]}")
    usage = getattr(sim.director, "usage", None)
    if usage:
        print(f"planner usage: {usage}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"history": hist, "stats": dict(sim.stats), "policy_log": sim.policy_log}, f)


def cmd_compare(args) -> None:
    names = args.directors.split(",")
    print(f"{'director':<10} {'final pop':>14} {'peak pop':>10} {'fabs':>6} {'chips made':>11} {'earth chips':>12}")
    for name in names:
        finals, peaks, fabs, made, earth = [], [], [], [], []
        for seed in range(args.seeds):
            sim = Simulation(config_from(args, seed), director=make_director(name, args))
            hist = sim.run()
            finals.append(hist[-1]["population"])
            peaks.append(max(h["population"] for h in hist))
            fabs.append(hist[-1]["fabs"])
            made.append(hist[-1]["chips_made"])
            earth.append(sim.stats["vitamins_used"])
        sd = statistics.pstdev(finals)
        print(f"{name:<10} {statistics.mean(finals):>8.1f} ±{sd:>4.1f} {statistics.mean(peaks):>10.1f} "
              f"{statistics.mean(fabs):>6.1f} {statistics.mean(made):>11.1f} {statistics.mean(earth):>12.1f}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="vnmach", description="Self-replicating robot colony on Mars")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--seed", type=int, default=0)
        sp.add_argument("--ticks", type=int, default=8000)
        sp.add_argument("--chips", type=int, default=20, help="chips shipped from Earth")
        sp.add_argument("--chip-fab", action="store_true", help="base starts with a working chip fab")
        sp.add_argument("--no-storms", action="store_true")
        sp.add_argument("--defect-rate", type=float, default=0.02)
        sp.add_argument("--mutation-rate", type=float, default=0.0)
        sp.add_argument("--unprotected-governor", action="store_true",
                        help="let copy errors change the replication limit")
        sp.add_argument("--max-population", type=int, default=400)
        sp.add_argument("--target-population", type=int, default=150, help="heuristic director stop size")
        sp.add_argument("--director-interval", type=int, default=250)
        sp.add_argument("--model", default="claude-opus-5-5")
        sp.add_argument("--effort", default="low", choices=["low", "medium", "high", "xhigh", "max"])

    sp = sub.add_parser("closure", help="material closure analysis of the robot design")
    sp.add_argument("--defect-rate", type=float, default=0.02)
    sp.set_defaults(fn=cmd_closure)

    sp = sub.add_parser("run", help="run one colony")
    common(sp)
    sp.add_argument("--director", default="heuristic", choices=["none", "static", "heuristic", "claude"])
    sp.add_argument("--map", action="store_true")
    sp.add_argument("--json", help="write history to this file")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("compare", help="compare directors over several seeds")
    common(sp)
    sp.add_argument("--directors", default="none,static,heuristic")
    sp.add_argument("--seeds", type=int, default=5)
    sp.set_defaults(fn=cmd_compare)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
