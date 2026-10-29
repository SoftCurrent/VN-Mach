"""Command line: `python -m vnmach <command>`."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

from .bom import closure, default_recipes
from .director import HeuristicDirector
from .econ import EconConfig
from .sim import Config, Simulation
from .viz import render_map, sparkline

DIRECTORS = ["none", "import", "fab", "auto", "claude"]


def make_director(name: str, args) -> object | None:
    if name == "none":
        return None
    if name in ("import", "fab", "auto"):
        return HeuristicDirector(name, target_population=args.target_population, total_ticks=args.ticks)
    if name == "claude":
        from .llm import ClaudeDirector

        return ClaudeDirector(model=args.model, effort=args.effort, total_ticks=args.ticks,
                              econ=config_from(args).econ)
    raise SystemExit(f"unknown director {name}")


def skill_from(spec: str | None) -> dict:
    """'controller:condition[:delay_s]' -> measured skill profile from the MuJoCo arm benchmark."""
    if not spec:
        return {}
    from .arm.bench import CONDITIONS, skill_profile

    parts = spec.split(":")
    controller, condition = parts[0], parts[1] if len(parts) > 1 else "clean"
    delay = float(parts[2]) if len(parts) > 2 else 0.0
    prof = skill_profile(controller, delay_s=delay, n_trials=30, **CONDITIONS[condition])
    print(f"arm skill {spec}: {prof}", file=sys.stderr)
    return {"skill_success": prof["assembly_success"], "skill_time_multiplier": prof["time_multiplier"]}


def config_from(args, seed: int | None = None, director: str | None = None) -> Config:
    econ = EconConfig(launch_cost_per_kg=args.launch_cost, launch_cost_decline=args.launch_decline,
                      starting_cash=args.cash * 1e6)
    location = args.director_location or ("earth" if director == "claude" else "mars")
    return Config(
        seed=args.seed if seed is None else seed,
        ticks=args.ticks,
        seed_chips=args.chips,
        chip_fab=args.chip_fab,
        dust_storms=not args.no_storms,
        defect_rate=args.defect_rate,
        mutation_rate=args.mutation_rate,
        governor_protected=not args.unprotected_governor,
        max_population=args.max_population,
        director_interval=args.director_interval,
        director_location=location,
        econ=econ,
        **getattr(args, "skill_profile", {}),
    )


def run_one(args, director: str, seed: int, **econ_overrides) -> dict:
    cfg = config_from(args, seed, director)
    if econ_overrides:
        cfg.econ = replace(cfg.econ, **econ_overrides)
    sim = Simulation(cfg, director=make_director(director, args))
    hist = sim.run()
    last = hist[-1]
    return {
        "director": director, "seed": seed, **econ_overrides,
        "population": last["population"], "peak": max(h["population"] for h in hist),
        "fabs": last["fabs"], "chips_made": last["chips_made"],
        "chips_imported": sim.stats["imported_chip"] + cfg.seed_chips,
        "first_fab_sol": sim.stats["first_fab_at"] // 10 if "first_fab_at" in sim.stats else None,
        "cash_musd": last["cash_musd"], "revenue_musd": last["revenue_musd"],
        "net_value_musd": last["net_value_musd"], "contracts_done": last["contracts_done"],
        "ledger_musd": sim.econ.snapshot(sim.t)["ledger_musd"],
    }


def _run_one(job):
    args, director, seed, overrides = job
    return run_one(args, director, seed, **overrides)


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
    sim = Simulation(config_from(args, director=args.director), director=make_director(args.director, args))
    hist = sim.run()
    pops = [h["population"] for h in hist]
    print(f"population  {sparkline(pops)}  peak={max(pops)} final={pops[-1]}")
    print(f"chips made  {sparkline([h['chips_made'] for h in hist])}  total={hist[-1]['chips_made']}")
    print(f"fabs        {sparkline([h['fabs'] for h in hist])}  final={hist[-1]['fabs']}")
    print(f"cash $M     {sparkline([max(0, h['cash_musd']) for h in hist])}  final={hist[-1]['cash_musd']}")
    if args.map:
        print(render_map(sim))
    snap = sim.snapshot()
    keys = ("sol", "population", "fabs", "chips_in_depot", "chips_made", "fleet_value_musd")
    print(json.dumps({k: snap[k] for k in keys}))
    print("ledger $M:", json.dumps(snap["economy"]["ledger_musd"]))
    print(json.dumps(dict(sim.stats)))
    if sim.policy_log:
        orders = [(t // 10, u["orders"]) for t, u in sim.policy_log if "orders" in u]
        print(f"policy changes: {len(sim.policy_log)}; Earth orders (sol, items): {orders}")
    usage = getattr(sim.director, "usage", None)
    if usage:
        print(f"planner usage: {dict(usage)}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"history": hist, "stats": dict(sim.stats), "policy_log": sim.policy_log}, f)


def run_jobs(jobs, workers: int):
    if workers <= 1:
        return [_run_one(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_run_one, jobs))


def cmd_compare(args) -> None:
    names = args.directors.split(",")
    jobs = [(args, name, seed, {}) for name in names for seed in range(args.seeds)]
    results = run_jobs(jobs, 1 if "claude" in names else args.workers)
    print(f"{'director':<9} {'final pop':>12} {'fabs':>5} {'chips made':>10} {'chips imp':>9} "
          f"{'cash $M':>9} {'revenue $M':>10} {'net value $M':>12}")
    for name in names:
        rs = [r for r in results if r["director"] == name]
        m = lambda k: statistics.mean(r[k] for r in rs)  # noqa: E731
        print(f"{name:<9} {m('population'):>7.1f} ±{statistics.pstdev([r['population'] for r in rs]):>3.0f} "
              f"{m('fabs'):>5.1f} {m('chips_made'):>10.1f} {m('chips_imported'):>9.1f} {m('cash_musd'):>9.0f} "
              f"{m('revenue_musd'):>10.0f} {m('net_value_musd'):>12.0f}")


def cmd_econ(args) -> None:
    """Sweep launch cost: when does shipping chips beat building a fab on Mars?"""
    costs = [float(x) for x in args.launch_costs.split(",")]
    names = args.directors.split(",")
    jobs = [(args, name, seed, {"launch_cost_per_kg": c}) for c in costs for name in names for seed in range(args.seeds)]
    results = run_jobs(jobs, args.workers)
    print(f"Final cash ($M) after {args.ticks // 10} sols, mean of {args.seeds} seeds. * = best")
    print(f"{'$/kg':>7} " + " ".join(f"{n:>10}" for n in names) + "   auto: first fab sol")
    for c in costs:
        row = {}
        for n in names:
            rs = [r for r in results if r["director"] == n and r["launch_cost_per_kg"] == c]
            row[n] = statistics.mean(r["cash_musd"] for r in rs)
        best = max(row, key=row.get)
        auto = [r["first_fab_sol"] for r in results if r["director"] == "auto" and r["launch_cost_per_kg"] == c]
        cells = " ".join(f"{row[n]:>9.0f}{'*' if n == best else ' '}" for n in names)
        print(f"{c:>7.0f} {cells}   {auto if auto else ''}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=1)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="vnmach", description="Self-replicating robot colony on Mars")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--seed", type=int, default=0)
        sp.add_argument("--ticks", type=int, default=20000, help="10 ticks = 1 sol")
        sp.add_argument("--chips", type=int, default=20, help="chips on the first landing")
        sp.add_argument("--chip-fab", action="store_true", help="base starts with a working chip fab")
        sp.add_argument("--no-storms", action="store_true")
        sp.add_argument("--defect-rate", type=float, default=0.02)
        sp.add_argument("--mutation-rate", type=float, default=0.0)
        sp.add_argument("--unprotected-governor", action="store_true",
                        help="let copy errors change the replication limit")
        sp.add_argument("--max-population", type=int, default=400)
        sp.add_argument("--target-population", type=int, default=150)
        sp.add_argument("--director-interval", type=int, default=250)
        sp.add_argument("--director-location", choices=["earth", "mars"],
                        help="earth = conjunction blackouts + latency (default for claude)")
        sp.add_argument("--launch-cost", type=float, default=5000, help="USD per kg landed on Mars")
        sp.add_argument("--launch-decline", type=float, default=0.15, help="launch cost drop per window")
        sp.add_argument("--cash", type=float, default=600, help="starting cash, $M")
        sp.add_argument("--model", default="claude-opus-5-5")
        sp.add_argument("--effort", default="low", choices=["low", "medium", "high", "xhigh", "max"])
        sp.add_argument("--workers", type=int, default=4)
        sp.add_argument("--skill", help="measure manipulation in the MuJoCo arm and use it, e.g. "
                        "learned:harsh, scripted:dusty, teleop:clean:0.5, supervisory:clean:1200")

    sp = sub.add_parser("closure", help="material closure analysis of the robot design")
    sp.add_argument("--defect-rate", type=float, default=0.02)
    sp.set_defaults(fn=cmd_closure)

    sp = sub.add_parser("run", help="run one colony")
    common(sp)
    sp.add_argument("--director", default="auto", choices=DIRECTORS)
    sp.add_argument("--map", action="store_true")
    sp.add_argument("--json", help="write history to this file")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("compare", help="compare directors over several seeds")
    common(sp)
    sp.add_argument("--directors", default="none,import,fab,auto")
    sp.add_argument("--seeds", type=int, default=4)
    sp.set_defaults(fn=cmd_compare)

    sp = sub.add_parser("econ", help="sweep launch cost: import chips vs build fabs")
    common(sp)
    sp.add_argument("--directors", default="import,fab,auto")
    sp.add_argument("--launch-costs", default="20000,10000,5000,2000,500")
    sp.add_argument("--seeds", type=int, default=2)
    sp.add_argument("--json", help="write all results to this file")
    sp.set_defaults(fn=cmd_econ)

    args = p.parse_args(argv)
    args.skill_profile = skill_from(getattr(args, "skill", None))
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
