"""Play the three finale demo scenarios against a running API, no hardware needed.

    python simulate.py                 # all three shoppers
    python simulate.py swap            # one scenario: honest | forget | swap
    python simulate.py --url http://localhost:8000

Watch the staff dashboard at http://localhost:8000 while it runs.
"""

import argparse
import time

import httpx

ZONE = {"CHIPS": "A3-Z1", "SOAP": "A3-Z2", "SHAMPOO": "A3-Z3", "DRYFRUIT": "A3-Z4"}


class Store:
    def __init__(self, url):
        self.c = httpx.Client(base_url=url, timeout=10)

    def shopper(self, name):
        sid = self.c.post("/api/sessions", json={"name": name}).json()["id"]
        self.c.post(f"/api/sessions/{sid}/aisle", json={"aisle": "A3"})
        print(f"  {name} ({sid}) enters aisle A3")
        return sid

    def pick(self, sid, sku):
        self.c.post("/api/shelf", json={"zone": ZONE[sku], "delta": 1, "vision_sku": sku,
                                        "vision_conf": 0.92, "session_hint": sid})
        print(f"  shelf: {sku} picked")

    def scan(self, sid, sku):
        self.c.post(f"/api/sessions/{sid}/scan", json={"sku": sku})
        print(f"  phone: {sku} scanned")

    def leave(self, sid):
        self.c.post(f"/api/sessions/{sid}/aisle", json={"aisle": None})
        out = self.c.post(f"/api/sessions/{sid}/exit").json()
        print(f"  exit: {out['status'].upper()}  ₹{out['total']:.0f}  {out['messages'][-1:]}")


def honest(s, grace):
    print("Scenario 1 · honest shopper")
    sid = s.shopper("Ravi")
    for sku in ("CHIPS", "SOAP", "SHAMPOO"):
        s.pick(sid, sku); time.sleep(1); s.scan(sid, sku); time.sleep(1)
    s.leave(sid)


def forget(s, grace):
    print("Scenario 2 · forgets one scan, gets nudged")
    sid = s.shopper("Asha")
    s.pick(sid, "CHIPS"); s.scan(sid, "CHIPS")
    s.pick(sid, "SOAP")
    print(f"  ...waiting {grace:.0f}s grace period for the nudge")
    time.sleep(grace + 2)
    msgs = s.c.get("/api/state").json()
    nudge = [a for a in msgs["alerts"] if a["session_id"] == sid and a["kind"] == "nudge"]
    print(f"  phone shows: {nudge[0]['message'] if nudge else '(no nudge)'}")
    s.scan(sid, "SOAP")
    s.leave(sid)


def swap(s, grace):
    print("Scenario 3 · barcode swap")
    sid = s.shopper("Kiran")
    s.scan(sid, "CHIPS")
    s.pick(sid, "DRYFRUIT")
    print(f"  ...waiting {grace:.0f}s")
    time.sleep(grace + 2)
    s.leave(sid)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", choices=["honest", "forget", "swap"])
    ap.add_argument("--url", default="http://localhost:8000")
    a = ap.parse_args()
    store = Store(a.url)
    g = store.c.get("/api/catalog").json()["grace_s"]
    for name, fn in [("honest", honest), ("forget", forget), ("swap", swap)]:
        if a.scenario in (None, name):
            fn(store, g)
            print()
