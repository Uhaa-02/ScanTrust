"""ScanTrust reconciliation engine.

The phone says what a shopper intends to buy (scans). The shelf (weight) and
the camera (vision) say what was actually taken (picks). The engine keeps a
per-shopper ledger of picks vs scans and decides, for every mismatch that
outlives a grace period, whether to do nothing, nudge the shopper, or flag it
for staff.

Design rule: be precise rather than perfect. Only high-confidence picks ever
trigger an action; uncertain ones are logged for analytics.

Every method takes an explicit `now` (seconds) so the logic is deterministic
and easy to test.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from .catalog import INSTRUMENTED_SKUS, PRODUCTS, ZONES


class Confidence(str, Enum):
    HIGH = "high"      # one shopper in reach + camera agrees with the shelf
    MEDIUM = "medium"  # one shopper, but camera missing or disagreeing
    LOW = "low"        # cannot tell who picked it: log only


class AlertKind(str, Enum):
    NUDGE = "nudge"            # sent to the shopper's phone
    SWAP = "swap"              # scanned one product, took another
    EXIT_HOLD = "exit_hold"    # unpaid picks still open at the exit
    UNASSIGNED = "unassigned"  # pick with no clear shopper (analytics)
    RESTOCK = "restock"        # item put back on the wrong shelf


@dataclass
class Pick:
    sku: str
    ts: float
    confidence: Confidence
    zone_id: str


@dataclass
class Scan:
    sku: str
    ts: float
    qty: int


@dataclass
class Alert:
    id: int
    kind: AlertKind
    session_id: str | None
    sku: str | None
    message: str
    ts: float
    resolved: bool = False
    outcome: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "session_id": self.session_id,
            "sku": self.sku,
            "message": self.message,
            "ts": self.ts,
            "resolved": self.resolved,
            "outcome": self.outcome,
        }


@dataclass
class Session:
    id: str
    name: str
    started: float
    aisle: str | None = None
    status: str = "shopping"  # shopping | paid | hold
    cart: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    picked: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    picks: list[Pick] = field(default_factory=list)
    scans: list[Scan] = field(default_factory=list)
    nudged: set[str] = field(default_factory=set)
    total: float = 0.0
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "aisle": self.aisle,
            "status": self.status,
            "cart": {k: v for k, v in self.cart.items() if v > 0},
            "picked": {k: v for k, v in self.picked.items() if v > 0},
            "total": round(self.cart_total(), 2),
            "messages": self.messages[-5:],
        }

    def cart_total(self) -> float:
        return sum(PRODUCTS[s].price * q for s, q in self.cart.items() if q > 0)


class Reconciler:
    def __init__(self, grace_s: float = 15.0, scan_window_s: float = 120.0):
        self.grace_s = grace_s
        self.scan_window_s = scan_window_s
        self.sessions: dict[str, Session] = {}
        self.alerts: list[Alert] = []
        self.log: list[dict] = []
        self._ids = itertools.count(1)
        self._alert_ids = itertools.count(1)

    # ---- sessions and phone events -------------------------------------
    def start_session(self, name: str, now: float) -> Session:
        sid = f"S{next(self._ids):03d}"
        s = Session(id=sid, name=name, started=now)
        self.sessions[sid] = s
        self._log(now, "session_start", session=sid, name=name)
        return s

    def enter_aisle(self, session_id: str, aisle: str | None, now: float) -> None:
        """Called when the phone hears an aisle beacon (None = left the aisle)."""
        s = self._session(session_id)
        s.aisle = aisle
        self._log(now, "aisle", session=session_id, aisle=aisle)

    def scan(self, session_id: str, sku: str, now: float, qty: int = 1) -> None:
        s = self._session(session_id)
        if sku not in PRODUCTS:
            raise KeyError(f"unknown product {sku}")
        if qty < 0 and s.cart[sku] + qty < 0:
            qty = -s.cart[sku]
        s.cart[sku] += qty
        s.scans.append(Scan(sku, now, qty))
        self._log(now, "scan", session=session_id, sku=sku, qty=qty)
        # A scan that closes the gap resolves any open nudge for this product.
        if s.cart[sku] >= s.picked[sku]:
            s.nudged.discard(sku)
            self._resolve_open(session_id, sku, AlertKind.NUDGE, "scanned")

    # ---- shelf events (weight + vision, already fused per zone) ---------
    def shelf_event(
        self,
        zone_id: str,
        delta_units: int,
        now: float,
        vision_sku: str | None = None,
        vision_conf: float = 0.0,
        session_hint: str | None = None,
    ) -> dict:
        """A weight change on a zone.

        delta_units > 0: units taken from the shelf (pick).
        delta_units < 0: units put back.
        vision_sku / vision_conf: what the camera saw in the hand, if anything.
        session_hint: shopper assigned by camera hand tracking, if any.
        """
        zone = ZONES[zone_id]
        sku = zone.sku
        vision_agrees = vision_sku == sku and vision_conf >= 0.6
        if vision_sku and vision_sku != sku and vision_conf >= 0.6:
            # Camera sees a different product: an item left on the wrong shelf.
            sku = vision_sku
            self._alert(AlertKind.RESTOCK, None, sku,
                        f"{PRODUCTS[sku].name} found on {zone_id} (wrong shelf)", now)

        candidates = self._candidates(zone.aisle, session_hint)
        if len(candidates) != 1:
            conf = Confidence.LOW
        elif vision_agrees:
            conf = Confidence.HIGH
        else:
            conf = Confidence.MEDIUM

        event = {"zone": zone_id, "sku": sku, "delta": delta_units,
                 "confidence": conf.value,
                 "session": candidates[0].id if len(candidates) == 1 else None}
        self._log(now, "shelf", **event)

        if conf == Confidence.LOW:
            self._alert(AlertKind.UNASSIGNED, None, sku,
                        f"{abs(delta_units)} × {PRODUCTS[sku].name} moved on {zone_id}; "
                        f"{len(candidates)} shoppers in reach — logged only", now)
            return event

        s = candidates[0]
        if delta_units > 0:
            s.picked[sku] += delta_units
            for _ in range(delta_units):
                s.picks.append(Pick(sku, now, conf, zone_id))
        else:
            back = min(-delta_units, s.picked[sku])
            s.picked[sku] -= back
            for _ in range(back):
                self._drop_latest_pick(s, sku)
            # Put back after scanning: keep the cart honest automatically.
            if s.cart[sku] > s.picked[sku]:
                s.cart[sku] = s.picked[sku]
                s.messages.append(f"{PRODUCTS[sku].name} put back — removed from your cart")
            s.nudged.discard(sku)
            self._resolve_open(s.id, sku, AlertKind.NUDGE, "put back")
        return event

    # ---- periodic evaluation -------------------------------------------
    def tick(self, now: float) -> None:
        for s in self.sessions.values():
            if s.status != "shopping":
                continue
            for sku in list(s.picked):
                gap = s.picked[sku] - s.cart[sku]
                if gap <= 0 or sku in s.nudged:
                    continue
                oldest = self._oldest_unexplained(s, sku)
                if oldest is None or now - oldest.ts < self.grace_s:
                    continue
                if oldest.confidence != Confidence.HIGH:
                    continue  # medium: tracked, judged at exit
                swap = self._swap_candidate(s, sku, oldest.ts)
                s.nudged.add(sku)
                if swap:
                    self._alert(AlertKind.SWAP, s.id, sku,
                                f"{s.name}: picked {PRODUCTS[sku].name} (₹{PRODUCTS[sku].price:.0f}) "
                                f"but scanned {PRODUCTS[swap].name} (₹{PRODUCTS[swap].price:.0f})", now)
                else:
                    msg = f"Looks like you picked up {PRODUCTS[sku].name}. Tap to add it."
                    s.messages.append(msg)
                    self._alert(AlertKind.NUDGE, s.id, sku, f"{s.name}: {msg}", now)

    # ---- exit -------------------------------------------------------------
    def exit(self, session_id: str, now: float) -> dict:
        self.tick(now + self.grace_s)  # judge any pending picks now
        s = self._session(session_id)
        open_high, open_medium = [], []
        for sku in s.picked:
            gap = s.picked[sku] - s.cart[sku]
            if gap <= 0:
                continue
            pick = self._oldest_unexplained(s, sku)
            (open_high if pick and pick.confidence == Confidence.HIGH else open_medium).append(sku)
        has_swap = any(a.kind == AlertKind.SWAP and a.session_id == s.id and not a.resolved
                       for a in self.alerts)

        # Medium-confidence gaps alone never block an honest shopper unless repeated.
        if open_high or has_swap or len(open_medium) >= 2:
            s.status = "hold"
            items = ", ".join(PRODUCTS[k].name for k in open_high + open_medium) or "see swap alert"
            self._alert(AlertKind.EXIT_HOLD, s.id, None,
                        f"{s.name} at exit with unpaid items: {items}", now)
            s.messages.append("Please see the counter before leaving.")
        else:
            s.status = "paid"
            s.total = s.cart_total()
            s.messages.append(f"Paid ₹{s.total:.0f}. Thank you!")
        self._log(now, "exit", session=s.id, status=s.status)
        return s.to_dict()

    def resolve_alert(self, alert_id: int, outcome: str, now: float) -> Alert:
        """Staff decision: 'ok' (false alarm) or 'charged' (add open items to bill)."""
        a = next(a for a in self.alerts if a.id == alert_id)
        a.resolved, a.outcome = True, outcome
        if a.session_id and a.kind in (AlertKind.SWAP, AlertKind.EXIT_HOLD):
            s = self._session(a.session_id)
            if outcome == "charged":
                for sku in s.picked:
                    s.cart[sku] = max(s.cart[sku], s.picked[sku])
            if s.status == "hold" and not self._open_for(s.id):
                s.status = "paid"
                s.total = s.cart_total()
                s.messages.append(f"Cleared by staff. Paid ₹{s.total:.0f}.")
        self._log(now, "resolve", alert=alert_id, outcome=outcome)
        return a

    def snapshot(self) -> dict:
        return {
            "sessions": [s.to_dict() for s in self.sessions.values()],
            "alerts": [a.to_dict() for a in reversed(self.alerts)],
            "log": self.log[-30:],
        }

    # ---- helpers -----------------------------------------------------------
    def _candidates(self, aisle: str, hint: str | None) -> list[Session]:
        if hint and hint in self.sessions:
            return [self.sessions[hint]]
        return [s for s in self.sessions.values() if s.aisle == aisle and s.status == "shopping"]

    def _oldest_unexplained(self, s: Session, sku: str) -> Pick | None:
        picks = [p for p in s.picks if p.sku == sku]
        covered = max(s.cart[sku], 0)
        return picks[covered] if covered < len(picks) else None

    def _swap_candidate(self, s: Session, picked_sku: str, pick_ts: float) -> str | None:
        """An instrumented product scanned around the pick but never taken."""
        for scan in reversed(s.scans):
            sku = scan.sku
            if (sku != picked_sku and scan.qty > 0 and sku in INSTRUMENTED_SKUS
                    and s.cart[sku] > s.picked[sku]
                    and abs(scan.ts - pick_ts) <= self.scan_window_s
                    and PRODUCTS[sku].price < PRODUCTS[picked_sku].price):
                return sku
        return None

    def _drop_latest_pick(self, s: Session, sku: str) -> None:
        for i in range(len(s.picks) - 1, -1, -1):
            if s.picks[i].sku == sku:
                del s.picks[i]
                return

    def _open_for(self, session_id: str) -> bool:
        return any(a.session_id == session_id and not a.resolved
                   and a.kind in (AlertKind.SWAP, AlertKind.EXIT_HOLD) for a in self.alerts)

    def _resolve_open(self, session_id: str, sku: str, kind: AlertKind, outcome: str) -> None:
        for a in self.alerts:
            if a.session_id == session_id and a.sku == sku and a.kind == kind and not a.resolved:
                a.resolved, a.outcome = True, outcome

    def _alert(self, kind: AlertKind, session_id, sku, message, now) -> Alert:
        a = Alert(next(self._alert_ids), kind, session_id, sku, message, now)
        self.alerts.append(a)
        return a

    def _session(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            raise KeyError(f"unknown session {session_id}")
        return self.sessions[session_id]

    def _log(self, now: float, kind: str, **data) -> None:
        self.log.append({"ts": now, "kind": kind, **data})
