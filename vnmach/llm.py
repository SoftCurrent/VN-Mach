"""Claude as the colony director: an LLM planner over scripted low-level skills.

Every `director_interval` ticks Claude gets the colony and economy state plus
its own recent decisions, and returns policy, Earth orders and contract
acceptances as schema-constrained JSON. The output is clamped by `sanitize`
before it touches the simulation, and any API failure leaves the current
policy in place: the colony must never depend on the planner being reachable.

Where the planner runs is a simulation setting (Config.director_location):
"earth" means a big model in a datacenter, blacked out during solar
conjunction (Mars only) and delayed by light time; "onboard" means a model on
site (pass a smaller --model to stand in for edge hardware) that is always available.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from .body import BODIES, Body
from .director import sanitize
from .econ import EconConfig

DEFAULT_MODEL = "claude-opus-5-5"
PLACE = {"mars": "Mars", "moon": "the Moon"}


def _a(noun: str) -> str:
    return f"an {noun}" if noun[0].lower() in "aeiou" or noun.startswith("MRE") else f"a {noun}"


def _cap(text: str) -> str:
    return text[0].upper() + text[1:]


def _station_text(body: Body) -> str:
    st, vit = body.station, body.label(body.vitamin)
    kit = json.dumps(st.kit)
    if st.makes_vitamin:
        core = st.core_recipe
        return f"""- Everything is made locally EXCEPT {vit}s, which come from Earth unless the colony has {_a(st.name)}.
- {_cap(_a(st.name))} = {st.kits_required} kits built by robots ({kit} each) + 1 {body.label(st.core)}. The first
  {body.label(st.core)} must be bought from Earth. Once one {st.name} works, it can make more
  ({core.ticks} ticks each, needs {core.inputs.get(body.vitamin, 0)} {vit}s).
  Each {st.name} makes one {vit} per {st.recipes[0].ticks} ticks. If imported {vit}s run out before the
  first {st.name} exists, replication stops until the next shipment lands."""
    outputs = ", ".join(f"{r.output} ({r.ticks} ticks, + {json.dumps(r.byproducts)})" for r in st.recipes)
    return f"""- {vit.capitalize()}s can't be made locally at all: every robot and every station kit needs one from Earth.
- Metal comes only from {st.name}s: {outputs}. Each {st.name} runs one job at a time, so they are
  the bottleneck. {_cap(_a(st.name))} = {st.kits_required} kits built by robots ({kit} each) + 1 {body.label(st.core)},
  which must be bought from Earth for every new {st.name}. The colony starts with one."""


def system_prompt(econ: EconConfig) -> str:
    body = BODIES[econ.body]
    day = body.day_name
    fatal = sum(body.robot_bom.values()) / len(body.fatal_parts) * body.mtbf
    weather = ("Dust storms cut solar power to 25% for 15-60 sols." if body.dust_storms else
               "Sunlight is near-constant at the polar site, but ~15% of each 29.5-day lunar cycle is dark.")
    comms = ("An Earth-hosted planner is blacked out for ~14 sols at each solar conjunction."
             if body.conjunction_days else "Earth is always in contact (1.3 s light delay).")
    return f"""You are the planning layer of a self-replicating robot colony on {PLACE[body.name]}, run as a business.
Goal: maximise cash at the end of the run (contract revenue minus everything bought and
launched from Earth), while keeping the colony alive and growing toward independence from Earth.

How the colony works (10 ticks = 1 {day}):
- Robots mine local feedstock ({", ".join(body.raw)}), refine it, fabricate parts and
  assemble copies of themselves. Robot bill of materials: {json.dumps(body.robot_bom)}.
{_station_text(body)}
- Robot components fail; losing the {" or ".join(body.label(p) for p in body.fatal_parts)} kills a robot
  (~1 fatal failure per {fatal:,.0f} robot-ticks). Robots repair other failures themselves.
- {weather} {comms}

Earth supply chain:
- Anything in the catalog can be ordered: {", ".join(econ.catalog)}. Orders fly only at launch windows
  every {econ.window_period_sols} {day}s and arrive {econ.transit_sols} {day}s later. Orders you place at
  tick 0 land immediately with the first landing.
- Landed cost = Earth price + mass x launch cost per kg (falls each window). The state shows current
  landed costs. Each window with cargo also costs ${econ.shipment_fixed_cost / 1e6:.0f}M fixed.
- Mission control costs ${econ.ops_cost_per_sol / 1e3:.0f}k per {day}.

Revenue:
- Other operators offer contracts (deliver goods to a site before a deadline). Payment is pegged
  to what shipping the goods from Earth would cost them, so it falls as launch gets cheaper.
  Failing an accepted contract costs a 10% penalty. Offers expire after 30 {day}s.
- Robots that can't replicate work on accepted contracts; contract_share diverts replicators too.

You control ("fab" below means the {body.station.name}):
- replicate, build_fab, target_fabs, fab_share, make_tooling, chip_reserve, contract_share, harvest_focus
- orders: items to buy on the next launch window (use 0 for items you don't want)
- accept_contracts: ids of offered contracts to take on

Return the complete decision each time, with a short rationale."""


def order_schema(items) -> dict:
    return {
        "type": "object",
        "properties": {k: {"type": "integer"} for k in items},
        "required": list(items),
        "additionalProperties": False,
    }


def decision_schema(catalog_items) -> dict:
    return {**DECISION_SCHEMA, "properties": {**DECISION_SCHEMA["properties"], "orders": order_schema(catalog_items)}}


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
        "orders": order_schema(EconConfig().catalog),
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
        econ = econ or EconConfig()
        self.system = system_prompt(econ)
        self.schema = decision_schema(econ.catalog)
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
                               "format": {"type": "json_schema", "schema": self.schema}},
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
