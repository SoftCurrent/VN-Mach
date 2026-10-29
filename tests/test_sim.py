from collections import Counter
from types import SimpleNamespace

import pytest

from vnmach.bom import closure, default_recipes
from vnmach.director import HeuristicDirector, StaticDirector, sanitize
from vnmach.sim import Config, Simulation


def run(director=None, **kw):
    kw.setdefault("ticks", 3000)
    sim = Simulation(Config(**kw), director=director)
    sim.run()
    return sim


def test_closure_chips_are_the_only_vitamin():
    without = closure(default_recipes(chip_fab=False))
    assert without.vitamins == {"chip": 1}
    assert without.mass_closure > 0.95
    with_fab = closure(default_recipes(chip_fab=True))
    assert with_fab.vitamins == {}
    assert with_fab.parts_closure == 1.0


def test_vitamin_wall_caps_births_at_imported_chips():
    sim = run(seed_vitamins=5, ticks=4000)
    assert sim.stats["births"] <= 5
    assert sim.stats["chips_made"] == 0


def test_prebuilt_fab_breaks_the_vitamin_wall():
    sim = run(seed_vitamins=0, chip_fab=True, ticks=4000)
    assert sim.stats["chips_made"] > 0
    assert sim.stats["births"] > 0


def test_heuristic_director_bootstraps_a_fab_from_imported_chips():
    sim = run(HeuristicDirector(), ticks=3000)
    assert sim.world.n_fabs >= 1
    assert sim.stats["vitamins_used"] <= 20


def test_reserved_chips_go_to_fab_kits_not_children():
    sim = run(StaticDirector(build_fab=True, fab_share=0.0, chip_reserve=20), ticks=1500)
    assert sim.stats["births"] == 0  # nobody builds kits, reserve blocks children


def test_protected_governor_never_mutates():
    sim = run(HeuristicDirector(), mutation_rate=1.0, governor_protected=True, ticks=4000)
    assert all(r.genome.max_children == 3 for r in sim.robots)


def test_unprotected_governor_drifts():
    drifted = False
    for seed in range(4):
        sim = run(HeuristicDirector(), seed=seed, mutation_rate=1.0, governor_protected=False, ticks=5000)
        if any(r.genome.max_children != 3 for r in sim.robots):
            drifted = True
            break
    assert drifted


def test_self_repair_replaces_a_broken_part():
    sim = Simulation(Config(ticks=50, mtbf=1e12))
    r = sim.robots[0]
    r.built_defects["motor"] = 1
    r.inventory["motor"] = 1
    assert sim.controller.decide(r, sim) == ("repair", "motor")
    for _ in range(40):
        sim.step()
    assert r.built_defects["motor"] == 0
    assert sim.stats["repairs"] == 1


def test_sanitize_clamps_planner_output():
    out = sanitize({"replicate": 1, "target_fabs": 999, "fab_share": 3, "chip_reserve": -4, "harvest_focus": "gold"})
    assert out == {"replicate": True, "target_fabs": 50, "fab_share": 1.0, "chip_reserve": 0, "harvest_focus": None}


def test_runs_are_deterministic_per_seed():
    a = run(HeuristicDirector(), seed=3, ticks=1500).history
    b = run(HeuristicDirector(), seed=3, ticks=1500).history
    assert a == b


class FakeMessages:
    def __init__(self, text, stop_reason="end_turn"):
        self.text, self.stop_reason, self.calls = text, stop_reason, []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=self.text)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


def fake_claude(text, stop_reason="end_turn"):
    pytest.importorskip("anthropic")
    from vnmach.llm import ClaudeDirector

    d = ClaudeDirector.__new__(ClaudeDirector)
    import anthropic

    d.anthropic = anthropic
    d.model, d.effort, d.total_ticks, d.history_window, d.verbose = "claude-opus-5-5", "low", 1000, 6, False
    d.log, d.usage = [], Counter()
    msgs = FakeMessages(text, stop_reason)
    d.client = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    return d, msgs


def test_claude_director_parses_and_sanitizes():
    d, msgs = fake_claude('{"rationale":"r","replicate":true,"build_fab":true,"target_fabs":3,'
                          '"fab_share":0.5,"chip_reserve":4,"harvest_focus":null}')
    sim = Simulation(Config(ticks=600, director_interval=250), director=d)
    sim.run()
    assert sim.policy["target_fabs"] == 3 and sim.policy["chip_reserve"] == 4
    assert len(msgs.calls) == 3
    assert msgs.calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert "Your recent decisions" in msgs.calls[1]["messages"][0]["content"]


def test_claude_director_refusal_keeps_policy():
    d, _ = fake_claude("", stop_reason="refusal")
    assert d.decide(Simulation(Config()).snapshot()) == {}
    assert d.usage["errors"] == 1
