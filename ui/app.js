(() => {
const { Sim, HeuristicDirector, BODIES, SOL_TICKS, money, label, describe } = VN;
const ARM = /*ARM_PRESETS*/[];
const $ = id => document.getElementById(id);
const fmtInt = n => Math.round(n).toLocaleString("en-US");
const cap = t => t[0].toUpperCase() + t.slice(1);

// Text and visuals that differ between bodies. Model numbers live in the engine (BODIES).
const BODY_UI = {
  mars: {
    subtitle: "Self-replicating robot colony, Mars", mapTitle: "Jezero-class site, 48 × 48 km",
    deposits: { hematite: ["122,46,27", "Hematite"], ice: ["191,230,242", "Ice"] },
    terrain: [150, 86, 52, 40, 26, 18],
    orders: [["chip", "electronics module"], ["robot", "complete, 2.5 t"], ["fab_tooling", "first fab needs one"],
      ["motor", "spare"], ["sensor", "spare"]],
    launch: [[20000, "$20,000 (early flights)"], [5000, "$5,000"], [1000, "$1,000"], [250, "$250 (mature)"]],
    launchDefault: 5000,
    tabs: { import: "Import chips", fab: "Build fab" },
    output: { title: "Chips made on Mars", get: x => x.chips_made, fmt: v => fmtInt(v), kpi: "Chips made" },
    notes: {
      you: "You're the director at mission control on Earth. Commands take a few hours to act on, and wait out the 14-sol solar conjunction blackout.",
      auto: "Imports chips while that's cheaper, then switches to building chip fabs once the chips still needed (plus revenue lost waiting for launch windows) cost more than fab tooling.",
      import: "Never builds a fab. Orders chips from Earth every launch window, every 780 sols.",
      fab: "Lands fab tooling with the first landing, then grows fabs with the fleet. Fabs make tooling for more fabs.",
    },
  },
  moon: {
    subtitle: "Self-replicating robot colony, the Moon", mapTitle: "South-pole ridge near Shackleton, 48 × 48 km",
    deposits: { ice: ["191,230,242", "Ice (shadowed craters)"] },
    terrain: [128, 126, 122, 34, 34, 32],
    orders: [["chip", "avionics, 9 kg"], ["robot", "complete, 300 kg"], ["anode", "each new MRE cell needs one"],
      ["mre_cell", "complete, from Earth"], ["motor", "spare"]],
    launch: [[1000000, "$1M (today's landers)"], [200000, "$200,000"], [20000, "$20,000 (Starship era)"], [5000, "$5,000 (mature)"]],
    launchDefault: 20000,
    tabs: { import: "Keep one cell", fab: null },
    output: { title: "Oxygen made", get: x => x.oxygen_t, fmt: v => `${v.toFixed(v < 10 ? 1 : 0)} t`, kpi: "Oxygen made" },
    notes: {
      you: "You're the director at mission control on Earth, 1.3 s away. A flight leaves every 30 days and lands 5 days later. Every robot needs an avionics module from Earth, and every new MRE cell needs an anode.",
      auto: "Adds an MRE cell for every 6 robots, ordering an anode for each, and orders avionics one doubling ahead. Metal from MRE cells is what limits growth.",
      import: "Keeps the one MRE cell the colony landed with and orders avionics every flight. Growth stays linear.",
      fab: "Same as Auto on the Moon.",
    },
  },
};

let sim, body, ui, mode = "auto", running = false, armIdx = 0, lastLog = 0, ended = false;

function storage(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
const day = () => body.day;
const station = () => body.station.name;

function applyBody(name) {
  body = BODIES[name]; ui = BODY_UI[name];
  $("s_body").value = name;
  $("subtitle").textContent = ui.subtitle;
  $("mapTitle").textContent = ui.mapTitle;
  $("dayUnit").textContent = day().toUpperCase();
  $("stepSol").textContent = `+1 ${day()}`;
  [...$("speed").options].forEach(o => { o.textContent = `${o.value} ${day()}${+o.value > 1 ? "s" : ""}/frame`; });
  [...$("s_sols").options].forEach(o => { o.textContent = `${o.value} ${day()}s`; });
  $("s_launch").innerHTML = ui.launch.map(([v, t]) => `<option value="${v}" ${v === ui.launchDefault ? "selected" : ""}>${t}</option>`).join("");
  $("l_chips").textContent = `${cap(body.label(body.vitamin))}s on first landing`;
  $("l_storms").hidden = !body.dust_storms;
  $("tab_import").textContent = ui.tabs.import;
  $("tab_fab").hidden = !ui.tabs.fab; if (ui.tabs.fab) $("tab_fab").textContent = ui.tabs.fab;
  $("l_build_fab").textContent = `Build ${station()}s`;
  $("l_target_fabs").textContent = `Target ${station()}s`;
  $("l_fab_share").textContent = `${cap(station())} crew`;
  $("l_chip_reserve").textContent = `${cap(body.label(body.vitamin))}s held for ${station()} kits`;
  $("t_chips").textContent = ui.output.title;
  $("t_fabs").textContent = `${cap(station())}s`;
  const dep = Object.values(ui.deposits).map(([rgb, name]) => `<span><i class="sq" style="background:rgb(${rgb})"></i>${name}</span>`).join("");
  $("legend").innerHTML = `
    <span><i style="background:#f4efe6"></i>Building copies</span>
    <span><i style="background:#79b8f0"></i>${cap(station())} crew</span>
    <span><i style="background:#f2c14e"></i>Contract work</span>
    <span><i style="background:#9aa5b1"></i>Harvesting</span>
    <span><i style="background:#ef5d52"></i>Self-repair</span>
    ${dep}
    <span><i class="sq" style="background:#ffffff;outline:1px solid #888"></i>Base and ${station()}s</span>
    <span><i class="sq" style="background:#f2c14e;transform:rotate(45deg)"></i>Contract site</span>`;
  terrain = null;
  buildOrderRows();
  storage("vn.body", name);
}

function newSim() {
  const days = +$("s_sols").value, arm = ARM[armIdx] || { success: 1, time_multiplier: 1 };
  const cfg = { body: body.name, seed: +$("s_seed").value, ticks: days * SOL_TICKS, seed_chips: +$("s_chips").value,
    dust_storms: $("s_storms").checked, skill_success: arm.success, skill_time_multiplier: arm.time_multiplier };
  const econ = { launch_cost_per_kg: +$("s_launch").value };
  sim = new Sim(cfg, econ, mode === "you" ? null : new HeuristicDirector(mode, 150, cfg.ticks, body));
  lastLog = 0; ended = false;
  $("log").innerHTML = "";
  $("summary").className = "card summary";
  $("solOf").textContent = `/ ${days}`;
  sim.log("landing", `Landing site ready. Plan the first landing in Earth orders: anything ordered before ${day()} 0 lands with it.`);
  setRunning(false);
  render(true);
}

function setMode(m) {
  if (m === "fab" && !ui.tabs.fab) m = "auto";
  mode = m;
  document.querySelectorAll(".tabs button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.mode === m)));
  if (sim) sim.director = m === "you" ? null : new HeuristicDirector(m, 150, sim.config.ticks, body);
  if (sim && m !== "you" && sim.t > 0)
    sim.director.fabOrdered = sim.world.fab_tooling > 0 || (sim.stats[`imported_${body.station.core}`] || 0) > 0;
  $("modeNote").textContent = ui.notes[m];
  $("policy").disabled = m !== "you"; $("ordersBox").disabled = m !== "you";
  storage("vn.mode", m);
  renderDesk();
}

function setRunning(r) {
  running = r && !ended;
  $("play").textContent = running ? "Pause" : (sim && sim.t > 0 ? "Resume" : "Play");
  if (running) requestAnimationFrame(loop);
}

function advance(days) {
  const ticks = Math.max(1, Math.round(days * SOL_TICKS));
  for (let i = 0; i < ticks; i++) {
    if (sim.t >= sim.config.ticks) { finish(); break; }
    sim.step();
    if (!sim.population && !sim.econ.in_transit.length && sim.t > 1) { finish(); break; }
  }
}
function loop() {
  if (!running) return;
  advance(+$("speed").value);
  render();
  if (running) requestAnimationFrame(loop);
}
function finish() {
  if (ended) return;
  ended = true; running = false;
  sim.sample();
  const s = sim.snapshot(), led = s.ledger, h = sim.history[sim.history.length - 1];
  const rows = Object.entries(led).sort((a, b) => a[1] - b[1])
    .map(([k, v]) => `<tr><td>${label(k)}</td><td class="r num ${v < 0 ? "bad-t" : "good-t"}">${money(v)}</td></tr>`).join("");
  const el = $("summary");
  el.innerHTML = `<h2>Mission complete, ${day()} ${s.sol}</h2>
    <p style="margin:8px 0 10px;font-size:15px">Final cash <b class="num">${money(s.cash)}</b> with <b class="num">${s.population}</b> robots,
    <b class="num">${s.fabs}</b> ${station()}${s.fabs === 1 ? "" : "s"} and <b class="num">${ui.output.fmt(ui.output.get(h))}</b>
    ${ui.output.kpi.toLowerCase()}. Try the same seed with another director to compare.</p>
    <div class="scroll"><table><tbody>${rows}<tr><td><b>Starting capital</b></td><td class="r num">${money(sim.econ.cfg.starting_cash)}</td></tr></tbody></table></div>`;
  el.className = "card summary show";
  setRunning(false);
  render(true);
}

// ---- rendering -------------------------------------------------------------
const ROLE_COLOR = { replicate: "#f4efe6", fab: "#79b8f0", contract: "#f2c14e", harvest: "#9aa5b1", repair: "#ef5d52" };
let terrain = null;
function buildTerrain(size, seed) {
  const c = document.createElement("canvas"); c.width = c.height = size;
  const x = c.getContext("2d"), img = x.createImageData(size, size);
  let s = seed * 9301 + 49297;
  const rnd = () => (s = (s * 9301 + 49297) % 233280) / 233280;
  const h = []; for (let i = 0; i < size * size; i++) h.push(rnd());
  for (let pass = 0; pass < 3; pass++) for (let i = 0; i < size * size; i++) {
    const xx = i % size, yy = Math.floor(i / size); let sum = 0, n = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const X = xx + dx, Y = yy + dy; if (X >= 0 && Y >= 0 && X < size && Y < size) { sum += h[Y * size + X]; n++; } }
    h[i] = sum / n;
  }
  const [r0, g0, b0, rv, gv, bv] = ui.terrain;
  for (let i = 0; i < size * size; i++) {
    const v = (h[i] - 0.5) * 2.4;
    img.data[i * 4] = r0 + v * rv; img.data[i * 4 + 1] = g0 + v * gv; img.data[i * 4 + 2] = b0 + v * bv; img.data[i * 4 + 3] = 255;
  }
  x.putImageData(img, 0, 0);
  return c;
}
function drawMap() {
  const cv = $("map"), ctx = cv.getContext("2d"), w = sim.world, N = w.size, cell = cv.width / N;
  const tkey = `${body.name}|${sim.config.seed}`;
  if (!terrain || terrain.key !== tkey) { terrain = buildTerrain(N, sim.config.seed); terrain.key = tkey; }
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(terrain, 0, 0, cv.width, cv.height);
  for (const [rt, [rgb]] of Object.entries(ui.deposits)) {
    for (const [k, amt] of w.deposits[rt] || []) {
      const x = Math.floor(k / 1000), y = k % 1000;
      ctx.fillStyle = `rgba(${rgb},${Math.min(1, 0.35 + amt / 6000)})`;
      ctx.fillRect(x * cell, y * cell, cell, cell);
    }
  }
  for (const c of sim.econ.active()) {
    const [x, y] = c.site, cx = (x + .5) * cell, cy = (y + .5) * cell, r = cell * .75;
    ctx.fillStyle = "#f2c14e"; ctx.strokeStyle = "#3a2418"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath(); ctx.fill(); ctx.stroke();
  }
  const [bx, by] = w.depot;
  ctx.fillStyle = "#ffffff"; ctx.strokeStyle = "#1b1b1b"; ctx.lineWidth = 2;
  ctx.fillRect((bx - 1) * cell, (by - 1) * cell, cell * 3, cell * 3); ctx.strokeRect((bx - 1) * cell, (by - 1) * cell, cell * 3, cell * 3);
  ctx.fillStyle = "#1b1b1b"; ctx.font = `600 ${Math.round(cell * 1.3)}px "Barlow Condensed", sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(w.n_fabs ? `${body.name === "moon" ? "M" : "F"}${w.n_fabs}` : "B", (bx + .5) * cell, (by + .55) * cell);
  const occ = new Map();
  for (const r of sim.robots) {
    if (!r.alive) continue;
    const k = r.pos[0] * 1000 + r.pos[1], n = occ.get(k) || 0; occ.set(k, n + 1);
    if (n > 3) continue;
    const ox = (n % 2) * cell * .5, oy = Math.floor(n / 2) * cell * .5;
    ctx.fillStyle = ROLE_COLOR[r.role] || "#fff";
    ctx.beginPath(); ctx.arc(r.pos[0] * cell + cell * .27 + ox, r.pos[1] * cell + cell * .27 + oy, cell * .26, 0, Math.PI * 2); ctx.fill();
  }
  let light;
  if (body.sunlight === "polar") {
    const dark = w.dark(sim.t);
    if (dark) { ctx.fillStyle = "rgba(6,8,16,0.55)"; ctx.fillRect(0, 0, cv.width, cv.height); }
    light = dark ? "Terrain shadow: no sun" : "Sunlit";
  } else {
    const sun = w.solarFactor(sim.t) / Math.PI;
    const night = Math.max(0, 0.45 - sun * 0.6);
    if (night > 0) { ctx.fillStyle = `rgba(8,14,30,${night})`; ctx.fillRect(0, 0, cv.width, cv.height); }
    light = sun > 0.05 ? "Day" : "Night";
  }
  if (w.storm(sim.t)) { ctx.fillStyle = "rgba(214,140,70,0.38)"; ctx.fillRect(0, 0, cv.width, cv.height); }
  $("mapnote").textContent = `${w.storm(sim.t) ? "Dust storm · " : ""}${light} · ${fmtInt(sim.population)} robots`;
}

function chart(id, values, color, fmt) {
  const cv = $(id), dpr = window.devicePixelRatio || 1, W = cv.clientWidth || 240, H = 110;
  if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const line = css.getPropertyValue("--line").trim(), muted = css.getPropertyValue("--muted").trim();
  const pad = { l: 4, r: 4, t: 8, b: 16 }, iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const maxSol = sim.config.ticks / SOL_TICKS;
  let lo = Math.min(0, ...values.map(v => v[1])), hi = Math.max(1, ...values.map(v => v[1]));
  if (hi === lo) hi = lo + 1;
  ctx.strokeStyle = line; ctx.lineWidth = 1;
  for (let i = 0; i <= 2; i++) { const y = pad.t + ih * i / 2; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke(); }
  ctx.fillStyle = muted; ctx.font = "10.5px 'IBM Plex Mono', monospace"; ctx.textBaseline = "top";
  ctx.textAlign = "left"; ctx.fillText(fmt(hi), pad.l + 2, pad.t + 2);
  ctx.textAlign = "left"; ctx.fillText(`${day()} 0`, pad.l, H - 12); ctx.textAlign = "right"; ctx.fillText(`${maxSol}`, W - pad.r, H - 12);
  if (values.length < 2) return;
  const X = s => pad.l + iw * s / maxSol, Y = v => pad.t + ih * (1 - (v - lo) / (hi - lo));
  ctx.beginPath(); values.forEach(([s, v], i) => i ? ctx.lineTo(X(s), Y(v)) : ctx.moveTo(X(s), Y(v)));
  ctx.lineTo(X(values[values.length - 1][0]), Y(Math.max(lo, 0))); ctx.lineTo(X(values[0][0]), Y(Math.max(lo, 0))); ctx.closePath();
  ctx.globalAlpha = 0.14; ctx.fillStyle = color; ctx.fill(); ctx.globalAlpha = 1;
  ctx.beginPath(); values.forEach(([s, v], i) => i ? ctx.lineTo(X(s), Y(v)) : ctx.moveTo(X(s), Y(v)));
  ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.stroke();
  const [ls, lv] = values[values.length - 1];
  ctx.fillStyle = color; ctx.beginPath(); ctx.arc(X(ls), Y(lv), 3, 0, Math.PI * 2); ctx.fill();
}

function liveSample(s) {
  return { sol: s.sol, population: s.population, cash: s.cash, chips_made: s.chips_made, fabs: s.fabs,
    oxygen_t: (sim.stats.made_oxygen || 0) * body.kg_per_unit / 1000 };
}

function renderKpis(s) {
  const e = sim.econ, kr = sim.world.kits_required, shells = Math.floor(s.fab_kits / kr);
  const k = [
    ["Robots", fmtInt(s.population)],
    ["Cash", money(s.cash)],
    ["Revenue", money(s.ledger.contract_revenue || 0)],
    [`${cap(body.label(body.vitamin))}s in depot`, fmtInt(s.chips_in_depot)],
    [ui.output.kpi, ui.output.fmt(ui.output.get(liveSample(s)))],
    [`${cap(station())}s`, `${s.fabs}${s.fab_kits % kr || shells > s.fabs ? ` · ${s.fab_kits} kits` : ""}`],
    ["Launch $/kg", fmtInt(e.launchCostPerKg(sim.t))],
    [body.name === "moon" ? "Next flight" : "Next window", `${day()} ${s.next_window_sol}`],
  ];
  $("kpis").innerHTML = k.map(([a, b]) => `<div class="kpi"><span>${a}</span><b>${b}</b></div>`).join("");
  const pills = [];
  if (sim.world.storm(sim.t)) pills.push(`<span class="pill warn">Dust storm</span>`);
  if (sim.world.dark(sim.t)) pills.push(`<span class="pill warn">Polar shadow</span>`);
  if (sim.inConjunction(sim.t)) pills.push(`<span class="pill bad">Comms blackout</span>`);
  if (s.in_transit.length) pills.push(`<span class="pill">${s.in_transit.length} ship${s.in_transit.length > 1 ? "s" : ""} in transit</span>`);
  if (sim.pending.length) pills.push(`<span class="pill warn">${sim.pending.length} command${sim.pending.length > 1 ? "s" : ""} queued</span>`);
  const arm = ARM[armIdx]; if (arm) pills.push(`<span class="pill">${arm.controller} arm · ${arm.condition}${arm.delay_s ? ` · ${arm.delay_s}s` : ""}</span>`);
  $("status").innerHTML = pills.join("");
}

function renderDesk() {
  if (!sim) return;
  const p = sim.policy;
  if (mode !== "you" || !document.activeElement || !document.activeElement.id.startsWith("p_")) {
    $("p_replicate").checked = !!p.replicate; $("p_build_fab").checked = !!p.build_fab;
    $("p_target_fabs").value = p.target_fabs ?? 1; $("p_fab_share").value = p.fab_share ?? 0.5;
    $("p_contract_share").value = p.contract_share || 0; $("p_chip_reserve").value = p.chip_reserve || 0;
  }
  $("p_target_fabs").max = body.name === "moon" ? 60 : 12;
  $("v_target_fabs").textContent = $("p_target_fabs").value;
  $("v_fab_share").textContent = `${Math.round($("p_fab_share").value * 100)}%`;
  $("v_contract_share").textContent = `${Math.round($("p_contract_share").value * 100)}%`;
  $("v_chip_reserve").textContent = $("p_chip_reserve").value;
  const t = sim.t, e = sim.econ;
  ui.orders.forEach(([item]) => {
    const cell = document.getElementById(`lc_${item}`);
    if (cell) cell.textContent = money(e.landedCost(item, t));
  });
  updateOrderTotal();
  const nw = Math.floor(e.nextWindow(t) / SOL_TICKS);
  $("windowNote").textContent = t === 0 ? `Before ${day()} 0: orders land with the first landing` :
    `Next ${body.name === "moon" ? "flight" : "launch window"} ${day()} ${nw}, lands ${day()} ${nw + e.cfg.transit_sols}`;
  const man = Object.keys(e.manifest).length ? describe(e.manifest, body) : "empty";
  const transit = e.in_transit.map(s => `${describe(s.items, body)} (lands ${day()} ${Math.floor(s.arrives / SOL_TICKS)})`).join("; ");
  $("manifestNote").textContent = `Manifest: ${man}.${transit ? ` In transit: ${transit}.` : ""}`;
  renderContracts();
}
function orderQty() {
  const o = {};
  ui.orders.forEach(([i]) => { const v = Math.max(0, Math.floor(+$(`q_${i}`).value || 0)); if (v) o[i] = v; });
  return o;
}
function updateOrderTotal() {
  const o = orderQty(), cost = Object.keys(o).length ? sim.econ.manifestCost(o, sim.t) : 0;
  const fixed = money(sim.econ.cfg.shipment_fixed_cost);
  $("orderTotal").textContent = cost ? `This order: ${money(cost)} incl. ${fixed} per shipment · cash ${money(sim.econ.cash)}` : `Cash ${money(sim.econ.cash)}`;
}
function renderContracts() {
  const e = sim.econ, t = sim.t, you = mode === "you";
  const card = (c, active) => {
    const rem = e.remaining(c), req = c.requirements;
    const total = Object.values(req).reduce((a, b) => a + b, 0), left = Object.values(rem).reduce((a, b) => a + b, 0);
    const daysLeft = Math.floor((c.deadline - t) / SOL_TICKS);
    const needs = Object.entries(req).map(([k, q]) => `${q} ${body.label(k)}`).join(", ");
    return `<div class="contract ${active && daysLeft < 40 ? "late" : ""}">
      <div><div class="what">${label(c.kind)} <span class="num" style="font-weight:500">${money(c.payment)}</span></div>
      <div class="meta">${c.client} · ${needs} · ${active ? `${daysLeft} ${day()}s left` : `deadline in ${daysLeft} ${day()}s`}</div></div>
      ${active ? `<span class="num note">${Math.round(100 * (total - left) / total)}%</span>` :
        `<button data-accept="${c.id}" ${you ? "" : "disabled"}>Accept</button>`}
      ${active ? `<div class="progress"><div style="width:${100 * (total - left) / total}%"></div></div>` : ""}
    </div>`;
  };
  const act = e.active(), off = e.offers().slice(-8).reverse();
  $("contracts").innerHTML = (act.length ? act.map(c => card(c, true)).join("") : `<p class="note">No active contracts.</p>`) +
    (off.length ? `<p class="note" style="margin-top:4px">Offers (expire after 30 ${day()}s)</p>` + off.map(c => card(c, false)).join("") : "");
}
function renderLog() {
  const box = $("log"), evs = sim.events;
  if (evs.length === lastLog) return;
  const frag = evs.slice(lastLog).map(ev => `<div class="k-${ev.kind}"><span class="sol">${day()} ${ev.sol}</span><span>${ev.text}</span></div>`).join("");
  lastLog = evs.length;
  box.insertAdjacentHTML("afterbegin", frag.split("</div>").filter(Boolean).reverse().map(x => x + "</div>").join(""));
  while (box.children.length > 120) box.lastChild.remove();
}
let frame = 0;
function render(force) {
  const s = sim.snapshot();
  $("sol").textContent = s.sol;
  drawMap();
  if (force || frame++ % 3 === 0) {
    renderKpis(s); renderDesk(); renderLog();
    const h = sim.history.concat([liveSample(s)]);
    const css = getComputedStyle(document.documentElement);
    chart("ch_pop", h.map(x => [x.sol, x.population]), css.getPropertyValue("--ochre").trim(), v => fmtInt(v));
    chart("ch_cash", h.map(x => [x.sol, x.cash]), css.getPropertyValue("--accent").trim(), money);
    chart("ch_chips", h.map(x => [x.sol, ui.output.get(x)]), css.getPropertyValue("--good").trim(), ui.output.fmt);
    chart("ch_fabs", h.map(x => [x.sol, x.fabs]), css.getPropertyValue("--ink").trim(), v => fmtInt(v));
    $("c_pop").textContent = fmtInt(s.population); $("c_cash").textContent = money(s.cash);
    $("c_chips").textContent = ui.output.fmt(ui.output.get(h[h.length - 1])); $("c_fabs").textContent = s.fabs;
  }
}

// ---- wiring -------------------------------------------------------------------
function buildOrderRows() {
  $("orderRows").innerHTML = ui.orders.map(([i, note]) => `<tr><td>${body.label(i)}<div class="note">${note}</div></td>
    <td class="r num" id="lc_${i}"></td><td class="r"><input type="number" id="q_${i}" min="0" max="500" value="0" aria-label="${body.label(i)} quantity"></td></tr>`).join("");
  ui.orders.forEach(([i]) => $(`q_${i}`).addEventListener("input", updateOrderTotal));
}
function buildArmRows() {
  $("armRows").innerHTML = ARM.map((a, i) => {
    const asm = Math.pow(a.success, 4), cls = asm < 0.5 ? "bad-t" : asm < 0.85 ? "warn-t" : "good-t";
    const ctrl = a.controller === "teleop" ? `teleop from ${a.delay_s ? a.delay_s + " s away" : "next door"}` : a.controller;
    return `<tr data-arm="${i}" class="${i === armIdx ? "sel" : ""}" tabindex="0"><td>${ctrl}</td><td>${a.condition}</td>
      <td class="r num">${Math.round(a.success * 100)}%</td><td class="r num">${a.time_multiplier.toFixed(1)}</td>
      <td class="r num ${cls}">${Math.round(asm * 100)}%</td></tr>`;
  }).join("");
}
document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => setMode(b.dataset.mode)));
$("play").addEventListener("click", () => setRunning(!running));
$("stepSol").addEventListener("click", () => { if (!ended) { advance(1); render(true); } });
$("restart").addEventListener("click", () => newSim());
$("s_body").addEventListener("change", () => { applyBody($("s_body").value); newSim(); setMode(mode); });
$("armRows").addEventListener("click", e => {
  const tr = e.target.closest("tr[data-arm]"); if (!tr) return;
  armIdx = +tr.dataset.arm; storage("vn.arm", String(armIdx)); buildArmRows(); render(true);
  $("restart").textContent = "Restart with this arm";
});
$("armRows").addEventListener("keydown", e => { if (e.key === "Enter") e.target.click(); });
const policyInputs = ["p_replicate", "p_build_fab", "p_target_fabs", "p_fab_share", "p_contract_share", "p_chip_reserve"];
policyInputs.forEach(id => $(id).addEventListener("change", () => {
  if (mode !== "you") return;
  sim.command({ replicate: $("p_replicate").checked, build_fab: $("p_build_fab").checked,
    target_fabs: +$("p_target_fabs").value, fab_share: +$("p_fab_share").value,
    contract_share: +$("p_contract_share").value, chip_reserve: +$("p_chip_reserve").value });
  render(true);
}));
policyInputs.forEach(id => $(id).addEventListener("input", () => renderDesk()));
$("placeOrder").addEventListener("click", () => {
  const o = orderQty(); if (!Object.keys(o).length) return;
  sim.command({ orders: o });
  const when = sim.t === 0 ? " for the first landing" :
    ` for the ${day()} ${Math.floor(sim.econ.nextWindow(sim.t) / SOL_TICKS)} ${body.name === "moon" ? "flight" : "window"}`;
  sim.log("comms", `Ordered ${describe(o, body)}${when}`);
  ui.orders.forEach(([i]) => $(`q_${i}`).value = 0);
  render(true);
});
$("contracts").addEventListener("click", e => {
  const b = e.target.closest("button[data-accept]"); if (!b || mode !== "you") return;
  sim.command({ accept_contracts: [+b.dataset.accept] });
  render(true);
});
window.addEventListener("resize", () => sim && render(true));

const savedArm = +storage("vn.arm"); if (savedArm >= 0 && savedArm < ARM.length) armIdx = savedArm;
const savedMode = storage("vn.mode"), savedBody = storage("vn.body");
applyBody(BODIES[savedBody] ? savedBody : "mars");
buildArmRows();
mode = ["you", "auto", "import", "fab"].includes(savedMode) ? savedMode : "auto";
newSim(); setMode(mode);
// Open on a working colony when a strategy is in charge; as director you start at day 0 to plan the first landing.
if (mode !== "you") { advance(120); render(true); }
})();
