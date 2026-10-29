"""Claude as the colony director: an LLM planner over scripted low-level skills.

Every `director_interval` ticks Claude gets the colony state plus its own
recent decisions and returns a policy as schema-constrained JSON. The output
is clamped by `sanitize` before it touches the simulation, and any API
failure leaves the current policy in place: the colony must never depend on
the planner being reachable (on Mars it's ~4-24 light-minutes away if hosted
on Earth).
"""

from __future__ import annotations

import json
import sys

from .bom import CHIP_RECIPE, FAB_KIT, FAB_KITS_REQUIRED, ROBOT_BOM
from .director import sanitize

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = f"""You are the planning layer of a self-replicating robot colony on Mars.
Your goal: maximise the number of working robots at the end of the run, while
making the colony independent of Earth.

How the colony works:
- Robots mine Martian feedstock (hematite, basalt, ice, CO2), refine it, fabricate
  parts and assemble copies of themselves. Robot bill of materials: {json.dumps(ROBOT_BOM)}.
- Everything is made locally EXCEPT chips. Chips come from a finite stock shipped
  from Earth, unless the colony has built chip fabs.
- A chip fab is built from {FAB_KITS_REQUIRED} kits delivered to the base. Each kit is
  {json.dumps(FAB_KIT)}, so building a fab consumes imported chips. If imported chips
  run out before the first fab is finished, the colony can never make chips again.
- Each fab makes one chip at a time ({CHIP_RECIPE.ticks} ticks per chip). More fabs = more
  chip throughput, but robots building fab kits are not building children.
- Robots that may not replicate become workers that harvest feedstock to the base stockpile.
- Fabricated parts are sometimes defective. A robot with a defective chip is dead on arrival.
- Dust storms cut solar power to 25% for hundreds of ticks.

You control only the colony policy (low-level robot control is automatic):
- replicate: whether robots may build children
- build_fab: whether some replicators build fab kits
- target_fabs: how many fabs the colony should have in total
- fab_share: fraction (0-1) of replicators assigned to fab kits while fabs < target_fabs
- chip_reserve: imported chips held back so only fab-kit builders may use them
- harvest_focus: feedstock workers prioritise (hematite, basalt, ice, co2) or null

Return the complete policy each time, with a short rationale."""

POLICY_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "replicate": {"type": "boolean"},
        "build_fab": {"type": "boolean"},
        "target_fabs": {"type": "integer"},
        "fab_share": {"type": "number"},
        "chip_reserve": {"type": "integer"},
        "harvest_focus": {"type": ["string", "null"], "enum": ["hematite", "basalt", "ice", "co2", None]},
    },
    "required": ["rationale", "replicate", "build_fab", "target_fabs", "fab_share", "chip_reserve", "harvest_focus"],
    "additionalProperties": False,
}


class ClaudeDirector:
    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "low", total_ticks: int | None = None,
                 history_window: int = 6, verbose: bool = True):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.total_ticks = total_ticks
        self.history_window = history_window
        self.verbose = verbose
        self.log: list[dict] = []  # (snapshot, decision) pairs fed back as memory
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "errors": 0}

    def prompt(self, snap: dict) -> str:
        recent = self.log[-self.history_window:]
        parts = []
        if self.total_ticks:
            parts.append(f"Run length: {self.total_ticks} ticks. Current tick: {snap['t']}.")
        if recent:
            parts.append("Your recent decisions and the state you saw at the time:")
            for entry in recent:
                parts.append(json.dumps(entry, separators=(",", ":")))
        parts.append("Current colony state:")
        parts.append(json.dumps(snap, separators=(",", ":")))
        parts.append("Decide the policy for the next interval.")
        return "\n".join(parts)

    def decide(self, snap: dict) -> dict:
        a = self.anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM_PROMPT,
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": POLICY_SCHEMA}},
                messages=[{"role": "user", "content": self.prompt(snap)}],
            )
        except (a.RateLimitError, a.APIStatusError, a.APIConnectionError) as e:
            self.usage["errors"] += 1
            self._say(f"[t={snap['t']}] director unreachable ({type(e).__name__}); keeping current policy")
            return {}
        self.usage["calls"] += 1
        self.usage["input_tokens"] += response.usage.input_tokens
        self.usage["output_tokens"] += response.usage.output_tokens
        if response.stop_reason in ("refusal", "max_tokens"):
            self.usage["errors"] += 1
            self._say(f"[t={snap['t']}] director stop_reason={response.stop_reason}; keeping current policy")
            return {}
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            decision = json.loads(text)
        except json.JSONDecodeError:
            self.usage["errors"] += 1
            return {}
        policy = sanitize(decision)
        self.log.append({"t": snap["t"], "population": snap["population"], "fabs": snap["fabs"],
                         "chips_in_depot": snap["chips_in_depot"], "decision": policy})
        self._say(f"[t={snap['t']}] {policy} -- {decision.get('rationale', '')}")
        return policy

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)
