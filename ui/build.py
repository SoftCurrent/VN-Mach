"""Inline the engine, the app script and the measured arm presets into one self-contained page:
ui/vn-mach-control.html."""

import json
import pathlib

here = pathlib.Path(__file__).parent
page = (here / "app.html").read_text()
engine = (here / "engine.js").read_text().replace('if (typeof module !== "undefined") module.exports = VN;', "")
app = (here / "app.js").read_text()
presets = json.loads((here / "arm_presets.json").read_text())
app = app.replace("/*ARM_PRESETS*/[]", json.dumps(presets))
page = page.replace("/*ENGINE*/", engine).replace("/*APP*/", app)
(here / "vn-mach-control.html").write_text(page)
print(f"wrote vn-mach-control.html ({len(page) // 1024} KB)")
