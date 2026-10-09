// ScanTrust reconciliation engine, JavaScript port of backend/scantrust/engine.py.
// Used by the in-browser live demo so it runs with no server. Keep the two in sync;
// the Python version is the reference and has the test suite.
(function (global) {
  const PRODUCTS = {
    CHIPS: { sku: "CHIPS", name: "Potato chips 52 g", price: 20, unit_weight_g: 55 },
    SOAP: { sku: "SOAP", name: "Bath soap 125 g", price: 45, unit_weight_g: 135 },
    SHAMPOO: { sku: "SHAMPOO", name: "Shampoo 340 ml", price: 199, unit_weight_g: 380 },
    DRYFRUIT: { sku: "DRYFRUIT", name: "Dry fruits 200 g", price: 350, unit_weight_g: 215 },
  };
  const ZONES = {
    "A3-Z1": { zone_id: "A3-Z1", aisle: "A3", sku: "CHIPS" },
    "A3-Z2": { zone_id: "A3-Z2", aisle: "A3", sku: "SOAP" },
    "A3-Z3": { zone_id: "A3-Z3", aisle: "A3", sku: "SHAMPOO" },
    "A3-Z4": { zone_id: "A3-Z4", aisle: "A3", sku: "DRYFRUIT" },
  };
  const INSTRUMENTED = new Set(Object.values(ZONES).map((z) => z.sku));
  const name = (s) => PRODUCTS[s].name;
  const price = (s) => PRODUCTS[s].price;
  const n = (m, k) => m[k] || 0;

  class Reconciler {
    constructor(grace_s = 10, scan_window_s = 120) {
      this.grace_s = grace_s;
      this.scan_window_s = scan_window_s;
      this.sessions = {};
      this.alerts = [];
      this.log = [];
      this._sid = 0;
      this._aid = 0;
    }

    start_session(nm, now) {
      const id = "S" + String(++this._sid).padStart(3, "0");
      this.sessions[id] = { id, name: nm, started: now, aisle: null, status: "shopping",
        cart: {}, picked: {}, picks: [], scans: [], nudged: new Set(), total: 0, messages: [] };
      this._log(now, "session_start", { session: id, name: nm });
      return this._dict(this.sessions[id]);
    }

    enter_aisle(sid, aisle, now) {
      this._s(sid).aisle = aisle;
      this._log(now, "aisle", { session: sid, aisle });
    }

    scan(sid, sku, now, qty = 1) {
      const s = this._s(sid);
      if (!PRODUCTS[sku]) throw new Error("unknown product " + sku);
      if (qty < 0 && n(s.cart, sku) + qty < 0) qty = -n(s.cart, sku);
      s.cart[sku] = n(s.cart, sku) + qty;
      s.scans.push({ sku, ts: now, qty });
      this._log(now, "scan", { session: sid, sku, qty });
      if (n(s.cart, sku) >= n(s.picked, sku)) {
        s.nudged.delete(sku);
        this._resolveOpen(sid, sku, "nudge", "scanned");
      }
    }

    shelf_event(zone_id, delta, now, vision_sku = null, vision_conf = 0, hint = null) {
      const zone = ZONES[zone_id];
      let sku = zone.sku;
      const agrees = vision_sku === sku && vision_conf >= 0.6;
      if (vision_sku && vision_sku !== sku && vision_conf >= 0.6) {
        sku = vision_sku;
        this._alert("restock", null, sku, `${name(sku)} found on ${zone_id} (wrong shelf)`, now);
      }
      const cands = this._candidates(zone.aisle, hint);
      const conf = cands.length !== 1 ? "low" : agrees ? "high" : "medium";
      const ev = { zone: zone_id, sku, delta, confidence: conf, session: cands.length === 1 ? cands[0].id : null };
      this._log(now, "shelf", ev);
      if (conf === "low") {
        this._alert("unassigned", null, sku,
          `${Math.abs(delta)} × ${name(sku)} moved on ${zone_id}; ${cands.length} shoppers in reach — logged only`, now);
        return ev;
      }
      const s = cands[0];
      if (delta > 0) {
        s.picked[sku] = n(s.picked, sku) + delta;
        for (let i = 0; i < delta; i++) s.picks.push({ sku, ts: now, confidence: conf, zone_id });
      } else {
        const back = Math.min(-delta, n(s.picked, sku));
        s.picked[sku] = n(s.picked, sku) - back;
        for (let i = 0; i < back; i++) {
          const j = s.picks.map((p) => p.sku).lastIndexOf(sku);
          if (j >= 0) s.picks.splice(j, 1);
        }
        if (n(s.cart, sku) > n(s.picked, sku)) {
          s.cart[sku] = n(s.picked, sku);
          s.messages.push(`${name(sku)} put back — removed from your cart`);
        }
        s.nudged.delete(sku);
        this._resolveOpen(s.id, sku, "nudge", "put back");
      }
      return ev;
    }

    tick(now) {
      for (const s of Object.values(this.sessions)) {
        if (s.status !== "shopping") continue;
        for (const sku of Object.keys(s.picked)) {
          if (n(s.picked, sku) - n(s.cart, sku) <= 0 || s.nudged.has(sku)) continue;
          const oldest = this._oldest(s, sku);
          if (!oldest || now - oldest.ts < this.grace_s || oldest.confidence !== "high") continue;
          const swap = this._swap(s, sku, oldest.ts);
          s.nudged.add(sku);
          if (swap) {
            this._alert("swap", s.id, sku,
              `${s.name}: picked ${name(sku)} (₹${price(sku)}) but scanned ${name(swap)} (₹${price(swap)})`, now);
          } else {
            const msg = `Looks like you picked up ${name(sku)}. Tap to add it.`;
            s.messages.push(msg);
            this._alert("nudge", s.id, sku, `${s.name}: ${msg}`, now);
          }
        }
      }
    }

    exit(sid, now) {
      this.tick(now + this.grace_s);
      const s = this._s(sid);
      const high = [], medium = [];
      for (const sku of Object.keys(s.picked)) {
        if (n(s.picked, sku) - n(s.cart, sku) <= 0) continue;
        const p = this._oldest(s, sku);
        (p && p.confidence === "high" ? high : medium).push(sku);
      }
      const swap = this.alerts.some((a) => a.kind === "swap" && a.session_id === s.id && !a.resolved);
      if (high.length || swap || medium.length >= 2) {
        s.status = "hold";
        const items = [...high, ...medium].map(name).join(", ") || "see swap alert";
        this._alert("exit_hold", s.id, null, `${s.name} at exit with unpaid items: ${items}`, now);
        s.messages.push("Please see the counter before leaving.");
      } else {
        s.status = "paid";
        s.total = this._total(s);
        s.messages.push(`Paid ₹${s.total}. Thank you!`);
      }
      this._log(now, "exit", { session: s.id, status: s.status });
      return this._dict(s);
    }

    resolve_alert(id, outcome, now) {
      const a = this.alerts.find((x) => x.id === id);
      if (!a) throw new Error("unknown alert");
      a.resolved = true; a.outcome = outcome;
      if (a.session_id && (a.kind === "swap" || a.kind === "exit_hold")) {
        const s = this._s(a.session_id);
        if (outcome === "charged") for (const k of Object.keys(s.picked)) s.cart[k] = Math.max(n(s.cart, k), n(s.picked, k));
        const open = this.alerts.some((x) => x.session_id === s.id && !x.resolved && (x.kind === "swap" || x.kind === "exit_hold"));
        if (s.status === "hold" && !open) {
          s.status = "paid"; s.total = this._total(s);
          s.messages.push(`Cleared by staff. Paid ₹${s.total}.`);
        }
      }
      this._log(now, "resolve", { alert: id, outcome });
      return { ...a };
    }

    snapshot() {
      return {
        sessions: Object.values(this.sessions).map((s) => this._dict(s)),
        alerts: [...this.alerts].reverse().map((a) => ({ ...a })),
        log: this.log.slice(-30),
      };
    }

    _candidates(aisle, hint) {
      if (hint && this.sessions[hint]) return [this.sessions[hint]];
      return Object.values(this.sessions).filter((s) => s.aisle === aisle && s.status === "shopping");
    }
    _oldest(s, sku) {
      const picks = s.picks.filter((p) => p.sku === sku);
      const c = Math.max(n(s.cart, sku), 0);
      return c < picks.length ? picks[c] : null;
    }
    _swap(s, picked, ts) {
      for (let i = s.scans.length - 1; i >= 0; i--) {
        const sc = s.scans[i];
        if (sc.sku !== picked && sc.qty > 0 && INSTRUMENTED.has(sc.sku) && n(s.cart, sc.sku) > n(s.picked, sc.sku)
          && Math.abs(sc.ts - ts) <= this.scan_window_s && price(sc.sku) < price(picked)) return sc.sku;
      }
      return null;
    }
    _resolveOpen(sid, sku, kind, outcome) {
      for (const a of this.alerts) if (a.session_id === sid && a.sku === sku && a.kind === kind && !a.resolved) { a.resolved = true; a.outcome = outcome; }
    }
    _alert(kind, session_id, sku, message, ts) {
      this.alerts.push({ id: ++this._aid, kind, session_id, sku, message, ts, resolved: false, outcome: null });
    }
    _total(s) { return Object.entries(s.cart).reduce((t, [k, q]) => t + (q > 0 ? price(k) * q : 0), 0); }
    _dict(s) {
      const pos = (m) => Object.fromEntries(Object.entries(m).filter(([, v]) => v > 0));
      return { id: s.id, name: s.name, aisle: s.aisle, status: s.status, cart: pos(s.cart),
        picked: pos(s.picked), total: this._total(s), messages: s.messages.slice(-5) };
    }
    _s(sid) { const s = this.sessions[sid]; if (!s) throw new Error("unknown session " + sid); return s; }
    _log(ts, kind, data) { this.log.push({ ts, kind, ...data }); }
  }

  global.ScanTrust = { Reconciler, PRODUCTS, ZONES };
})(typeof window !== "undefined" ? window : globalThis);
