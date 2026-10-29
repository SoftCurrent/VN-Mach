"""Controllers for the peg-in-hole arm.

All controllers share the same interface::

    ctrl.reset(env)          # once per episode (gives access to the robot's own nominal model)
    action = ctrl.act(obs)   # every control step, returns normalised joint torques

Three families:

* ``ScriptedController``: analytic IK + joint PD with gravity compensation, driven by a
  state machine (approach, descend/insert, spiral search on contact, compliant insert).
* ``LearnedController``: the scripted skill plus a small learned residual policy and
  learned skill parameters, trained by cross-entropy search (see ``train``).
* ``TeleopController``: a human-operator model that sees delayed observations and whose
  commands arrive delayed, in ``"continuous"`` or ``"move_wait"`` mode.
* ``SupervisoryController``: the operator sends one goal; an onboard skill executes it.
  The light delay only adds a fixed latency (``latency_s``).
"""

from __future__ import annotations

import math
import os
import time
from collections import deque
from dataclasses import dataclass, field

import mujoco
import numpy as np

from .env import GEAR, INSERT_DEPTH, SL_F, SL_HOLE, SL_Q, SL_QD, SL_T, SL_TIP, SL_TIPV, SOCKET_TOP, ik

WEIGHTS_DIR = os.path.join(os.path.dirname(__file__), "weights")
DEFAULT_WEIGHTS = os.path.join(WEIGHTS_DIR, "residual_cem.npz")


# ----------------------------------------------------------------------------------------
# Low-level servo: IK + joint PD + gravity compensation (runs onboard at the control rate)
# ----------------------------------------------------------------------------------------
class Servo:
    KP = np.array([150.0, 220.0, 160.0, 40.0])
    KD = np.array([6.0, 8.0, 5.0, 1.0])

    def __init__(self):
        self.model = None

    def reset(self, env):
        # The robot's *nominal* model of itself: it does not know its motors are worn.
        if self.model is not env.model:
            self.model = env.model
            self.data = mujoco.MjData(env.model)

    def gravity(self, q):
        self.data.qpos[:] = q
        self.data.qvel[:] = 0
        mujoco.mj_forward(self.model, self.data)
        return self.data.qfrc_bias.copy()

    def torque(self, obs, target):
        q, qd = obs[SL_Q], obs[SL_QD]
        qdes = ik(target)
        err = qdes - q
        err[0] = (err[0] + math.pi) % (2 * math.pi) - math.pi
        tau = self.KP * err - self.KD * qd + self.gravity(q)
        return np.clip(tau / GEAR, -1.0, 1.0)


def _move_toward(sp, goal, vmax, dt):
    d = goal - sp
    n = float(np.linalg.norm(d))
    step = vmax * dt
    return goal.copy() if n <= step else sp + d * (step / n)


# ----------------------------------------------------------------------------------------
# Scripted skill
# ----------------------------------------------------------------------------------------
@dataclass
class SkillParams:
    """Tunable parameters of the scripted insertion skill (SI units)."""
    v_approach: float = 0.25  # m/s
    approach_h: float = 0.025  # m above rim
    approach_tol: float = 0.002  # m
    v_insert: float = 0.04  # m/s setpoint descent in free space
    push: float = 0.004  # m: setpoint never more than this below the tip (bounds force)
    push_search: float = 0.002  # m: lighter push while spiralling
    f_contact: float = 2.5  # N: upward force that means "landed on the rim"
    spiral_pitch: float = 0.0008  # m per revolution
    spiral_speed: float = 0.012  # m/s tangential
    spiral_rmax: float = 0.010  # m: restart spiral beyond this
    caught_wait: float = 1.0  # s to wait while partially in before resuming the spiral
    hole_alpha: float = 0.05  # EMA on camera hole estimate
    xy_integral: float = 2.0  # 1/s: outer-loop correction of tip xy error in free space

    def to_vec(self):
        return np.array([getattr(self, k) for k in self.__dataclass_fields__])

    @classmethod
    def from_vec(cls, v):
        return cls(**{k: float(x) for k, x in zip(cls.__dataclass_fields__, v)})


class ScriptedController:
    name = "scripted"
    latency_s = 0.0

    def __init__(self, params: SkillParams | None = None):
        self.p = params or SkillParams()
        self.servo = Servo()

    # Hook for the learned residual: returns an xyz offset added to the setpoint.
    def residual(self, obs, phase_vec) -> np.ndarray:
        return np.zeros(3)

    def reset(self, env):
        self.servo.reset(env)
        self.dt = env.dt
        self.phase = "approach"
        self.sp = None
        self.hole = None
        self.theta = 0.0
        self.search = False
        self.integ = np.zeros(3)
        self.engaged_xy = None
        self.caught_t = 0.0

    def phase_vec(self):
        return np.array([self.phase == "approach", self.phase == "insert" and not self.search,
                         self.search, self.phase == "engaged"], dtype=float)

    def act(self, obs):
        p, dt = self.p, self.dt
        tip, tipv, f = obs[SL_TIP], obs[SL_TIPV], obs[SL_F]
        hole_obs = obs[SL_HOLE]
        if self.sp is None:
            self.sp = tip.copy()
            self.hole = hole_obs.copy()
        else:
            self.hole += p.hole_alpha * (hole_obs - self.hole)
        hole = self.hole
        depth = SOCKET_TOP - tip[2]

        if self.phase == "approach":
            goal = np.array([hole[0], hole[1], hole[2] + p.approach_h])
            self.sp = _move_toward(self.sp, goal, p.v_approach, dt)
            if np.linalg.norm(tip - goal) < p.approach_tol and np.linalg.norm(tipv) < 0.02:
                self.phase = "insert"
        if self.phase == "insert":
            if depth > 0.002:
                self.phase = "engaged"
                self.engaged_xy = tip[:2].copy()
            else:
                if not self.search and f[2] > p.f_contact and depth > -0.002:
                    self.search, self.theta = True, 2 * math.pi  # start one turn out
                push = p.push_search if self.search else p.push
                z = max(self.sp[2] - p.v_insert * dt, tip[2] - push)
                caught = self.search and depth > 0.0003
                self.caught_t = self.caught_t + dt if caught else 0.0
                if caught and self.caught_t < p.caught_wait:
                    # Tip has started into the hole: stop searching, comply laterally, push.
                    self.sp = np.array([tip[0], tip[1], z])
                else:
                    off = np.zeros(2)
                    if self.search:
                        r = p.spiral_pitch * self.theta / (2 * math.pi)
                        if r > p.spiral_rmax:
                            self.theta, r = 2 * math.pi, p.spiral_pitch
                        self.theta += p.spiral_speed * dt / max(r, 5e-4)
                        off = r * np.array([math.cos(self.theta), math.sin(self.theta)])
                    self.sp = np.array([hole[0] + off[0], hole[1] + off[1], z])
        if self.phase == "engaged":
            # Compliant in xy: follow wherever the hole walls guide the peg.
            self.engaged_xy += 0.2 * (tip[:2] - self.engaged_xy)
            z = max(self.sp[2] - p.v_insert * dt, tip[2] - p.push)
            self.sp = np.array([self.engaged_xy[0], self.engaged_xy[1], z])

        # Outer-loop integral on xy tracking error (compensates sag / weak motors) in free space.
        if self.phase == "approach":
            self.integ += p.xy_integral * dt * (self.sp - tip)
        elif self.phase == "insert" and f[2] < p.f_contact:
            self.integ[:2] += p.xy_integral * dt * (self.sp[:2] - tip[:2])
        self.integ = np.clip(self.integ, -0.01, 0.01)
        target = self.sp.copy()
        target[:2] += self.integ[:2]
        if self.phase == "approach":
            target[2] += self.integ[2]
        target += self.residual(obs, self.phase_vec())
        return self.servo.torque(obs, target)


# ----------------------------------------------------------------------------------------
# Learned: scripted skill + learned skill parameters + linear residual policy (CEM-trained)
# ----------------------------------------------------------------------------------------
TUNED = ("v_approach", "approach_h", "v_insert", "push", "push_search", "f_contact", "spiral_pitch",
         "spiral_speed", "spiral_rmax", "caught_wait", "xy_integral")
N_FEAT = 14
RESIDUAL_MAX = 0.002  # m
N_PARAMS = len(TUNED) + 3 * N_FEAT


def features(obs, hole_est, phase_vec) -> np.ndarray:
    tip = obs[SL_TIP]
    phi = np.empty(N_FEAT)
    phi[0:2] = (tip[:2] - hole_est[:2]) / 0.005
    phi[2:5] = obs[SL_F] / 10.0
    phi[5:8] = obs[SL_TIPV] / 0.05
    phi[8:12] = phase_vec
    phi[12] = (SOCKET_TOP - tip[2]) / 0.01
    phi[13] = 1.0
    return phi


class LearnedController(ScriptedController):
    """Scripted skill whose parameters and a residual setpoint correction are learned.

    ``theta`` layout: ``len(TUNED)`` log-multipliers on ``SkillParams`` defaults, then a
    3 x N_FEAT matrix W; residual = RESIDUAL_MAX * tanh(W @ features).
    """
    name = "learned"

    def __init__(self, theta: np.ndarray | None = None, weights: str | None = None):
        if theta is None:
            path = weights or DEFAULT_WEIGHTS
            theta = load_weights(path) if os.path.exists(path) else np.zeros(N_PARAMS)
            self.untrained = not os.path.exists(path)
        else:
            self.untrained = False
        self.theta = np.asarray(theta, dtype=float)
        base = SkillParams()
        mult = np.exp(np.clip(self.theta[:len(TUNED)], -1.4, 1.4))
        params = SkillParams(**{**base.__dict__, **{k: getattr(base, k) * m for k, m in zip(TUNED, mult)}})
        self.W = self.theta[len(TUNED):].reshape(3, N_FEAT)
        super().__init__(params)

    def residual(self, obs, phase_vec):
        return RESIDUAL_MAX * np.tanh(self.W @ features(obs, self.hole, phase_vec))


def save_weights(path: str, theta: np.ndarray, **meta) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    np.savez(path, theta=np.asarray(theta, dtype=np.float64), tuned=np.array(TUNED), n_feat=N_FEAT,
             **{k: np.asarray(v) for k, v in meta.items()})


def load_weights(path: str = DEFAULT_WEIGHTS) -> np.ndarray:
    with np.load(path) as z:
        theta = z["theta"]
    if theta.shape != (N_PARAMS,):
        raise ValueError(f"{path}: expected {N_PARAMS} parameters, got {theta.shape}")
    return theta


# Training distribution: the conditions a Mars assembly robot will meet.
TRAIN_RANGES = {"tolerance_mm": (0.1, 0.6), "noise_mm": (0.0, 3.5), "degradation": (0.2, 1.0)}
TRAIN_TIME_LIMIT = 20.0


def episode_score(info) -> float:
    s = 1.0 if info["success"] else 0.0
    bad = 0.5 if info["outcome"] in ("jammed", "overforce") else 0.0
    return s - 0.02 * info["time_s"] - bad


_ENV_CACHE: dict = {}


def _env(kw: tuple):
    from .env import PegInHoleEnv
    if kw not in _ENV_CACHE:
        _ENV_CACHE[kw] = PegInHoleEnv(**dict(kw))
    return _ENV_CACHE[kw]


def _eval_theta(args) -> float:
    from .env import rollout
    theta, episodes = args
    ctrl = LearnedController(theta)
    return float(np.mean([episode_score(rollout(_env(kw), ctrl, seed)) for kw, seed in episodes]))


def sample_conditions(rng: np.random.Generator, n: int, time_limit_s: float = TRAIN_TIME_LIMIT) -> list:
    eps = []
    for _ in range(n):
        kw = {k: round(float(rng.uniform(lo, hi)), 2) for k, (lo, hi) in TRAIN_RANGES.items()}
        kw["time_limit_s"] = time_limit_s
        eps.append((tuple(sorted(kw.items())), int(rng.integers(2**31))))
    return eps


def _pool(workers: int):
    import multiprocessing as mp
    return mp.get_context("fork").Pool(workers) if workers > 1 else None


def train(seed: int = 0, iters: int = 14, pop: int = 24, elite: int = 6, episodes: int = 6,
          init_std: float = 0.3, workers: int | None = None, out: str | None = DEFAULT_WEIGHTS,
          validate: int = 96, verbose: bool = True) -> dict:
    """Cross-entropy method over ``theta``. Deterministic for a given seed (any worker count).

    Each iteration samples ``pop`` candidates and scores all of them on the same ``episodes``
    randomly drawn conditions (common random numbers). Finally the CEM mean is compared with the
    untrained skill (theta = 0) on ``validate`` held-out episodes, and saved only if it is better.
    """
    t0 = time.time()
    workers = workers or min(4, os.cpu_count() or 1)
    rng = np.random.default_rng(seed)
    mu = np.zeros(N_PARAMS)
    std = np.full(N_PARAMS, init_std)
    std[:len(TUNED)] = 0.25
    history = []
    pool = _pool(workers)
    mapper = pool.map if pool else map
    try:
        for it in range(iters):
            eps = sample_conditions(rng, episodes)
            cands = mu + std * rng.standard_normal((pop, N_PARAMS))
            cands[0] = mu  # always re-score the current mean
            scores = np.array(list(mapper(_eval_theta, [(c, eps) for c in cands])))
            idx = np.argsort(-scores)[:elite]
            mu = cands[idx].mean(axis=0)
            std = np.sqrt(cands[idx].var(axis=0) + 0.02 ** 2)  # small floor keeps exploring
            history.append((float(scores[0]), float(scores[idx].mean())))
            if verbose:
                print(f"iter {it + 1:2d}/{iters}  mean-policy score {scores[0]:+.3f}  "
                      f"elite {scores[idx].mean():+.3f}  ({time.time() - t0:5.1f}s)", flush=True)
        val_eps = sample_conditions(np.random.default_rng(seed + 10_000), validate)
        chunks = [val_eps[i::workers] for i in range(workers)]
        s_new = float(np.mean(list(mapper(_eval_theta, [(mu, c) for c in chunks]))))
        s_base = float(np.mean(list(mapper(_eval_theta, [(np.zeros(N_PARAMS), c) for c in chunks]))))
    finally:
        if pool:
            pool.close()
            pool.join()
    elapsed = time.time() - t0
    result = {"theta": mu, "val_score": s_new, "val_score_scripted": s_base, "train_seconds": elapsed,
              "history": history, "improved": s_new > s_base}
    if verbose:
        print(f"validation ({validate} held-out episodes): learned {s_new:+.3f}  scripted {s_base:+.3f}  "
              f"train time {elapsed:.1f}s")
    if out and result["improved"]:
        save_weights(out, mu, seed=seed, iters=iters, pop=pop, episodes=episodes, val_score=s_new,
                     val_score_scripted=s_base, train_seconds=elapsed, history=np.array(history))
        if verbose:
            print(f"saved {out}")
    elif out and verbose:
        print("learned policy did not beat the scripted skill on validation; weights not saved")
    return result


# ----------------------------------------------------------------------------------------
# Teleoperation with light delay
# ----------------------------------------------------------------------------------------
@dataclass
class Operator:
    """A careful human operator. Times in s, distances in m."""
    reaction_s: float = 0.3  # perception + decision lag on top of the light delay
    rate_hz: float = 10.0  # how often a continuous-mode operator updates the hand controller
    perception_mm: float = 0.3  # error judging peg-vs-hole alignment by eye, per look
    motor_frac: float = 0.05  # hand placement error proportional to move size
    gain: float = 2.0  # 1/s: continuous-mode correction gain without delay
    stability: float = 0.7  # operator keeps gain * round_trip_lag below this (stays stable)
    v_down: float = 0.02  # m/s descent speed without delay
    safe_overtravel: float = 0.003  # m the operator lets the setpoint run ahead unseen
    settle_s: float = 0.5  # move-and-wait: watch this long after a move appears to finish
    approach_h: float = 0.02
    max_lead: float = 0.03  # m: continuous mode, hand controller ahead of the seen robot


_SEARCH_GRID = sorted(((0.0005 * i, 0.0005 * j) for i in range(-8, 9) for j in range(-8, 9)),
                      key=lambda p: (p[0] ** 2 + p[1] ** 2, math.atan2(p[1], p[0])))


class TeleopController:
    """Operator on Earth (or in orbit) driving the arm through a delayed link.

    The operator sees observations ``delay_s`` old (plus their reaction time) and their
    commands (Cartesian setpoints for the onboard servo) arrive ``delay_s`` later.
    ``mode="continuous"``: the operator moves a hand controller in real time, correcting
    what they see. ``mode="move_wait"``: the operator sends a discrete move, then waits to
    see it finish before deciding the next one.
    """

    def __init__(self, delay_s: float = 0.0, mode: str = "continuous", operator: Operator | None = None):
        assert mode in ("continuous", "move_wait")
        self.delay = float(delay_s)
        self.mode = mode
        self.op = operator or Operator()
        self.servo = Servo()
        self.name = f"teleop_{mode}"
        self.latency_s = 0.0

    def reset(self, env):
        self.servo.reset(env)
        self.dt = env.dt
        self.rng = np.random.default_rng(int(env.np_random.integers(2**31)))
        self.obs_buf: deque = deque()
        self.cmd_buf: deque = deque()
        self.sp = None
        self.master = None
        self.next_op_t = 0.0
        self.busy_until = -1.0
        self.state = "approach"
        self.search_k = 0
        self.last_goal = None
        lag = 2 * self.delay + self.op.reaction_s
        self.k = min(self.op.gain, self.op.stability / lag)
        self.v_down = min(self.op.v_down, self.op.safe_overtravel / lag)

    # what the operator perceives
    def _look(self, o):
        hole = o[SL_HOLE].copy()
        hole[:2] += self.rng.normal(0, self.op.perception_mm / 1000, 2)
        return o[SL_TIP].copy(), o[SL_TIPV].copy(), hole, o[SL_F].copy()

    def _send(self, t, sp, vmax=0.1):
        self.cmd_buf.append((t + self.delay, sp.copy(), vmax))

    def act(self, obs):
        t = obs[SL_T]
        if self.sp is None:
            self.sp = obs[SL_TIP].copy()
            self.master = self.sp.copy()
            self.goal = self.sp.copy()
            self.vmax = 0.1
        self.obs_buf.append(obs)
        view_t = t - self.delay - self.op.reaction_s
        while len(self.obs_buf) > 1 and self.obs_buf[1][SL_T] <= view_t + 1e-9:
            self.obs_buf.popleft()
        view = self.obs_buf[0] if self.obs_buf[0][SL_T] <= view_t + 1e-9 else None
        if view is not None and t + 1e-9 >= self.next_op_t:
            if self.mode == "continuous":
                self._continuous(t, view)
                self.next_op_t = t + 1.0 / self.op.rate_hz
            elif t >= self.busy_until:
                self._move_wait(t, view)
        while self.cmd_buf and self.cmd_buf[0][0] <= t + 1e-9:
            _, self.goal, self.vmax = self.cmd_buf.popleft()
        # Onboard trajectory generator: move the servo setpoint toward the latest command.
        self.sp = _move_toward(self.sp, self.goal, self.vmax, self.dt)
        return self.servo.torque(obs, self.sp)

    def _continuous(self, t, view):
        op = self.op
        tip, tipv, hole, f = self._look(view)
        depth = SOCKET_TOP - tip[2]
        dt = 1.0 / op.rate_hz
        m = self.master
        if depth > 0.002:
            self.state = "engaged"
        if self.state == "approach":
            goal = hole + np.array([0, 0, op.approach_h])
            m += np.clip(self.k * (goal - tip) * dt, -0.08 * dt, 0.08 * dt)
            if np.linalg.norm(goal[:2] - tip[:2]) < 0.0006 and abs(goal[2] - tip[2]) < 0.003:
                self.state = "insert"
        elif self.state == "insert":
            m[:2] += self.k * (hole[:2] - tip[:2]) * dt
            if f[2] > 4.0 and depth < 0.0005:
                # Resting on the rim: back off a little and nudge somewhere nearby.
                m[2] = tip[2] + 0.001
                m[:2] += self.rng.normal(0, 0.0006, 2)
            else:
                near = tip[2] - SOCKET_TOP < 0.005 + op.safe_overtravel
                m[2] -= (self.v_down if near else op.v_down) * dt
                # Until the peg is seen going in, never command deeper than just below the rim.
                m[2] = max(m[2], min(tip[2], SOCKET_TOP) - op.safe_overtravel)
        else:  # engaged: let the hole guide the peg, keep pushing gently
            m[:2] += 0.5 * (tip[:2] - m[:2])
            m[2] = max(m[2] - 0.5 * op.v_down * dt, tip[2] - op.safe_overtravel)
        m += self.rng.normal(0, op.motor_frac * 0.002, 3)  # hand tremor
        # Don't run the hand controller far ahead of what the robot is seen doing.
        lead = m - tip
        n = float(np.linalg.norm(lead))
        max_lead = op.max_lead * (2 if self.state == "approach" else 1)
        if n > max_lead:
            m[:] = tip + lead * (max_lead / n)
        self._send(t, m)

    def _move_wait(self, t, view):
        op = self.op
        tip, tipv, hole, f = self._look(view)
        if np.linalg.norm(tipv) > 0.005:  # still moving: keep watching
            self.busy_until = t + 0.1
            return
        depth = SOCKET_TOP - tip[2]
        exy = hole[:2] - tip[:2]
        last = self.last_goal if self.last_goal is not None else tip
        if depth > 0.002:
            goal = np.array([tip[0], tip[1], tip[2] - 0.003])  # in: push 3 mm at a time
        elif f[2] > 1.5:
            # Resting on the rim: correct by the error seen (the arm deflects under load),
            # plus the next spot of a small search pattern.
            off = np.array(_SEARCH_GRID[self.search_k % 25])
            self.search_k += 1
            goal = np.array([last[0] + exy[0] + off[0], last[1] + exy[1] + off[1], SOCKET_TOP - 0.002])
        elif np.linalg.norm(exy) > 0.0005 or tip[2] > SOCKET_TOP + op.approach_h + 0.005:
            z = SOCKET_TOP + (op.approach_h if np.linalg.norm(exy) > 0.003 else 0.002)
            base = last[:2] if np.linalg.norm(exy) < 0.01 else tip[:2]
            goal = np.array([base[0] + exy[0], base[1] + exy[1], z])
        else:
            goal = np.array([last[0], last[1], SOCKET_TOP - 0.004])  # aligned, hovering: press in
        move = goal - tip
        goal = goal + np.abs(move) * self.rng.normal(0, op.motor_frac, 3)
        goal[:2] += self.rng.normal(0, 0.0002, 2)
        self.last_goal = goal.copy()
        # Moves that end in contact are sent as slow guarded moves.
        v = 0.01 if goal[2] < SOCKET_TOP + 0.001 else 0.1
        self._send(t, goal, v)
        # Wait for the command to arrive, the move to finish, and the result to come back.
        dur = np.linalg.norm(move) / v + op.settle_s
        self.busy_until = t + 2 * self.delay + op.reaction_s + dur


# ----------------------------------------------------------------------------------------
# Supervisory control: one goal, onboard skill executes
# ----------------------------------------------------------------------------------------
class SupervisoryController:
    """Operator sends "insert part at socket X" once; an onboard skill does the rest.

    The light delay only adds a fixed latency (``latency_s``, one-way) before the onboard
    skill starts. Benchmarks add it to the measured time instead of simulating an idle arm.
    """

    def __init__(self, delay_s: float = 0.0, skill=None):
        self.skill = skill or ScriptedController()
        self.latency_s = float(delay_s)
        self.name = f"supervisory_{self.skill.name}"

    def reset(self, env):
        self.skill.reset(env)

    def act(self, obs):
        return self.skill.act(obs)


CONTROLLERS = ("scripted", "learned", "teleop", "teleop_mw", "supervisory", "supervisory_learned")


def make_controller(name: str, delay_s: float = 0.0, **kw):
    """Factory: name in CONTROLLERS. Teleop / supervisory use ``delay_s`` (one-way, seconds)."""
    if name == "scripted":
        return ScriptedController(**kw)
    if name == "learned":
        return LearnedController(**kw)
    if name in ("teleop", "teleop_continuous"):
        return TeleopController(delay_s, "continuous", **kw)
    if name in ("teleop_mw", "teleop_move_wait"):
        return TeleopController(delay_s, "move_wait", **kw)
    if name == "supervisory":
        return SupervisoryController(delay_s, ScriptedController(**kw))
    if name == "supervisory_learned":
        return SupervisoryController(delay_s, LearnedController(**kw))
    raise ValueError(f"unknown controller {name!r}; choose from {CONTROLLERS}")
