// ScanTrust demo UI: shopper phone, smart shelf, staff dashboard.
// Runs against the FastAPI backend (default) or fully in the browser
// when window.SCANTRUST_MODE === "local" (needs engine.js loaded first).
(function () {
  const MODE = window.SCANTRUST_MODE === "local" ? "local" : "server";
  const $ = (sel) => document.querySelector(sel);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const now = () => Date.now() / 1000;

  // ---------- backend adapters ----------
  function localApi() {
    const { Reconciler, PRODUCTS, ZONES } = window.ScanTrust;
    let eng = new Reconciler(10);
    setInterval(() => eng.tick(now()), 1000);
    const wrap = (fn) => (...a) => { try { return Promise.resolve(fn(...a)); } catch (e) { return Promise.reject(e); } };
    return {
      catalog: wrap(() => ({ products: Object.values(PRODUCTS), zones: Object.values(ZONES), grace_s: eng.grace_s })),
      state: wrap(() => eng.snapshot()),
      start: wrap((name) => eng.start_session(name, now())),
      aisle: wrap((sid, aisle) => eng.enter_aisle(sid, aisle, now())),
      scan: wrap((sid, sku, qty = 1) => eng.scan(sid, sku, now(), qty)),
      exit: wrap((sid) => eng.exit(sid, now())),
      shelf: wrap((b) => eng.shelf_event(b.zone, b.delta, now(), b.vision_sku, b.vision_conf, b.session_hint)),
      resolve: wrap((id, outcome) => eng.resolve_alert(id, outcome, now())),
      reset: wrap(() => { eng = new Reconciler(10); }),
    };
  }

  function serverApi() {
    const call = async (method, path, body) => {
      const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      return r.json();
    };
    return {
      catalog: () => call("GET", "/api/catalog"),
      state: () => call("GET", "/api/state"),
      start: (name) => call("POST", "/api/sessions", { name }),
      aisle: (sid, aisle) => call("POST", `/api/sessions/${sid}/aisle`, { aisle }),
      scan: (sid, sku, qty = 1) => call("POST", `/api/sessions/${sid}/scan`, { sku, qty }),
      exit: (sid) => call("POST", `/api/sessions/${sid}/exit`),
      shelf: (b) => call("POST", "/api/shelf", b),
      resolve: (id, outcome) => call("POST", `/api/alerts/${id}/resolve`, { outcome }),
      reset: () => call("POST", "/api/reset"),
    };
  }

  const api = MODE === "local" ? localApi() : serverApi();

  // ---------- UI state ----------
  const ui = { products: [], zones: [], grace: 10, state: { sessions: [], alerts: [], log: [] },
    sid: null, stock: {}, camera: true, crowded: false, busy: false };
  const prod = (sku) => ui.products.find((p) => p.sku === sku);
  const session = () => ui.state.sessions.find((s) => s.id === ui.sid);

  async function refresh() {
    try { ui.state = await api.state(); render(); }
    catch (e) { $("#caption").textContent = "Cannot reach the ScanTrust server. Is it running?"; }
  }

  function caption(text) { $("#caption").textContent = text; }

  // ---------- actions ----------
  async function newShopper(name) {
    const s = await api.start(name);
    await api.aisle(s.id, "A3");
    ui.sid = s.id;
    await refresh();
    return s.id;
  }

  async function pick(zoneId, delta, sid = ui.sid) {
    const z = ui.zones.find((x) => x.zone_id === zoneId);
    if (delta > 0 && ui.stock[zoneId] <= 0) return;
    if (delta < 0 && ui.stock[zoneId] >= 6) return;
    ui.stock[zoneId] -= delta;
    await api.shelf({ zone: zoneId, delta, vision_sku: ui.camera ? z.sku : null, vision_conf: ui.camera ? 0.92 : 0,
      session_hint: ui.crowded ? null : sid });
    await refresh();
  }

  async function scan(sku, qty = 1, sid = ui.sid) {
    if (!sid) return;
    await api.scan(sid, sku, qty);
    await refresh();
  }

  // ---------- scenarios ----------
  const zoneOf = (sku) => ui.zones.find((z) => z.sku === sku).zone_id;

  async function countdown(label) {
    for (let t = Math.ceil(ui.grace) + 1; t > 0; t--) { caption(`${label} (${t}s)`); await sleep(1000); }
  }

  const scenarios = {
    async honest() {
      caption("Ravi enters aisle A3 with the app open");
      const sid = await newShopper("Ravi"); await sleep(1200);
      for (const sku of ["CHIPS", "SOAP", "SHAMPOO"]) {
        caption(`Ravi picks ${prod(sku).name} — the shelf weight drops`); await pick(zoneOf(sku), 1, sid); await sleep(1200);
        caption(`Ravi scans it on his phone`); await scan(sku, 1, sid); await sleep(1200);
      }
      caption("Ravi walks out — every pick matches a scan"); await api.exit(sid); await refresh();
      caption("Honest shopper: billed automatically, no alerts");
    },
    async forget() {
      caption("Asha enters aisle A3");
      const sid = await newShopper("Asha"); await sleep(1200);
      caption("Asha picks potato chips and scans them"); await pick(zoneOf("CHIPS"), 1, sid); await scan("CHIPS", 1, sid); await sleep(1200);
      caption("Asha picks a bath soap but forgets to scan it"); await pick(zoneOf("SOAP"), 1, sid);
      await countdown("Grace period before a nudge");
      await refresh();
      caption("Her phone shows a gentle nudge — she taps to add the soap"); await sleep(2200);
      await scan("SOAP", 1, sid); await sleep(1200);
      await api.exit(sid); await refresh();
      caption("Forgotten scan: fixed by the shopper, no staff needed");
    },
    async swap() {
      caption("Kiran enters aisle A3");
      const sid = await newShopper("Kiran"); await sleep(1200);
      caption("Kiran scans potato chips (₹20)…"); await scan("CHIPS", 1, sid); await sleep(1200);
      caption("…but takes dry fruits (₹350) from the shelf"); await pick(zoneOf("DRYFRUIT"), 1, sid);
      await countdown("Weight and camera disagree with the scan");
      await refresh(); await sleep(800);
      caption("Kiran tries to leave"); await api.exit(sid); await refresh();
      caption("Barcode swap: flagged to staff with the evidence; the gate stays shut");
    },
  };

  async function runScenario(key) {
    if (ui.busy) return;
    ui.busy = true; render();
    try { await scenarios[key](); } catch (e) { caption("Scenario stopped: " + e.message); }
    ui.busy = false; render();
  }

  // ---------- render ----------
  const STATUS = { shopping: ["Shopping", "info"], paid: ["Paid", "good"], hold: ["See counter", "bad"] };
  const KIND = { swap: ["Barcode swap", "bad"], exit_hold: ["Held at exit", "bad"], nudge: ["Nudge sent", "warn"],
    unassigned: ["Logged only", "muted"], restock: ["Restock", "muted"] };
  const rupees = (v) => "₹" + Number(v).toLocaleString("en-IN");
  const time = (ts) => new Date(ts * 1000).toLocaleTimeString("en-IN", { hour12: false });

  function render() {
    const s = session();
    const st = ui.state;
    document.body.classList.toggle("busy", ui.busy);
    document.querySelectorAll("[data-scenario],#reset").forEach((b) => (b.disabled = ui.busy));

    // shopper chips
    $("#shoppers").innerHTML = st.sessions.length
      ? st.sessions.map((x) => `<button class="chip ${x.id === ui.sid ? "on" : ""}" data-sid="${x.id}">${esc(x.name)}</button>`).join("")
      : `<span class="quiet">No shoppers yet</span>`;

    // phone
    const phone = $("#phone-body");
    if (!s) {
      phone.innerHTML = `<p class="empty">Add a shopper, or play a scenario above.</p>`;
    } else {
      const nudge = st.alerts.find((a) => a.kind === "nudge" && a.session_id === s.id && !a.resolved);
      const [label, tone] = STATUS[s.status];
      phone.innerHTML = `
        <div class="phone-top"><strong>${esc(s.name)}</strong><span class="pill ${tone}">${label}</span></div>
        <button class="beacon ${s.aisle ? "on" : ""}" id="beacon" ${s.status !== "shopping" ? "disabled" : ""}>
          <span class="dot"></span>${s.aisle ? "Aisle A3 beacon in range" : "Out of aisle A3"}</button>
        ${nudge ? `<div class="nudge" role="status"><p>${esc(nudge.message.split(": ").slice(1).join(": "))}</p>
          <button class="btn primary" data-scan="${nudge.sku}">Add to cart</button></div>` : ""}
        <ul class="scanlist">${ui.products.map((p) => `
          <li><div><span class="pname">${esc(p.name)}</span><span class="quiet">${rupees(p.price)}</span></div>
          <div class="qty"><button class="btn sm" data-unscan="${p.sku}" ${!(s.cart[p.sku] > 0) || s.status !== "shopping" ? "disabled" : ""} aria-label="Remove one ${esc(p.name)}">−</button>
          <span class="num">${s.cart[p.sku] || 0}</span>
          <button class="btn sm" data-scan="${p.sku}" ${s.status !== "shopping" ? "disabled" : ""}>Scan</button></div></li>`).join("")}
        </ul>
        <div class="total"><span>Cart</span><span class="num">${rupees(s.total)}</span></div>
        ${s.messages.length ? `<p class="msg">${esc(s.messages[s.messages.length - 1])}</p>` : ""}
        <button class="btn primary wide" id="exit" ${s.status !== "shopping" ? "disabled" : ""}>Walk out</button>`;
    }

    // shelf
    $("#zones").innerHTML = ui.zones.map((z) => {
      const p = prod(z.sku), units = ui.stock[z.zone_id];
      return `<div class="zone">
        <div class="zone-head"><span class="mono quiet">${z.zone_id}</span><span class="quiet">${rupees(p.price)}</span></div>
        <p class="pname">${esc(p.name)}</p>
        <p class="weight num">${(units * p.unit_weight_g).toLocaleString("en-IN")}<small> g</small></p>
        <p class="quiet">${units} of 6 units · ${p.unit_weight_g} g each</p>
        <div class="zone-btns"><button class="btn" data-pick="${z.zone_id}" ${!s || units <= 0 || s.status !== "shopping" ? "disabled" : ""}>Pick up</button>
        <button class="btn ghost" data-put="${z.zone_id}" ${!s || units >= 6 || s.status !== "shopping" ? "disabled" : ""}>Put back</button></div></div>`;
    }).join("");
    $("#camera").checked = ui.camera;
    $("#crowded").checked = ui.crowded;

    // dashboard
    const open = st.alerts.filter((a) => !a.resolved);
    const done = st.alerts.filter((a) => a.resolved).slice(0, 4);
    $("#open-count").textContent = open.length;
    $("#alerts").innerHTML = open.length || done.length ? [...open, ...done].map((a) => {
      const [label, tone] = KIND[a.kind];
      const act = !a.resolved && (a.kind === "swap" || a.kind === "exit_hold");
      return `<li class="alert ${tone} ${a.resolved ? "resolved" : ""}">
        <div class="alert-head"><span class="pill ${tone}">${label}</span><span class="mono quiet">${time(a.ts)}</span></div>
        <p>${esc(a.message)}</p>
        ${a.resolved ? `<p class="quiet">Resolved: ${esc(a.outcome)}</p>` : ""}
        ${act ? `<div class="alert-btns"><button class="btn primary sm" data-resolve="${a.id}" data-outcome="charged">Charge items</button>
          <button class="btn ghost sm" data-resolve="${a.id}" data-outcome="ok">False alarm</button></div>` : ""}</li>`;
    }).join("") : `<li class="empty">No alerts. Honest shoppers never appear here.</li>`;

    $("#sessions").innerHTML = st.sessions.length ? st.sessions.map((x) => {
      const [label, tone] = STATUS[x.status];
      return `<tr><td>${esc(x.name)}</td><td><span class="pill ${tone}">${label}</span></td><td class="num">${rupees(x.total)}</td></tr>`;
    }).join("") : `<tr><td colspan="3" class="quiet">No shoppers yet</td></tr>`;

    $("#log").innerHTML = st.log.slice(-8).reverse().map((e) => {
      const d = e.kind === "shelf" ? `${e.zone} ${e.delta > 0 ? "−" : "+"}${Math.abs(e.delta)} ${e.sku} · ${e.confidence}`
        : e.kind === "scan" ? `${e.session} scanned ${e.sku}${e.qty < 0 ? " (removed)" : ""}`
        : e.kind === "aisle" ? `${e.session} ${e.aisle ? "entered " + e.aisle : "left aisle"}`
        : e.kind === "exit" ? `${e.session} exit → ${e.status}`
        : e.kind === "session_start" ? `${e.session} started (${esc(e.name)})`
        : e.kind === "resolve" ? `alert ${e.alert} → ${e.outcome}` : e.kind;
      return `<li><span class="quiet">${time(e.ts)}</span> ${d}</li>`;
    }).join("") || `<li class="quiet">Events appear here as they happen</li>`;
  }

  // ---------- events ----------
  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("button");
    if (!b || b.disabled) return;
    try {
      if (b.dataset.scenario) return runScenario(b.dataset.scenario);
      if (b.id === "reset") { await api.reset(); ui.sid = null; ui.zones.forEach((z) => (ui.stock[z.zone_id] = 6)); caption("Store reset"); return refresh(); }
      if (b.dataset.sid) { ui.sid = b.dataset.sid; return render(); }
      if (b.dataset.scan) return scan(b.dataset.scan, 1);
      if (b.dataset.unscan) return scan(b.dataset.unscan, -1);
      if (b.dataset.pick) return pick(b.dataset.pick, 1);
      if (b.dataset.put) return pick(b.dataset.put, -1);
      if (b.dataset.resolve) { await api.resolve(Number(b.dataset.resolve), b.dataset.outcome); return refresh(); }
      if (b.id === "beacon") { const s = session(); await api.aisle(s.id, s.aisle ? null : "A3"); return refresh(); }
      if (b.id === "exit") { await api.exit(ui.sid); return refresh(); }
    } catch (e) { caption(e.message); }
  });

  $("#new-shopper").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = $("#shopper-name");
    const name = input.value.trim() || `Shopper ${ui.state.sessions.length + 1}`;
    input.value = "";
    await newShopper(name);
  });
  $("#camera").addEventListener("change", (e) => { ui.camera = e.target.checked; });
  $("#crowded").addEventListener("change", (e) => { ui.crowded = e.target.checked; });

  // ---------- boot ----------
  (async () => {
    try {
      const c = await api.catalog();
      ui.products = c.products; ui.zones = c.zones; ui.grace = c.grace_s;
      ui.zones.forEach((z) => (ui.stock[z.zone_id] = 6));
      $("#grace").textContent = `${ui.grace} s`;
      $("#mode").textContent = MODE === "local" ? "Runs in your browser" : "Connected to server";
    } catch (e) { caption("Cannot reach the ScanTrust server. Is it running?"); return; }
    await refresh();
    setInterval(refresh, 800);
  })();
})();
