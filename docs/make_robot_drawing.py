"""Draw the lunar replicator robot (docs/lunar_robot.svg) from the Moon parts list in vnmach/body.py.

Side elevation at 1 m = 300 px, parts coloured by material, numbered callouts
matching the parts table. Run from the repo root:  PYTHONPATH=. python docs/make_robot_drawing.py
"""

import math
import pathlib

from vnmach.body import MOON
from vnmach.econ import bom_mass_kg

OUT = pathlib.Path(__file__).with_name("lunar_robot.svg")

# palette: material colours (fixed, the drawing is a print-style plate)
PAPER, INK, MUTED, GRID = "#f4f5f6", "#1d2329", "#5f6871", "#dfe2e6"
IRON, FESI, ALU, GLASS, EARTH = "#4b5157", "#5e7389", "#b8c3cd", "#bfe3ea", "#d9822b"
REGOLITH, REGOLITH_DARK = "#c9c6c0", "#a9a59e"

M = 300  # px per metre
GROUND = 770


def wheel(cx, cy, r, far=False):
    op = 0.45 if far else 1.0
    s = [f'<g opacity="{op}">']
    s.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#mesh)" stroke="{IRON}" stroke-width="6"/>')
    for k in range(12):  # grouser cleats on the rim
        a = k * math.pi / 6
        x1, y1 = cx + (r - 3) * math.cos(a), cy + (r - 3) * math.sin(a)
        x2, y2 = cx + (r + 9) * math.cos(a), cy + (r + 9) * math.sin(a)
        s.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{IRON}" stroke-width="7"/>')
    s.append(f'<circle cx="{cx}" cy="{cy}" r="38" fill="{IRON}"/>')  # cycloidal reducer housing
    s.append(f'<circle cx="{cx}" cy="{cy}" r="24" fill="{FESI}" stroke="{INK}" stroke-width="1.5"/>')  # SR motor
    for k in range(6):  # stator teeth hint
        a = k * math.pi / 3
        s.append(f'<line x1="{cx + 10 * math.cos(a):.1f}" y1="{cy + 10 * math.sin(a):.1f}" '
                 f'x2="{cx + 22 * math.cos(a):.1f}" y2="{cy + 22 * math.sin(a):.1f}" stroke="{ALU}" stroke-width="3"/>')
    s.append(f'<circle cx="{cx}" cy="{cy}" r="6" fill="{ALU}"/>')
    s.append("</g>")
    return "\n".join(s)


def joint(cx, cy, r=26):
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{IRON}" stroke="{INK}" stroke-width="2"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r - 9}" fill="{FESI}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="5" fill="{ALU}"/>')


def tube(x1, y1, x2, y2, w=22):
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{INK}" stroke-width="{w + 4}" stroke-linecap="round"/>'
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{FESI}" stroke-width="{w}" stroke-linecap="round"/>'
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{ALU}" stroke-width="3" stroke-linecap="round" opacity=".6"/>')


def callout(n, x, y, tx, ty):
    """Numbered marker at (tx, ty) with a leader to the part at (x, y)."""
    return (f'<line x1="{x}" y1="{y}" x2="{tx}" y2="{ty}" stroke="{INK}" stroke-width="1.3"/>'
            f'<circle cx="{x}" cy="{y}" r="4" fill="{INK}"/>'
            f'<circle cx="{tx}" cy="{ty}" r="15" fill="{PAPER}" stroke="{INK}" stroke-width="2"/>'
            f'<text x="{tx}" y="{ty + 5.5}" text-anchor="middle" font-size="16" font-weight="700" fill="{INK}">{n}</text>')


def drawing() -> str:
    s = []
    # ground: regolith with a few rocks, and the cable running off to the power mast
    s.append(f'<rect x="0" y="{GROUND}" width="1080" height="70" fill="{REGOLITH}"/>')
    s.append(f'<line x1="0" y1="{GROUND}" x2="1080" y2="{GROUND}" stroke="{REGOLITH_DARK}" stroke-width="3"/>')
    for x, w, h in [(70, 46, 16), (470, 28, 10), (1010, 54, 20), (880, 20, 8)]:
        s.append(f'<ellipse cx="{x}" cy="{GROUND + 2}" rx="{w / 2}" ry="{h / 2}" fill="{REGOLITH_DARK}"/>')

    # far-side wheels (offset for depth)
    wr, wy = 0.3 * M - 9, GROUND - 0.3 * M  # 0.6 m wheels
    rear_x, front_x = 300, 300 + int(1.3 * M)
    for x in (rear_x, front_x):
        s.append(wheel(x + 34, wy - 22, wr, far=True))

    # frame: two cast beams (top/bottom rails), end plates, diagonal truss members
    fx0, fx1, fy0, fy1 = 225, 760, 520, 600
    s.append(f'<rect x="{fx0}" y="{fy0}" width="{fx1 - fx0}" height="{fy1 - fy0}" fill="none"/>')
    for y in (fy0, fy1 - 18):
        s.append(f'<rect x="{fx0}" y="{y}" width="{fx1 - fx0}" height="18" fill="{IRON}" stroke="{INK}" stroke-width="2"/>')
    for x in range(fx0 + 60, fx1 - 30, 95):
        s.append(f'<line x1="{x}" y1="{fy0 + 18}" x2="{x + 60}" y2="{fy1 - 18}" stroke="{IRON}" stroke-width="10"/>')
    for x in (fx0, fx1 - 22):  # end plates
        s.append(f'<rect x="{x}" y="{fy0 - 10}" width="22" height="{fy1 - fy0 + 20}" rx="3" fill="{FESI}" stroke="{INK}" stroke-width="2"/>')
    for x in range(fx0 + 40, fx1 - 20, 70):  # bolt heads
        for y in (fy0 + 9, fy1 - 9):
            s.append(f'<circle cx="{x}" cy="{y}" r="3" fill="{ALU}"/>')

    # suspension struts from frame to wheel hubs
    for x in (rear_x, front_x):
        s.append(f'<path d="M{x - 40},{fy1} L{x},{wy} L{x + 40},{fy1}" fill="none" stroke="{IRON}" stroke-width="12" stroke-linejoin="round"/>')

    # avionics box inside the frame (the only imported part)
    s.append(f'<rect x="430" y="{fy0 + 24}" width="110" height="40" rx="4" fill="{EARTH}" stroke="{INK}" stroke-width="2"/>')
    s.append(f'<text x="485" y="{fy0 + 49}" text-anchor="middle" font-size="12" font-weight="700" fill="{INK}">AVIONICS</text>')

    # radiator fins on top of the frame
    for i in range(9):
        x = 300 + i * 18
        s.append(f'<rect x="{x}" y="{fy0 - 46}" width="8" height="46" fill="{ALU}" stroke="{MUTED}" stroke-width="1"/>')
    s.append(f'<rect x="296" y="{fy0 - 6}" width="164" height="6" fill="{ALU}"/>')

    # sensor mast: stereo cameras + lidar (part of the avionics module)
    mx = 560
    s.append(f'<rect x="{mx - 6}" y="410" width="12" height="{fy0 - 410}" fill="{FESI}" stroke="{INK}" stroke-width="1.5"/>')
    s.append(f'<rect x="{mx - 46}" y="378" width="92" height="34" rx="6" fill="{EARTH}" stroke="{INK}" stroke-width="2"/>')
    for dx in (-24, 24):
        s.append(f'<circle cx="{mx + dx}" cy="395" r="10" fill="{INK}"/><circle cx="{mx + dx}" cy="395" r="4" fill="{GLASS}"/>')
    s.append(f'<path d="M{mx - 14},378 a14,14 0 0 1 28,0 z" fill="{GLASS}" stroke="{INK}" stroke-width="2"/>')

    # tether reel at the rear, cable to the power mast off-frame
    rx, ry = 170, 545
    s.append(f'<path d="M{rx},{ry + 44} C120,690 70,740 0,{GROUND - 6}" fill="none" stroke="{ALU}" stroke-width="7"/>')
    s.append(f'<path d="M{rx},{ry + 44} C120,690 70,740 0,{GROUND - 6}" fill="none" stroke="{INK}" stroke-width="1" stroke-dasharray="10 6"/>')
    s.append(f'<line x1="{rx}" y1="{ry}" x2="{fx0}" y2="{ry}" stroke="{IRON}" stroke-width="14"/>')
    s.append(f'<circle cx="{rx}" cy="{ry}" r="46" fill="{ALU}" stroke="{INK}" stroke-width="2.5"/>')
    for rr in (36, 27, 18):
        s.append(f'<circle cx="{rx}" cy="{ry}" r="{rr}" fill="none" stroke="{MUTED}" stroke-width="2"/>')
    s.append(f'<circle cx="{rx}" cy="{ry}" r="9" fill="{IRON}"/>')

    # near-side wheels
    for x in (rear_x, front_x):
        s.append(wheel(x, wy, wr))

    # arm: yaw base, shoulder, elbow, wrist, scoop tool
    base = (725, fy0 - 18)
    shoulder = (725, fy0 - 62)
    elbow = (865, 318)
    wrist = (985, 560)
    s.append(f'<rect x="{base[0] - 34}" y="{base[1] - 8}" width="68" height="26" rx="5" fill="{IRON}" stroke="{INK}" stroke-width="2"/>')
    s.append(tube(*shoulder, *elbow))
    s.append(tube(*elbow, *wrist))
    s.append(tube(wrist[0], wrist[1], 990, 640, 16))
    s.append(f'<path d="M958,640 L1030,640 L1048,700 Q1000,724 948,700 Z" fill="{IRON}" stroke="{INK}" stroke-width="2.5"/>')
    s.append(f'<path d="M972,646 L1026,646" stroke="{ALU}" stroke-width="3"/>')  # passive tool-changer cone/latch
    for p in (shoulder, elbow, wrist):
        s.append(joint(*p))
    s.append(joint(base[0], base[1] - 4, 20))

    # harness runs: aluminium cables along the frame and arm
    s.append(f'<path d="M{fx0 + 20},{fy0 + 30} H{fx1 - 30} Q{fx1 - 10},{fy0 + 30} {fx1 - 10},{fy0 - 20} L{shoulder[0] + 18},{shoulder[1] + 10}" '
             f'fill="none" stroke="{GLASS}" stroke-width="5" stroke-dasharray="1 0"/>')
    s.append(f'<path d="M{shoulder[0] + 14},{shoulder[1]} L{elbow[0] + 10},{elbow[1] + 16} L{wrist[0] + 14},{wrist[1] - 8}" '
             f'fill="none" stroke="{GLASS}" stroke-width="4"/>')

    # scale bar
    s.append(f'<g transform="translate(40,{GROUND + 40})">'
             f'<rect x="0" y="0" width="{M // 2}" height="8" fill="{INK}"/><rect x="{M // 2}" y="0" width="{M // 2}" height="8" fill="{PAPER}" stroke="{INK}" stroke-width="1.5"/>'
             f'<text x="0" y="24" font-size="13" fill="{INK}">0</text><text x="{M // 2}" y="24" font-size="13" text-anchor="middle" fill="{INK}">0.5</text>'
             f'<text x="{M}" y="24" font-size="13" text-anchor="end" fill="{INK}">1 m</text></g>')

    # callouts
    s.append(callout(1, 330, fy1 - 9, 330, 455 - 140))
    s.append(callout(2, rear_x - 60, wy + 40, 150, 720))
    s.append(callout(3, front_x, wy, front_x - 110, 700))
    s.append(callout(4, front_x + 30, wy + 26, front_x + 90, 735))
    s.append(callout(5, 800, 404, 760, 300))
    s.append(callout(6, 865, 318, 930, 260))
    s.append(callout(7, rx, ry - 46, 130, 430))
    s.append(callout(8, 380, fy0 - 46, 420, 400 - 70))
    s.append(callout(9, 485, fy0 + 24, 505, 470))
    s.append(callout(10, mx, 378, 640, 330))
    return "\n".join(s)


def table() -> str:
    kg = {p: bom_mass_kg(p, MOON) * q for p, q in MOON.robot_bom.items()}
    rows = [
        # (callout numbers, part, qty, made from, mass, source)
        ("1", "1 frame: 4 cast beams, 2 end plates", "", "cast iron, iron-silicon", kg["frame"], "Moon"),
        ("2", "4 wheels: woven wire mesh, grousers", "", "drawn iron wire", kg["wheel"], "Moon"),
        ("3, 6", "8 motors: switched reluctance", "", "Fe-Si laminations, Al coils, glass fibre", kg["motor"], "Moon"),
        ("4, 6", "8 reducers: cycloidal, lapped", "", "cast iron", kg["reducer"], "Moon"),
        ("5", "1 arm: 4 joints + scoop tool", "", "Fe-Si tubes, aluminium", kg["arm"], "Moon"),
        ("7, 8", "1 harness: tether, cables, radiator", "", "aluminium, glass insulation", kg["harness"], "Moon"),
        ("9, 10", "1 avionics: compute, cameras, lidar", "", "Earth-made electronics", kg["chip"], "Earth"),
    ]
    total = sum(kg.values())
    local = total - kg["chip"]
    x0, y0 = 1110, 150
    s = [f'<text x="{x0}" y="{y0 - 62}" font-size="30" font-weight="700" fill="{INK}">Parts list</text>',
         f'<text x="{x0}" y="{y0 - 34}" font-size="15" fill="{MUTED}">From the Moon profile in vnmach/body.py</text>']
    for dx, h, anchor in [(0, "#", "start"), (52, "Part", "start"), (500, "Mass", "end")]:
        s.append(f'<text x="{x0 + dx}" y="{y0}" font-size="12.5" font-weight="700" fill="{MUTED}" letter-spacing="1" '
                 f'text-anchor="{anchor}">{h.upper()}</text>')
    s.append(f'<line x1="{x0}" y1="{y0 + 9}" x2="{x0 + 500}" y2="{y0 + 9}" stroke="{INK}" stroke-width="1.5"/>')
    y = y0 + 40
    for num, part, qty, made, mass, src in rows:
        colour = EARTH if src == "Earth" else FESI
        s.append(f'<rect x="{x0}" y="{y - 22}" width="6" height="56" fill="{colour}"/>')
        s.append(f'<text x="{x0 + 14}" y="{y}" font-size="15" font-weight="700" fill="{INK}">{num}</text>')
        s.append(f'<text x="{x0 + 52}" y="{y}" font-size="15.5" font-weight="600" fill="{INK}">{part}</text>')
        s.append(f'<text x="{x0 + 52}" y="{y + 21}" font-size="13.5" fill="{MUTED}">{made} · made on {"the Moon" if src == "Moon" else "Earth"}</text>')
        s.append(f'<text x="{x0 + 500}" y="{y}" font-size="15.5" text-anchor="end" fill="{INK}">{mass:.0f} kg</text>')
        s.append(f'<line x1="{x0}" y1="{y + 36}" x2="{x0 + 500}" y2="{y + 36}" stroke="{GRID}" stroke-width="1"/>')
        y += 66
    s.append(f'<text x="{x0 + 52}" y="{y}" font-size="16" font-weight="700" fill="{INK}">Total</text>')
    s.append(f'<text x="{x0 + 500}" y="{y}" font-size="16" font-weight="700" text-anchor="end" fill="{INK}">{total:.0f} kg</text>')
    s.append(f'<text x="{x0 + 52}" y="{y + 26}" font-size="14" fill="{MUTED}">{local / total:.0%} made on the Moon by mass; only the avionics come from Earth.</text>')
    y += 70
    s.append(f'<text x="{x0}" y="{y}" font-size="12.5" font-weight="700" fill="{MUTED}" letter-spacing="1">MATERIALS</text>')
    for i, (c, name) in enumerate([(IRON, "Cast iron"), (FESI, "Iron-silicon (MRE)"), (ALU, "Aluminium (MRE)"),
                                   (GLASS, "Glass / glass fibre"), (EARTH, "Imported from Earth")]):
        yy = y + 24 + i * 26
        s.append(f'<rect x="{x0}" y="{yy - 13}" width="18" height="18" rx="3" fill="{c}" stroke="{INK}" stroke-width="1"/>')
        s.append(f'<text x="{x0 + 28}" y="{yy + 1}" font-size="14.5" fill="{INK}">{name}</text>')
    notes = ["Motors and reducers come in pairs:",
             "one per wheel hub (4), one per arm joint (4).",
             "Joints turn in plain bushings: iron shafts",
             "in cast-basalt sleeves, so no metal-on-metal",
             "contact cold-welds in vacuum.",
             "The tether brings 100–300 V DC from the",
             "power mast: no battery to make or ship."]
    for i, line in enumerate(notes):
        s.append(f'<text x="{x0 + 215}" y="{y + 24 + i * 21}" font-size="13.5" fill="{MUTED}">{line}</text>')
    return "\n".join(s)


def svg() -> str:
    W, H = 1660, 900
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}"
     font-family="Inter, DejaVu Sans, sans-serif">
<defs>
  <pattern id="mesh" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
    <rect width="14" height="14" fill="{PAPER}"/>
    <line x1="0" y1="0" x2="0" y2="14" stroke="{IRON}" stroke-width="2.4"/>
    <line x1="0" y1="0" x2="14" y2="0" stroke="{IRON}" stroke-width="2.4"/>
  </pattern>
</defs>
<rect width="{W}" height="{H}" fill="{PAPER}"/>
<text x="40" y="60" font-size="34" font-weight="700" fill="{INK}">Lunar replicator robot</text>
<text x="40" y="92" font-size="17" fill="{MUTED}">Side view. ~300 kg, 1.8 m long, built from regolith by robots like itself, plus one avionics module from Earth.</text>
<line x1="1080" y1="120" x2="1080" y2="860" stroke="{GRID}" stroke-width="2"/>
{drawing()}
<text x="1035" y="{GROUND - 12}" font-size="13" text-anchor="end" fill="{MUTED}">to power mast ←</text>
{table()}
</svg>'''


if __name__ == "__main__":
    OUT.write_text(svg())
    print(f"wrote {OUT}")
