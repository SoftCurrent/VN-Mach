import os

import numpy as np
import pytest

pytest.importorskip("mujoco")
pytest.importorskip("gymnasium")

from vnmach.arm.bench import measure_skill, skill_profile  # noqa: E402
from vnmach.arm.controllers import (  # noqa: E402
    DEFAULT_WEIGHTS, LearnedController, ScriptedController, SupervisoryController, train,
)
from vnmach.arm.env import OBS_DIM, PegInHoleEnv, fk, ik, rollout  # noqa: E402


def test_ik_fk_round_trip():
    for tip in ([0.35, 0.05, 0.06], [0.22, -0.12, 0.17], [0.3, 0.2, 0.1]):
        assert np.allclose(fk(ik(np.array(tip))), tip, atol=1e-9)


def test_env_reset_and_step():
    env = PegInHoleEnv(time_limit_s=0.5)
    obs, info = env.reset(seed=1)
    assert obs.shape == (OBS_DIM,) and np.all(np.isfinite(obs))
    assert env.observation_space.contains(obs)
    assert info["outcome"] is None
    rng = np.random.default_rng(0)
    for k in range(60):
        obs, r, term, trunc, info = env.step(0.05 * rng.uniform(-1, 1, 4))
        assert np.all(np.isfinite(obs)) and np.isfinite(r)
        if term or trunc:
            break
    assert trunc and info["outcome"] == "timeout"
    assert obs[20] == pytest.approx(0.5)
    # Same seed, same episode.
    a, _ = env.reset(seed=7)
    b, _ = env.reset(seed=7)
    assert np.array_equal(a, b)


def test_mars_gravity_and_degradation_params():
    env = PegInHoleEnv(degradation=0.5)
    assert env.model.opt.gravity[2] == pytest.approx(-3.71)
    env.reset(seed=0)
    env.step(np.ones(4))
    assert np.allclose(env.data.ctrl, 0.5)


def test_scripted_succeeds_in_clean_conditions():
    s = measure_skill("scripted", n_trials=12, seed=0, workers=2)
    assert s.success_rate >= 0.9
    assert s.jam_rate == 0 and s.overforce_rate == 0
    assert 1.0 < s.mean_time_s < 10.0


def test_jam_or_overforce_is_detected():
    # Ram the peg straight down with full torque on the rim: the env must flag it.
    env = PegInHoleEnv(time_limit_s=5)
    ctrl = ScriptedController()

    class Rammer:
        def reset(self, env):
            ctrl.reset(env)

        def act(self, obs):
            a = ctrl.act(obs)
            if ctrl.phase != "approach":
                a = a.copy()
                a[1] = 1.0  # shoulder full torque: drives the tip down hard
            return a

    info = rollout(env, Rammer(), seed=3)
    assert info["outcome"] in ("overforce", "jammed")


def test_teleop_degrades_with_delay():
    fast = measure_skill("teleop", n_trials=6, seed=0, delay_s=0.0, time_limit_s=40, workers=4)
    slow = measure_skill("teleop", n_trials=6, seed=0, delay_s=10.0, time_limit_s=40, workers=4)
    assert fast.success_rate >= 0.66
    assert slow.success_rate < fast.success_rate
    assert slow.success_rate == 0.0


def test_supervisory_adds_fixed_latency():
    sup = SupervisoryController(1200.0, ScriptedController())
    s = measure_skill(sup, n_trials=4, seed=0, workers=1)
    base = measure_skill("scripted", n_trials=4, seed=0, workers=1)
    assert s.success_rate == base.success_rate
    assert s.mean_time_s == pytest.approx(base.mean_time_s + 1200.0)


def test_skill_profile_is_sane():
    p = skill_profile("scripted", n_trials=10, seed=0, workers=4)
    assert set(p) == {"assembly_success", "time_multiplier"}
    assert 0.8 <= p["assembly_success"] <= 1.0
    assert 0.7 < p["time_multiplier"] < 1.5
    q = skill_profile("supervisory", delay_s=1200.0, n_trials=4, seed=0, workers=4)
    assert q["time_multiplier"] > 100
    assert 0.0 <= q["assembly_success"] <= 1.0


def test_learned_weights_ship_and_run():
    assert os.path.exists(DEFAULT_WEIGHTS) and os.path.getsize(DEFAULT_WEIGHTS) < 1_000_000
    ctrl = LearnedController()
    assert not ctrl.untrained
    info = rollout(PegInHoleEnv(), ctrl, seed=0)
    assert info["success"]


def test_training_is_reproducible():
    kw = dict(seed=3, iters=1, pop=3, elite=2, episodes=2, validate=2, workers=1, out=None, verbose=False)
    a, b = train(**kw), train(**kw)
    assert np.array_equal(a["theta"], b["theta"])
