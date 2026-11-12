"""Terminal rendering: sparklines and a map of the colony."""

from __future__ import annotations

BARS = " ▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 60) -> str:
    if not values:
        return ""
    if len(values) > width:
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width)]
    hi = max(values) or 1
    return "".join(BARS[min(8, int(v / hi * 8))] for v in values)


def render_map(sim) -> str:
    w = sim.world
    grid = [["." for _ in range(w.size)] for _ in range(w.size)]
    marks = {"hematite": "h", "ice": "i"}
    for rtype, cells in w.deposits.items():
        for (x, y) in cells:
            grid[y][x] = marks.get(rtype, "?")
    for r in sim.alive:
        x, y = r.pos
        grid[y][x] = "R" if sim.can_replicate(r) else "w"
    for c in sim.econ.active_contracts():
        x, y = c.site
        grid[y][x] = "C"
    x, y = w.depot
    grid[y][x] = "B"
    ground = ", ".join(sim.body.everywhere)
    legend = (f"B=base/{sim.body.station.name}s  R=replicator  w=worker  C=contract site  "
              f"h=hematite  i=ice  .={ground} (everywhere)")
    return "\n".join("".join(row) for row in grid) + "\n" + legend
