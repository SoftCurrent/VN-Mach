"""Claude as the colony director: an LLM planner over scripted low-level skills.

Every `director_interval` ticks Claude gets the colony and economy state plus
its own recent decisions, and returns policy, Earth orders and contract
acceptances as schema-constrained JSON. The output is clamped by `sanitize`
before it touches the simulation, and any API failure leaves the current
policy in place: the colony must never depend on the planner being reachable.

Where the planner runs is a simulation setting (Config.director_location):
"earth" means a big model in a datacenter, blacked out during solar
conjunction and delayed by light time; "mars" means an onboard model (pass a
smaller --model to stand in for edge hardware) that is always available.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from .bom import CHIP_RECIPE, FAB_KIT, FAB_KITS_REQUIRED, ROBOT_BOM, TOOLING_RECIPE
from .director import CATALOG_ITEMS, sanitize
from .econ import EconConfig

DEFAULT_MODEL = "claude-opus-5-5"


def system_prompt(econ: EconConfig) -> str:
    return f"""You are the planning layer of a self-replicating robot colony on Mars, run as a business.
Goal: maximise cash at the end of the run (contract revenue minus everything bought and
launched from Earth), while keeping the colony alive and growing toward independence from Earth.

How the colony works (10 ticks = 1 sol):
- Robots mine Martian feedstock (hematite, basalt, ice, CO2), refine it, fabricate parts and
  assemble copies of themselves. Robot bill of materials: {json.dumps(ROBOT_BOM)}.
- Everything is made locally EXCEPT chips, which come from Earth unless the colony has chip fabs.
- A chip fab = {FAB_KITS_REQUIRED} kits built by robots ({json.dumps(FAB_KIT)} each, so kits use chips)
  + 1 set of fab tooling. The first tooling must be bought from Earth. Once one fab works, it can make
  tooling for more fabs ({TOOLING_RECIPE.ticks} ticks each, needs {TOOLING_RECIPE.inputs['chip']} chips).
  Each fab makes one chip per {CHIP_RECIPE.ticks} ticks. If imported chips run out before the first
  fab exists, replication stops until the next shipment lands.
- Robot components fail; losing the chip or frame kills a robot (~1 fatal failure per 22,000 robot-ticks).
  Robots repair other failures themselves.
- Dust storms cut solar power to 25% for 15-60 sols.

Earth supply chain:
- Anything in the catalog can be ordered: {', '.join(CATALOG_ITEMS)}. Orders fly only at launch windows
  every {econ.window_period_sols} sols and arrive {econ.transit_sols} sols later. Orders you place at
  tick 0 land immediately with the first landing.
- Landed cost = Earth price + mass x launch cost per kg (falls each window). The state shows current
  landed costs. Each window with cargo also costs ${econ.shipment_fixed_cost / 1e6:.0f}M fixed.
- Mission control costs ${econ.ops_cost_per_sol / 1e3:.0f}k per sol.

Revenue:
- Other Mars operators offer contracts (deliver goods to a site before a deadline). Payment is pegged
  to what shipping the goods from Earth would cost them, so it falls as launch gets cheaper.
  Failing an accepted contract costs a 10% penalty. Offers expire after 30 sols.
- Robots that can't replicate work on accepted contracts; contract_share diverts replicators too.

You control:
- replicate, build_fab, target_fabs, fab_share, make_tooling, chip_reserve, contract_share, harvest_focus
- orders: items to buy on the next launch window (use 0 for items you don't want)
- accept_contracts: ids of offered contracts to take on

Return the complete decision each time, with a short rationale."""


ORDER_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "integer"} for k in CATALOG_ITEMS},
    "required": list(CATALOG_ITEMS),
    "additionalProperties": False,
}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "replicate": {"type": "boolean"},
        "build_fab": {"type": "boolean"},
        "target_fabs": {"type": "integer"},
        "fab_share": {"type": "number"},
        "make_tooling": {"type": "boolean"},
        "chip_reserve": {"type": "integer"},
        "contract_share": {"type": "number"},
        "harvest_focus": {"type": ["string", "null"], "enum": ["hematite", "basalt", "ice", "co2", None]},
        "orders": ORDER_SCHEMA,
        "accept_contracts": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["rationale", "replicate", "build_fab", "target_fabs", "fab_share", "make_tooling",
                 "chip_reserve", "contract_share", "harvest_focus", "orders", "accept_contracts"],
    "additionalProperties": False,
}


class ClaudeDirector:
    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "low", total_ticks: int | None = None,
                 history_window: int = 6, verbose: bool = True, econ: EconConfig | None = None):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.total_ticks = total_ticks
        self.history_window = history_window
        self.verbose = verbose
        self.system = system_prompt(econ or EconConfig())
        self.log: list[dict] = []  # past decisions fed back as memory
        self.usage: Counter = Counter()

    def prompt(self, snap: dict) -> str:
        parts = []
        if self.total_ticks:
            parts.append(f"Run length: {self.total_ticks} ticks ({self.total_ticks // 10} sols). "
                         f"Current tick: {snap['t']} (sol {snap['sol']}).")
        recent = self.log[-self.history_window:]
        if recent:
            parts.append("Your recent decisions and the state you saw at the time:")
            parts.extend(json.dumps(e, separators=(",", ":")) for e in recent)
        parts.append("Current colony state:")
        parts.append(json.dumps(snap, separators=(",", ":")))
        parts.append("Decide for the next interval.")
        return "\n".join(parts)

    def decide(self, snap: dict) -> dict:
        a = self.anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=self.system,
                output_config={"effort": self.effort,
                               "format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
                messages=[{"role": "user", "content": self.prompt(snap)}],
            )
        except (a.RateLimitError, a.APIStatusError, a.APIConnectionError) as e:
            self.usage["errors"] += 1
            self._say(f"[sol {snap['sol']}] planner unreachable ({type(e).__name__}); keeping current policy")
            return {}
        self.usage["calls"] += 1
        self.usage["input_tokens"] += response.usage.input_tokens
        self.usage["output_tokens"] += response.usage.output_tokens
        if response.stop_reason in ("refusal", "max_tokens"):
            self.usage["errors"] += 1
            self._say(f"[sol {snap['sol']}] planner stop_reason={response.stop_reason}; keeping current policy")
            return {}
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            decision = json.loads(text)
        except json.JSONDecodeError:
            self.usage["errors"] += 1
            return {}
        update = sanitize(decision)
        e = snap["economy"]
        self.log.append({"sol": snap["sol"], "population": snap["population"], "fabs": snap["fabs"],
                         "chips_in_depot": snap["chips_in_depot"], "cash_musd": e["cash_musd"],
                         "decision": update})
        self._say(f"[sol {snap['sol']}] {update} -- {decision.get('rationale', '')}")
        return update

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)
