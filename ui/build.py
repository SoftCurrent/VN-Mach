"""Inline the engine and the measured arm presets into one self-contained page: ui/vn-mach-control.html."""

import json
import pathlib

here = pathlib.Path(__file__).parent
page = (here / "app.html").read_text()
engine = (here / "engine.js").read_text().replace('if (typeof module !== "undefined") module.exports = VN;', "")
presets = json.loads((here / "arm_presets.json").read_text())
page = page.replace("/*ENGINE*/", engine).replace("/*ARM_PRESETS*/[]", json.dumps(presets))
(here / "vn-mach-control.html").write_text(page)
print(f"wrote vn-mach-control.html ({len(page) // 1024} KB)")
