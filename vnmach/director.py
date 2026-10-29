"""High-level colony planners ("directors").

The director runs every `director_interval` ticks, reads a colony snapshot,
and returns policy updates. It never touches motors directly - this is the
hierarchical split real AI robot stacks use: a slow, smart planner on top of
fast, reliable low-level skills.

Policy keys:
  replicate     bool  - may robots build children at all
  build_fab     bool  - divert some robots to building chip-fab kits
  target_fabs   int   - how many fabs to build
  fab_share     float - fraction of replicators assigned to fab kits
  chip_reserve  int   - imported chips held back for fab kits
  harvest_focus str|None - feedstock workers should prioritise
"""

from __future__ import annotations

from .bom import FAB_KIT, FAB_KITS_REQUIRED

POLICY_KEYS = ("replicate", "build_fab", "target_fabs", "fab_share", "chip_reserve", "harvest_focus")


class StaticDirector:
    """Applies one fixed policy at t=0 and never changes it (the no-planner baseline)."""

    def __init__(self, **policy):
        self.policy = policy
        self.done = False

    def decide(self, snap: dict) -> dict:
        if self.done:
            return {}
        self.done = True
        return dict(self.policy)


class HeuristicDirector:
    """Hand-written rules: reserve chips for the first fab, scale fabs with population, stop at a target size."""

    def __init__(self, target_population: int = 150, robots_per_fab: int = 12):
        self.target_population = target_population
        self.robots_per_fab = robots_per_fab

    def decide(self, snap: dict) -> dict:
        kits_left = max(0, FAB_KITS_REQUIRED - snap["fab_kits"]) if snap["fabs"] == 0 else 0
        chips_per_kit = FAB_KIT.get("chip", 0)
        target_fabs = max(1, snap["population"] // self.robots_per_fab)
        return {
            "replicate": snap["population"] < self.target_population,
            "build_fab": True,
            "target_fabs": target_fabs,
            "fab_share": 0.6 if snap["fabs"] == 0 else 0.3,
            "chip_reserve": kits_left * chips_per_kit,
        }


def sanitize(update: dict) -> dict:
    """Clamp a planner's output to valid policy values. Never trust a planner blindly."""
    out = {}
    if "replicate" in update:
        out["replicate"] = bool(update["replicate"])
    if "build_fab" in update:
        out["build_fab"] = bool(update["build_fab"])
    if "target_fabs" in update:
        out["target_fabs"] = max(0, min(50, int(update["target_fabs"])))
    if "fab_share" in update:
        out["fab_share"] = max(0.0, min(1.0, float(update["fab_share"])))
    if "chip_reserve" in update:
        out["chip_reserve"] = max(0, min(100, int(update["chip_reserve"])))
    if "harvest_focus" in update:
        f = update["harvest_focus"]
        out["harvest_focus"] = f if f in ("hematite", "basalt", "ice", "co2") else None
    return out
