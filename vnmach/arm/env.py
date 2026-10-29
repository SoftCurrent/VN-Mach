"""MuJoCo peg-in-hole environment: a 4-DoF arm seats a part into a socket.

The part is a cylindrical peg rigidly held by the end effector (a stand-in for
"seat a motor into a frame socket"). The socket is a square hole built from
boxes, with an optional 45-degree chamfer at the rim.

Units: the API speaks millimetres for tolerances and noise (that is how
machinists think), metres / seconds / newtons everywhere else.

Arm: base yaw, shoulder pitch, elbow pitch, wrist pitch. All four joints are
torque motors; the action is normalised torque in [-1, 1] per joint. Actuator
wear (``degradation``) multiplies delivered torque, so a worn motor is weaker
than the controller thinks it is.

Observation (float64, length ``OBS_DIM``):
    [0:4]   joint positions (rad)
    [4:8]   joint velocities (rad/s)
    [8:11]  peg tip position (m, world), from joint encoders: exact
    [11:14] peg tip velocity (m/s)
    [14:17] observed hole position (m, centre of the hole at the rim), from a
            camera: biased by ``noise_mm`` (fixed per episode, i.e. dust) plus
            small per-frame jitter
    [17:20] wrist force/torque sensor: contact force on the peg (N, world)
    [20]    elapsed time (s)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import gymnasium as gym
import mujoco
import numpy as np

MARS_GRAVITY = 3.71

# Kinematics (metres). Shared with the controllers' analytic IK.
SHOULDER_Z = 0.25
L1 = 0.25  # upper arm
L2 = 0.25  # forearm
LW = 0.05  # horizontal wrist link
LP = 0.08  # peg length below the wrist link (tip offset)
PEG_R = 0.010  # peg radius
GEAR = np.array([10.0, 20.0, 15.0, 5.0])  # Nm at |ctrl| = 1

# Socket geometry (metres).
SOCKET_HALF = 0.05
SOCKET_TOP = 0.06
HOLE_DEPTH = 0.035
INSERT_DEPTH = 0.025  # success: peg tip this far below the rim
HOLE_NOMINAL = np.array([0.35, 0.05])
START_NOMINAL = np.array([0.22, -0.12, 0.17])

OBS_DIM = 21
SL_Q, SL_QD, SL_TIP, SL_TIPV, SL_HOLE, SL_F, SL_T = (
    slice(0, 4), slice(4, 8), slice(8, 11), slice(11, 14), slice(14, 17), slice(17, 20), 20)


def _box(name, center, half, euler=None):
    e = f' euler="{euler[0]:.6f} {euler[1]:.6f} {euler[2]:.6f}"' if euler is not None else ""
    return (f'<geom name="{name}" type="box" pos="{center[0]:.6f} {center[1]:.6f} {center[2]:.6f}" '
            f'size="{half[0]:.6f} {half[1]:.6f} {half[2]:.6f}"{e} class="socket"/>')


def socket_geoms(clearance_m: float, chamfer_m: float) -> str:
    """Square hole of half-width PEG_R + clearance/2, optional chamfer, built from boxes."""
    h = PEG_R + clearance_m / 2
    H, T, B = SOCKET_HALF, SOCKET_TOP, SOCKET_TOP - HOLE_DEPTH
    ch = chamfer_m
    g = [_box("socket_floor", (0, 0, B / 2), (H, H, B / 2))]
    zl = (B + T - ch) / 2
    hl = (T - ch - B) / 2
    for i, s in enumerate((1, -1)):
        # lower walls (full height minus chamfer band)
        g.append(_box(f"wall_x{i}", (s * (h + H) / 2, 0, zl), ((H - h) / 2, H, hl)))
        g.append(_box(f"wall_y{i}", (0, s * (h + H) / 2, zl), (H, (H - h) / 2, hl)))
        if ch > 0:
            # upper walls set back by the chamfer, plus a 45-degree prism filling the bevel
            hu = h + ch
            g.append(_box(f"top_x{i}", (s * (hu + H) / 2, 0, T - ch / 2), ((H - hu) / 2, H, ch / 2)))
            g.append(_box(f"top_y{i}", (0, s * (hu + H) / 2, T - ch / 2), (H, (H - hu) / 2, ch / 2)))
            a = ch / math.sqrt(2)
            g.append(_box(f"chamfer_x{i}", (s * (h + ch), 0, T - ch), (a, H, a), (0, math.pi / 4, 0)))
            g.append(_box(f"chamfer_y{i}", (0, s * (h + ch), T - ch), (H, a, a), (math.pi / 4, 0, 0)))
    return "\n        ".join(g)


def build_mjcf(clearance_mm: float = 1.0, chamfer_mm: float = 1.0, gravity: float = MARS_GRAVITY,
               friction: float = 0.3) -> str:
    sock = socket_geoms(clearance_mm / 1000, chamfer_mm / 1000)
    return f"""
<mujoco model="vnmach_peg_in_hole">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="0.001" gravity="0 0 {-gravity}" integrator="implicitfast" cone="elliptic" impratio="2">
    <flag multiccd="enable"/>
  </option>
  <default>
    <joint damping="0.5" armature="0.05" limited="false"/>
    <geom contype="0" conaffinity="0" rgba="0.7 0.7 0.75 1"/>
    <default class="socket">
      <geom contype="1" conaffinity="1" friction="{friction} 0.005 0.0001" solref="0.004 1"
            solimp="0.95 0.99 0.0005" rgba="0.6 0.4 0.3 1"/>
    </default>
  </default>
  <worldbody>
    <light pos="0 0 2"/>
    <geom name="floor" type="plane" size="1 1 0.01" contype="1" conaffinity="1" rgba="0.8 0.5 0.3 1"/>
    <geom name="pedestal" type="cylinder" size="0.04 {SHOULDER_Z / 2}" pos="0 0 {SHOULDER_Z / 2}"/>
    <body name="turret" pos="0 0 {SHOULDER_Z}">
      <joint name="yaw" axis="0 0 1" armature="0.1"/>
      <geom type="sphere" size="0.03" mass="0.5"/>
      <body name="upper">
        <joint name="shoulder" axis="0 1 0"/>
        <geom type="capsule" fromto="0 0 0 {L1} 0 0" size="0.02" mass="0.5"/>
        <body name="fore" pos="{L1} 0 0">
          <joint name="elbow" axis="0 1 0"/>
          <geom type="capsule" fromto="0 0 0 {L2} 0 0" size="0.015" mass="0.4"/>
          <body name="wrist" pos="{L2} 0 0">
            <joint name="wrist" axis="0 1 0" armature="0.03"/>
            <geom type="capsule" fromto="0 0 0 {LW} 0 0" size="0.012" mass="0.15"/>
            <body name="peg" pos="{LW} 0 0">
              <geom name="peg" type="cylinder" size="{PEG_R} {LP / 2}" pos="0 0 {-LP / 2}" mass="0.3"
                    contype="1" conaffinity="1" friction="{friction} 0.005 0.0001" solref="0.004 1"
                    solimp="0.95 0.99 0.0005" rgba="0.3 0.3 0.9 1"/>
              <site name="tip" pos="0 0 {-LP}" size="0.003"/>
            </body>
          </body>
        </body>
      </body>
    </body>
    <body name="socket" pos="{HOLE_NOMINAL[0]} {HOLE_NOMINAL[1]} 0">
        {sock}
    </body>
  </worldbody>
  <actuator>
    <motor name="m_yaw" joint="yaw" gear="{GEAR[0]}" ctrlrange="-1 1"/>
    <motor name="m_shoulder" joint="shoulder" gear="{GEAR[1]}" ctrlrange="-1 1"/>
    <motor name="m_elbow" joint="elbow" gear="{GEAR[2]}" ctrlrange="-1 1"/>
    <motor name="m_wrist" joint="wrist" gear="{GEAR[3]}" ctrlrange="-1 1"/>
  </actuator>
</mujoco>
"""


@lru_cache(maxsize=16)
def _model(clearance_mm: float, chamfer_mm: float, gravity: float, friction: float) -> mujoco.MjModel:
    return mujoco.MjModel.from_xml_string(build_mjcf(clearance_mm, chamfer_mm, gravity, friction))


def ik(tip: np.ndarray) -> np.ndarray:
    """Analytic IK (elbow up, peg vertical) for a tip position in world coordinates."""
    x, y, z = tip
    yaw = math.atan2(y, x)
    rw = math.hypot(x, y) - LW
    d = SHOULDER_Z - (z + LP)  # wrist point below shoulder (positive down)
    c2 = (rw * rw + d * d - L1 * L1 - L2 * L2) / (2 * L1 * L2)
    c2 = min(1.0, max(-1.0, c2))
    t2 = math.acos(c2)
    t1 = math.atan2(d, rw) - math.atan2(L2 * math.sin(t2), L1 + L2 * math.cos(t2))
    return np.array([yaw, t1, t2, -(t1 + t2)])


def fk(q: np.ndarray) -> np.ndarray:
    yaw, t1, t2, t3 = q
    p1, p2, p3 = t1, t1 + t2, t1 + t2 + t3
    r = L1 * math.cos(p1) + L2 * math.cos(p2) + LW * math.cos(p3) - LP * math.sin(p3)
    z = SHOULDER_Z - L1 * math.sin(p1) - L2 * math.sin(p2) - LW * math.sin(p3) - LP * math.cos(p3)
    return np.array([r * math.cos(yaw), r * math.sin(yaw), z])


@dataclass(frozen=True)
class EnvParams:
    tolerance_mm: float = 1.0  # diametral clearance between peg and hole
    noise_mm: float = 0.0  # std of the camera's hole-position error (per axis, per episode)
    degradation: float = 1.0  # actuator gain multiplier (1 = new, 0.5 = badly worn)
    gravity: float = MARS_GRAVITY
    time_limit_s: float = 30.0
    chamfer_mm: float = 1.0
    friction: float = 0.3
    control_hz: float = 100.0
    force_limit_n: float = 40.0  # sustained contact force above this damages the part
    jam_force_n: float = 10.0  # pushing this hard with no progress while engaged = jammed
    jam_window_s: float = 1.5
    hole_jitter_m: float = 0.02  # true hole position varies per episode by this much
    start_jitter_m: float = 0.02


class PegInHoleEnv(gym.Env):
    """Gymnasium-style peg-in-hole insertion under Mars conditions."""

    metadata = {"render_modes": []}

    def __init__(self, tolerance_mm: float = 1.0, noise_mm: float = 0.0, degradation: float = 1.0,
                 gravity: float = MARS_GRAVITY, time_limit_s: float = 30.0, seed: int | None = None, **kw):
        self.params = EnvParams(tolerance_mm=tolerance_mm, noise_mm=noise_mm, degradation=degradation,
                                gravity=gravity, time_limit_s=time_limit_s, **kw)
        p = self.params
        self.model = _model(round(p.tolerance_mm, 6), round(p.chamfer_mm, 6), round(p.gravity, 6),
                            round(p.friction, 6))
        self.data = mujoco.MjData(self.model)
        self.dt = 1.0 / p.control_hz
        self.n_sub = max(1, round(self.dt / self.model.opt.timestep))
        self.peg_geom = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "peg")
        self.tip_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "tip")
        self.socket_body = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "socket")
        self.action_space = gym.spaces.Box(-1.0, 1.0, shape=(4,), dtype=np.float64)
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(OBS_DIM,), dtype=np.float64)
        self._f6 = np.zeros(6)
        self._seed = seed
        self.hole_pos = np.zeros(3)
        self._t = 0.0

    # -- helpers ---------------------------------------------------------------------------
    def contact_force(self) -> np.ndarray:
        """Total contact force acting on the peg, world frame (N)."""
        d, m = self.data, self.model
        total = np.zeros(3)
        for i in range(d.ncon):
            c = d.contact[i]
            if c.geom1 == self.peg_geom:
                sign = -1.0
            elif c.geom2 == self.peg_geom:
                sign = 1.0
            else:
                continue
            mujoco.mj_contactForce(m, d, i, self._f6)
            fr = c.frame.reshape(3, 3)
            total += sign * (fr.T @ self._f6[:3])
        return total

    def tip(self) -> np.ndarray:
        return self.data.site_xpos[self.tip_site].copy()

    def depth(self) -> float:
        """How far the tip is below the rim (m); negative when above."""
        return SOCKET_TOP - self.data.site_xpos[self.tip_site][2]

    def _obs(self, force: np.ndarray) -> np.ndarray:
        d = self.data
        o = np.empty(OBS_DIM)
        o[SL_Q] = d.qpos
        o[SL_QD] = d.qvel
        o[SL_TIP] = d.site_xpos[self.tip_site]
        jac = np.zeros((3, self.model.nv))
        mujoco.mj_jacSite(self.model, d, jac, None, self.tip_site)
        o[SL_TIPV] = jac @ d.qvel
        o[SL_HOLE] = self.hole_pos + self._hole_bias
        if self.params.noise_mm > 0:
            o[14:16] += self.np_random.normal(0, 0.25 * self.params.noise_mm / 1000, 2)
        o[SL_F] = force + self.np_random.normal(0, 0.05, 3)
        o[SL_T] = self._t
        return o

    # -- gym API ---------------------------------------------------------------------------
    def reset(self, *, seed: int | None = None, options: dict | None = None):
        if seed is None and self._seed is not None:
            seed, self._seed = self._seed, None
        super().reset(seed=seed)
        p, m, d = self.params, self.model, self.data
        rng = self.np_random
        mujoco.mj_resetData(m, d)
        hxy = HOLE_NOMINAL + rng.uniform(-p.hole_jitter_m, p.hole_jitter_m, 2)
        m.body_pos[self.socket_body] = [hxy[0], hxy[1], 0.0]
        self.hole_pos = np.array([hxy[0], hxy[1], SOCKET_TOP])
        # Dust: a per-episode bias in where the camera thinks the hole is (x, y only:
        # the socket top height is known from the CAD model).
        b = rng.normal(0, p.noise_mm / 1000, 2) if p.noise_mm > 0 else np.zeros(2)
        self._hole_bias = np.array([b[0], b[1], 0.0])
        start = START_NOMINAL + rng.uniform(-p.start_jitter_m, p.start_jitter_m, 3)
        d.qpos[:] = ik(start)
        mujoco.mj_forward(m, d)
        self._t = 0.0
        self._over_steps = 0
        self._depth_hist: list[float] = []
        self.peak_force = 0.0
        self.outcome = None
        f = self.contact_force()
        return self._obs(f), self._info(f)

    def _info(self, f):
        return {
            "time_s": self._t,
            "depth_mm": self.depth() * 1000,
            "contact_force_n": float(np.linalg.norm(f)),
            "peak_force_n": self.peak_force,
            "success": self.outcome == "success",
            "jammed": self.outcome == "jammed",
            "overforce": self.outcome == "overforce",
            "outcome": self.outcome,
            "hole_true": self.hole_pos.copy(),
        }

    def step(self, action):
        p, m, d = self.params, self.model, self.data
        a = np.clip(np.asarray(action, dtype=np.float64), -1.0, 1.0)
        d.ctrl[:] = a * p.degradation
        mujoco.mj_step(m, d, nstep=self.n_sub)
        self._t += self.dt
        f = self.contact_force()
        fn = float(np.linalg.norm(f))
        self.peak_force = max(self.peak_force, fn)
        depth = self.depth()
        tip = d.site_xpos[self.tip_site]
        lateral = math.hypot(tip[0] - self.hole_pos[0], tip[1] - self.hole_pos[1])
        engaged = depth > 0.002 and lateral < PEG_R

        terminated = False
        self._over_steps = self._over_steps + 1 if fn > p.force_limit_n else 0
        if engaged and depth >= INSERT_DEPTH:
            self.outcome, terminated = "success", True
        elif self._over_steps >= 3:  # 30 ms sustained
            self.outcome, terminated = "overforce", True
        else:
            win = max(1, round(p.jam_window_s / self.dt))
            self._depth_hist.append(depth if engaged else -1.0)
            if len(self._depth_hist) > win:
                self._depth_hist.pop(0)
                h = self._depth_hist
                if min(h) > 0 and h[-1] - h[0] < 0.0005 and fn > p.jam_force_n:
                    self.outcome, terminated = "jammed", True
        truncated = not terminated and self._t >= p.time_limit_s - 1e-9
        if truncated:
            self.outcome = "timeout"
        reward = -lateral - max(0.0, -depth) * 0.5 + (10.0 if self.outcome == "success" else 0.0) \
            - (5.0 if self.outcome in ("jammed", "overforce") else 0.0)
        return self._obs(f), reward, terminated, truncated, self._info(f)


def rollout(env: PegInHoleEnv, controller, seed: int | None = None, max_steps: int | None = None) -> dict:
    """Run one episode with ``controller`` (``reset(env)`` / ``act(obs)``); return the final info."""
    obs, info = env.reset(seed=seed)
    controller.reset(env)
    n = 0
    while True:
        obs, _, term, trunc, info = env.step(controller.act(obs))
        n += 1
        if term or trunc or (max_steps is not None and n >= max_steps):
            return info
