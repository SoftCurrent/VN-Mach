"""Skill benchmarks: how well does each controller seat a part, and how fast?

The colony simulator consumes :func:`skill_profile`, which turns a controller and
operating conditions into ``{"assembly_success": p, "time_multiplier": m}``:

* ``assembly_success``: probability one insertion attempt succeeds within the time limit
  (no jam, no damaging force).
* ``time_multiplier``: mean time per attempt (successful or not, including any fixed
  command latency) relative to the scripted controller under clean conditions.
"""

from __future__ import annotations

import math
import os
import pickle
import time
from dataclasses import asdict, dataclass
from functools import lru_cache

import numpy as np

from .controllers import make_controller
from .env import PegInHoleEnv, rollout

# Operating conditions. "clean" still has a little camera error: real cameras are not perfect.
CLEAN = {"tolerance_mm": 0.5, "noise_mm": 0.5, "degradation": 1.0}
CONDITIONS = {
    "clean": dict(CLEAN),
    "dusty": {**CLEAN, "noise_mm": 3.0},  # dust on the camera: ~3 mm hole-position error
    "worn": {**CLEAN, "degradation": 0.25},  # motors deliver a quarter of commanded torque
    "tight": {**CLEAN, "tolerance_mm": 0.1},  # 0.1 mm diametral clearance
    "harsh": {"tolerance_mm": 0.1, "noise_mm": 3.0, "degradation": 0.25},  # all three at once
}
DELAYS = (0.5, 2.0, 10.0, 1200.0)  # one-way, seconds (1200 s = 20 min Mars light delay)
DEFAULT_TIME_LIMIT = 60.0


@dataclass
class SkillStats:
    controller: str
    tolerance_mm: float
    noise_mm: float
    degradation: float
    delay_s: float
    n_trials: int
    success_rate: float
    success_ci95: tuple[float, float]
    mean_time_s: float  # mean over successful trials (nan if none), including latency
    mean_attempt_s: float  # mean over all trials (failures count the time they used)
    jam_rate: float
    overforce_rate: float
    timeout_rate: float
    mean_peak_force_n: float
    latency_s: float  # fixed command latency added to every trial

    def as_dict(self) -> dict:
        return asdict(self)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def trial_seeds(seed: int, n: int) -> list[int]:
    """Episode seeds shared by every controller (common random numbers)."""
    return [int(np.random.SeedSequence([seed, i]).generate_state(1)[0]) for i in range(n)]


# ---------------------------------------------------------------------------------------
# Running trials (optionally in parallel)
# ---------------------------------------------------------------------------------------
def _build(spec):
    if isinstance(spec, tuple):
        return make_controller(*spec)
    return pickle.loads(spec)


def _run_chunk(args) -> list[tuple]:
    spec, env_kw, seeds = args
    env = PegInHoleEnv(**env_kw)
    ctrl = _build(spec)
    out = []
    for s in seeds:
        info = rollout(env, ctrl, s)
        out.append((info["outcome"], info["time_s"], info["peak_force_n"]))
    return out


def _spec(controller, delay_s):
    if isinstance(controller, str):
        return (controller, float(delay_s))
    if callable(controller) and not hasattr(controller, "act"):
        controller = controller()
    return pickle.dumps(controller)


def _latency_and_name(spec) -> tuple[float, str, object]:
    c = _build(spec)
    return float(getattr(c, "latency_s", 0.0)), getattr(c, "name", type(c).__name__), c


def _run_cells(cells: list[dict], workers: int | None = None) -> list[SkillStats]:
    """cells: dicts with controller, delay_s, env kwargs, n_trials, seed, time_limit_s."""
    workers = workers or min(4, os.cpu_count() or 1)
    tasks, slots, prepared = [], [], []
    for ci, cell in enumerate(cells):
        spec = _spec(cell["controller"], cell["delay_s"])
        latency, name, ctrl = _latency_and_name(spec)
        env_kw = {k: cell[k] for k in ("tolerance_mm", "noise_mm", "degradation")}
        env_kw["time_limit_s"] = cell["time_limit_s"]
        seeds = trial_seeds(cell["seed"], cell["n_trials"])
        # A teleoperator whose commands cannot arrive before the time limit cannot succeed.
        if getattr(ctrl, "delay", 0.0) >= cell["time_limit_s"]:
            prepared.append((name, latency, [("timeout", cell["time_limit_s"], 0.0)] * len(seeds)))
            continue
        prepared.append((name, latency, None))
        n_chunks = min(len(seeds), workers * 2)
        for k in range(n_chunks):
            tasks.append((spec, env_kw, seeds[k::n_chunks]))
            slots.append(ci)
    if workers > 1 and len(tasks) > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(workers) as pool:
            results = pool.map(_run_chunk, tasks, chunksize=1)
    else:
        results = [_run_chunk(t) for t in tasks]
    per_cell: dict[int, list] = {}
    for ci, res in zip(slots, results):
        per_cell.setdefault(ci, []).extend(res)

    stats = []
    for ci, cell in enumerate(cells):
        name, latency, fixed = prepared[ci]
        res = fixed if fixed is not None else per_cell.get(ci, [])
        n = len(res)
        outs = [r[0] for r in res]
        times = np.array([r[1] for r in res]) + latency
        ok = np.array([o == "success" for o in outs])
        k = int(ok.sum())
        stats.append(SkillStats(
            controller=name, tolerance_mm=cell["tolerance_mm"], noise_mm=cell["noise_mm"],
            degradation=cell["degradation"], delay_s=cell["delay_s"], n_trials=n,
            success_rate=k / n if n else 0.0, success_ci95=wilson(k, n),
            mean_time_s=float(times[ok].mean()) if k else float("nan"),
            mean_attempt_s=float(times.mean()) if n else float("nan"),
            jam_rate=outs.count("jammed") / n if n else 0.0,
            overforce_rate=outs.count("overforce") / n if n else 0.0,
            timeout_rate=outs.count("timeout") / n if n else 0.0,
            mean_peak_force_n=float(np.mean([r[2] for r in res])) if n else 0.0,
            latency_s=latency,
        ))
    return stats


def _cell(controller, tolerance_mm, noise_mm, degradation, delay_s, n_trials, seed, time_limit_s):
    return dict(controller=controller, tolerance_mm=float(tolerance_mm), noise_mm=float(noise_mm),
                degradation=float(degradation), delay_s=float(delay_s), n_trials=int(n_trials),
                seed=int(seed), time_limit_s=float(time_limit_s))


# ---------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------
def measure_skill(controller="scripted", tolerance_mm: float = 0.5, noise_mm: float = 0.5,
                  degradation: float = 1.0, delay_s: float = 0.0, n_trials: int = 30, seed: int = 0,
                  time_limit_s: float = DEFAULT_TIME_LIMIT, workers: int | None = None) -> SkillStats:
    """Run ``n_trials`` insertions and summarise them.

    ``controller``: a name from ``controllers.CONTROLLERS`` ("scripted", "learned", "teleop",
    "teleop_mw", "supervisory", "supervisory_learned"), a controller instance, or a zero-arg
    factory. ``delay_s`` is the one-way communication delay (only used when ``controller`` is
    a name; instances carry their own). Deterministic for a given seed and any worker count.
    """
    cell = _cell(controller, tolerance_mm, noise_mm, degradation, delay_s, n_trials, seed, time_limit_s)
    return _run_cells([cell], workers)[0]


@lru_cache(maxsize=8)
def nominal_attempt_time(n_trials: int = 40, seed: int = 0,
                         time_limit_s: float = DEFAULT_TIME_LIMIT) -> float:
    """Mean attempt time of the scripted controller under clean conditions (the 1.0x reference)."""
    s = measure_skill("scripted", **CLEAN, n_trials=n_trials, seed=seed, time_limit_s=time_limit_s)
    return s.mean_attempt_s


def profile_from_stats(stats: SkillStats, nominal_s: float | None = None) -> dict:
    nominal_s = nominal_s or nominal_attempt_time()
    return {"assembly_success": float(stats.success_rate),
            "time_multiplier": float(stats.mean_attempt_s / nominal_s)}


def skill_profile(controller="scripted", tolerance_mm: float = 0.5, noise_mm: float = 0.5,
                  degradation: float = 1.0, delay_s: float = 0.0, n_trials: int = 30, seed: int = 0,
                  time_limit_s: float = DEFAULT_TIME_LIMIT, workers: int | None = None) -> dict:
    """``{"assembly_success": float, "time_multiplier": float}`` for the colony simulator.

    time_multiplier = mean time per attempt (failures included, latency included) divided by
    the scripted controller's mean attempt time under clean conditions.
    """
    s = measure_skill(controller, tolerance_mm, noise_mm, degradation, delay_s, n_trials, seed,
                      time_limit_s, workers)
    return profile_from_stats(s)


def sweep(controller="scripted", param: str = "noise_mm", values=(0.0, 1.0, 2.0, 3.0, 4.0),
          n_trials: int = 30, seed: int = 0, time_limit_s: float = DEFAULT_TIME_LIMIT,
          workers: int | None = None, **base) -> list[SkillStats]:
    """Measure ``controller`` at each value of ``param`` (tolerance_mm, noise_mm, degradation, delay_s)."""
    assert param in ("tolerance_mm", "noise_mm", "degradation", "delay_s"), param
    kw = {**CLEAN, "delay_s": 0.0, **base}
    cells = [_cell(controller, **{**kw, param: v}, n_trials=n_trials, seed=seed, time_limit_s=time_limit_s)
             for v in values]
    return _run_cells(cells, workers)


# ---------------------------------------------------------------------------------------
# The benchmark table
# ---------------------------------------------------------------------------------------
BENCH_ROWS = {
    # row label: (controller name, conditions to run, delays to run)
    "scripted": ("scripted", tuple(CONDITIONS), ()),
    "learned": ("learned", tuple(CONDITIONS), ()),
    "teleop continuous": ("teleop", tuple(CONDITIONS), DELAYS),
    "teleop move&wait": ("teleop_mw", tuple(CONDITIONS), DELAYS),
    "supervisory (scripted)": ("supervisory", ("clean",), DELAYS),
    "supervisory (learned)": ("supervisory_learned", ("clean",), DELAYS),
}


def bench(n_trials: int = 30, seed: int = 0, workers: int | None = None,
          time_limit_s: float = DEFAULT_TIME_LIMIT, rows: dict | None = None) -> dict:
    """Run the controllers x conditions grid. Returns {(row, column): SkillStats}."""
    rows = rows or BENCH_ROWS
    keys, cells = [], []
    for label, (name, conds, delays) in rows.items():
        for c in conds:
            keys.append((label, c))
            cells.append(_cell(name, **CONDITIONS[c], delay_s=0.0, n_trials=n_trials, seed=seed,
                               time_limit_s=time_limit_s))
        for d in delays:
            keys.append((label, f"delay {d:g}s"))
            cells.append(_cell(name, **CLEAN, delay_s=d, n_trials=n_trials, seed=seed, time_limit_s=time_limit_s))
    return dict(zip(keys, _run_cells(cells, workers)))


def _fmt_time(t: float) -> str:
    if not np.isfinite(t):
        return "-"
    return f"{t:.1f}s" if t < 100 else f"{t:.0f}s"


def format_table(results: dict, nominal_s: float, time_limit_s: float = DEFAULT_TIME_LIMIT) -> str:
    rows = list(dict.fromkeys(r for r, _ in results))
    cols = list(CONDITIONS) + [f"delay {d:g}s" for d in DELAYS]
    w0 = max(len(r) for r in rows) + 2
    w = 13

    def table(title, cell_fn):
        lines = [title, "".ljust(w0) + "".join(c.rjust(w) for c in cols)]
        for r in rows:
            line = r.ljust(w0)
            for c in cols:
                s = results.get((r, c))
                line += (cell_fn(s) if s is not None else "").rjust(w)
            lines.append(line)
        return "\n".join(lines)

    n = next(iter(results.values())).n_trials
    a = table(f"Success rate and mean time to seat the part (successful trials), n={n} per cell",
              lambda s: f"{100 * s.success_rate:3.0f}% {_fmt_time(s.mean_time_s):>7}")
    b = table("Failures: jammed / overforce / timeout (counts)",
              lambda s: f"{round(s.jam_rate * s.n_trials)}/{round(s.overforce_rate * s.n_trials)}/"
                        f"{round(s.timeout_rate * s.n_trials)}")
    c = table(f"Colony skill_profile time_multiplier (mean attempt time / {nominal_s:.2f}s scripted-clean)",
              lambda s: f"{s.mean_attempt_s / nominal_s:.1f}x")
    notes = ("conditions: clean = 0.5 mm clearance, 0.5 mm camera error, new motors; dusty = 3 mm camera\n"
             "error; worn = 25% motor gain; tight = 0.1 mm clearance; harsh = dusty + worn + tight.\n"
             "delay columns: clean conditions, one-way delay; teleop columns 'clean'..'harsh' are at zero\n"
             "delay; teleop at 1200 s is 0% by construction (no command arrives within the limit).\n"
             f"Time limit per attempt {time_limit_s:.0f}s (supervisory latency is added on top of the onboard "
             "execution).")
    return "\n\n".join([a, b, c, notes])


def run_bench(n_trials: int = 30, seed: int = 0, workers: int | None = None) -> str:
    t0 = time.time()
    res = bench(n_trials, seed, workers)
    text = format_table(res, nominal_attempt_time(seed=seed))
    return text + f"\n\nbench wall time {time.time() - t0:.1f}s"
