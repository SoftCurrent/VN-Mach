from collections import Counter
from types import SimpleNamespace

import pytest

from vnmach.bom import closure, default_recipes, CHIP_RECIPE, TOOLING_RECIPE
from vnmach.director import HeuristicDirector, StaticDirector, sanitize
from vnmach.econ import EconConfig, Economy
from vnmach.robot import BATTERY_CAPACITY, Genome
from vnmach.sim import Config, Simulation
from vnmach.world import SOL_TICKS


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


def test_every_recipe_fits_in_one_battery():
    for rec in [*default_recipes().values(), CHIP_RECIPE, TOOLING_RECIPE]:
        assert rec.energy + Genome().energy_reserve <= BATTERY_CAPACITY, rec.output


def test_vitamin_wall_caps_births_at_imported_chips():
    sim = run(seed_chips=5, ticks=4000)
    assert sim.stats["births"] <= 5
    assert sim.stats["chips_made"] == 0


def test_prebuilt_fab_breaks_the_vitamin_wall():
    sim = run(seed_chips=0, chip_fab=True, ticks=4000)
    assert sim.stats["chips_made"] > 0
    assert sim.stats["births"] > 0


def test_fab_needs_tooling_and_tooling_order_lands_at_t0():
    sim = run(HeuristicDirector("fab", total_ticks=4000), ticks=4000)
    assert sim.stats["imported_fab_tooling"] == 1
    assert sim.world.n_fabs >= 1
    assert sim.econ.ledger["earth_hardware"] <= -40e6  # tooling was paid for


def test_kits_without_tooling_make_no_fab():
    sim = run(StaticDirector(build_fab=True, target_fabs=1, fab_share=1.0, chip_reserve=4), ticks=3000)
    assert sim.world.fab_kits >= 4
    assert sim.world.n_fabs == 0


def test_reserved_chips_go_to_fab_kits_not_children():
    sim = run(StaticDirector(build_fab=True, fab_share=0.0, chip_reserve=20), ticks=1500)
    assert sim.stats["births"] == 0  # nobody builds kits, reserve blocks children


def test_import_strategy_never_builds_a_fab_and_orders_chips():
    sim = run(HeuristicDirector("import", total_ticks=3000), ticks=3000)
    assert sim.world.n_fabs == 0
    assert sim.world.imports["chip"] + sim.stats["used_imported_chip"] > 20


def test_orders_fly_at_windows_and_arrive_after_transit():
    cfg = EconConfig(window_period_sols=50, transit_sols=20)
    sim = Simulation(Config(ticks=1000, econ=cfg))
    sim.step()  # t=0 landing
    sim.econ.order({"chip": 7})
    for _ in range(50 * SOL_TICKS):
        sim.step()
    assert sim.econ.in_transit and sim.world.imports["chip"] < 7 + 20
    for _ in range(20 * SOL_TICKS):
        sim.step()
    assert sim.stats["imported_chip"] == 7
    assert sim.econ.ledger["launch"] < 0


def test_launch_cost_declines_per_window_to_a_floor():
    import random

    e = Economy(EconConfig(), random.Random(0), 48)
    assert e.launch_cost_per_kg(0) == 5000
    assert e.launch_cost_per_kg(e.cfg.window_ticks) == pytest.approx(4250)
    assert e.launch_cost_per_kg(100 * e.cfg.window_ticks) == 500


def test_unaffordable_manifest_is_trimmed():
    import random

    e = Economy(EconConfig(starting_cash=50e6), random.Random(0), 48)
    e.order({"fab_tooling": 1, "chip": 10})
    s = e.launch(e.cfg.window_ticks)
    assert s is not None and "fab_tooling" not in s.items
    assert e.cash >= 0


def test_contracts_pay_on_completion():
    import random

    e = Economy(EconConfig(), random.Random(0), 48)
    c = e.offer(0)
    e.accept([c.id])
    for item, q in c.requirements.items():
        e.deliver(c, item, q, 10)
    assert c.status == "done"
    assert e.ledger["contract_revenue"] == pytest.approx(c.payment)


def test_late_contract_is_penalised():
    import random

    e = Economy(EconConfig(), random.Random(0), 48)
    c = e.offer(0)
    e.accept([c.id])
    e.tick_contracts(c.deadline + 1)
    assert c.status == "failed" and e.ledger["contract_penalty"] < 0


def test_earth_director_is_blacked_out_in_conjunction():
    calls = []

    class Spy:
        def decide(self, snap):
            calls.append(snap["t"])
            return {}

    cfg = Config(ticks=10, director_interval=1, director_location="earth")
    sim = Simulation(cfg, director=Spy())
    sim.t = sim.econ.cfg.window_ticks // 2  # mid-conjunction
    sim.step()
    assert calls == [] and sim.stats["director_blackouts"] == 1


def test_protected_governor_never_mutates():
    sim = run(HeuristicDirector("fab", total_ticks=4000), mutation_rate=1.0, governor_protected=True, ticks=4000)
    assert all(r.genome.max_children == 3 for r in sim.robots)


def test_unprotected_governor_drifts():
    drifted = False
    for seed in range(4):
        sim = run(HeuristicDirector("fab", total_ticks=5000), seed=seed, mutation_rate=1.0,
                  governor_protected=False, ticks=5000)
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
    out = sanitize({"replicate": 1, "target_fabs": 999, "fab_share": 3, "chip_reserve": -4,
                    "harvest_focus": "gold", "orders": {"chip": 9999, "unobtainium": 3, "robot": 0},
                    "accept_contracts": ["4"]})
    assert out == {"replicate": True, "target_fabs": 50, "fab_share": 1.0, "chip_reserve": 0,
                   "harvest_focus": None, "orders": {"chip": 500}, "accept_contracts": [4]}


def test_runs_are_deterministic_per_seed():
    a = run(HeuristicDirector("auto", total_ticks=1500), seed=3, ticks=1500).history
    b = run(HeuristicDirector("auto", total_ticks=1500), seed=3, ticks=1500).history
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
    import anthropic

    from vnmach.llm import ClaudeDirector, system_prompt

    d = ClaudeDirector.__new__(ClaudeDirector)
    d.anthropic = anthropic
    d.model, d.effort, d.total_ticks, d.history_window, d.verbose = "claude-opus-5-5", "low", 1000, 6, False
    d.system = system_prompt(EconConfig())
    d.log, d.usage = [], Counter()
    msgs = FakeMessages(text, stop_reason)
    d.client = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    return d, msgs


DECISION = ('{"rationale":"r","replicate":true,"build_fab":true,"target_fabs":3,"fab_share":0.5,'
            '"make_tooling":true,"chip_reserve":4,"contract_share":0.1,"harvest_focus":null,'
            '"orders":{"chip":5,"motor":0,"sensor":0,"solar_panel":0,"battery":0,"wiring":0,"frame":0,'
            '"robot":0,"fab_tooling":1},"accept_contracts":[]}')


def test_claude_director_parses_sanitizes_and_orders():
    d, msgs = fake_claude(DECISION)
    sim = Simulation(Config(ticks=600, director_interval=250), director=d)
    sim.run()
    assert sim.policy["target_fabs"] == 3 and sim.policy["chip_reserve"] == 4
    assert sim.stats["imported_fab_tooling"] == 1  # t=0 order landed with the first landing
    assert len(msgs.calls) == 3
    assert msgs.calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert "Your recent decisions" in msgs.calls[1]["messages"][0]["content"]


def test_claude_director_refusal_keeps_policy():
    d, _ = fake_claude("", stop_reason="refusal")
    assert d.decide(Simulation(Config()).snapshot()) == {}
    assert d.usage["errors"] == 1
