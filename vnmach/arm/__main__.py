"""python -m vnmach.arm {bench,train,profile,sweep}"""

from __future__ import annotations

import argparse
import json
import sys

from .controllers import CONTROLLERS, DEFAULT_WEIGHTS


def _t(x: float) -> str:
    return f"{x:.1f}s" if x == x else "-"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m vnmach.arm", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bench", help="controllers x conditions table")
    b.add_argument("--trials", type=int, default=30)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--workers", type=int, default=None)

    t = sub.add_parser("train", help="train the learned residual policy (CEM, CPU)")
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--iters", type=int, default=20)
    t.add_argument("--pop", type=int, default=24)
    t.add_argument("--episodes", type=int, default=8, help="episodes per candidate per iteration")
    t.add_argument("--workers", type=int, default=None)
    t.add_argument("--out", default=DEFAULT_WEIGHTS)
    t.add_argument("--no-residual", dest="residual", action="store_false",
                   help="only tune the scripted skill's parameters (no residual policy)")

    p = sub.add_parser("profile", help="print skill_profile() as JSON")
    p.add_argument("--controller", default="scripted", choices=CONTROLLERS)
    p.add_argument("--tolerance-mm", type=float, default=0.5)
    p.add_argument("--noise-mm", type=float, default=0.5)
    p.add_argument("--degradation", type=float, default=1.0)
    p.add_argument("--delay-s", type=float, default=0.0)
    p.add_argument("--trials", type=int, default=30)
    p.add_argument("--seed", type=int, default=0)

    s = sub.add_parser("sweep", help="success/time as one parameter varies")
    s.add_argument("--controller", default="scripted", choices=CONTROLLERS)
    s.add_argument("--param", default="noise_mm", choices=("tolerance_mm", "noise_mm", "degradation", "delay_s"))
    s.add_argument("--values", default="0,1,2,3,4")
    s.add_argument("--trials", type=int, default=30)
    s.add_argument("--seed", type=int, default=0)

    a = ap.parse_args(argv)
    if a.cmd == "bench":
        from .bench import run_bench
        print(run_bench(a.trials, a.seed, a.workers))
    elif a.cmd == "train":
        from .controllers import train
        r = train(seed=a.seed, iters=a.iters, pop=a.pop, episodes=a.episodes, workers=a.workers, out=a.out,
                  residual=a.residual)
        sys.exit(0 if r["improved"] else 1)
    elif a.cmd == "profile":
        from .bench import skill_profile
        print(json.dumps(skill_profile(a.controller, a.tolerance_mm, a.noise_mm, a.degradation, a.delay_s,
                                       a.trials, a.seed)))
    elif a.cmd == "sweep":
        from .bench import sweep
        vals = [float(v) for v in a.values.split(",")]
        print(f"{a.controller}: sweep {a.param}")
        print(f"{a.param:>12} {'success':>8} {'95% CI':>13} {'time':>8} {'jam':>5} {'force':>6} {'timeout':>8}")
        for v, st in zip(vals, sweep(a.controller, a.param, vals, a.trials, a.seed)):
            lo, hi = st.success_ci95
            print(f"{v:12g} {100 * st.success_rate:7.0f}% {f'{100*lo:.0f}-{100*hi:.0f}%':>13} "
                  f"{_t(st.mean_time_s):>8} {st.jam_rate:5.2f} {st.overforce_rate:6.2f} {st.timeout_rate:8.2f}")


if __name__ == "__main__":
    main()
