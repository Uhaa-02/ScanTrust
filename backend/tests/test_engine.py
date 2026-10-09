from scantrust.engine import AlertKind, Reconciler

ZONE = {"CHIPS": "A3-Z1", "SOAP": "A3-Z2", "SHAMPOO": "A3-Z3", "DRYFRUIT": "A3-Z4"}


def shopper(r, name="Ravi", t=0.0):
    s = r.start_session(name, t)
    r.enter_aisle(s.id, "A3", t)
    return s


def pick(r, sku, t, n=1, seen=True, hint=None):
    return r.shelf_event(ZONE[sku], n, t, vision_sku=sku if seen else None,
                         vision_conf=0.9 if seen else 0.0, session_hint=hint)


def kinds(r, open_only=True):
    return [a.kind for a in r.alerts if not (open_only and a.resolved)]


def test_honest_shopper_pays_with_no_alerts():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    pick(r, "CHIPS", 1); r.scan(s.id, "CHIPS", 3)
    pick(r, "SHAMPOO", 5); r.scan(s.id, "SHAMPOO", 6)
    r.tick(60)
    out = r.exit(s.id, 61)
    assert out["status"] == "paid"
    assert out["total"] == 219
    assert kinds(r) == []


def test_scan_before_pick_is_fine():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    r.scan(s.id, "SOAP", 1)
    pick(r, "SOAP", 4)
    r.tick(60)
    assert r.exit(s.id, 61)["status"] == "paid"


def test_forgotten_scan_gets_nudged_then_fixed():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    pick(r, "SOAP", 1)
    r.tick(5)
    assert kinds(r) == []  # still inside grace period
    r.tick(12)
    assert kinds(r) == [AlertKind.NUDGE]
    assert "Bath soap" in s.messages[-1]
    r.scan(s.id, "SOAP", 15)
    assert kinds(r) == []  # nudge resolved by the scan
    assert r.exit(s.id, 20)["status"] == "paid"


def test_ignored_nudge_holds_at_exit():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    pick(r, "DRYFRUIT", 1)
    r.tick(20)
    out = r.exit(s.id, 30)
    assert out["status"] == "hold"
    assert AlertKind.EXIT_HOLD in kinds(r)


def test_barcode_swap_is_flagged_not_nudged():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    r.scan(s.id, "CHIPS", 1)       # scans the ₹20 item
    pick(r, "DRYFRUIT", 2)         # takes the ₹350 item
    r.tick(20)
    assert kinds(r) == [AlertKind.SWAP]
    assert r.exit(s.id, 25)["status"] == "hold"


def test_staff_resolution_charges_and_releases():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    r.scan(s.id, "CHIPS", 1)
    pick(r, "DRYFRUIT", 2)
    r.exit(s.id, 30)
    for a in [a for a in r.alerts if not a.resolved]:
        r.resolve_alert(a.id, "charged", 40)
    assert s.status == "paid"
    assert s.cart["DRYFRUIT"] == 1


def test_put_back_after_scan_removes_from_cart():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    pick(r, "SHAMPOO", 1); r.scan(s.id, "SHAMPOO", 2)
    r.shelf_event("A3-Z3", -1, 10, vision_sku="SHAMPOO", vision_conf=0.9)
    assert s.cart["SHAMPOO"] == 0
    assert r.exit(s.id, 30)["status"] == "paid"


def test_two_shoppers_without_tracking_is_logged_only():
    r = Reconciler(grace_s=10)
    a, b = shopper(r, "Ravi"), shopper(r, "Asha")
    pick(r, "SOAP", 1)
    r.tick(30)
    assert kinds(r) == [AlertKind.UNASSIGNED]
    assert r.exit(a.id, 31)["status"] == "paid"
    assert r.exit(b.id, 31)["status"] == "paid"


def test_hand_tracking_hint_assigns_the_right_shopper():
    r = Reconciler(grace_s=10)
    a, b = shopper(r, "Ravi"), shopper(r, "Asha")
    pick(r, "SOAP", 1, hint=b.id)
    r.tick(30)
    assert [x.session_id for x in r.alerts if x.kind == AlertKind.NUDGE] == [b.id]


def test_weight_only_pick_is_not_nudged_and_does_not_block_alone():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    pick(r, "CHIPS", 1, seen=False)  # camera blocked: medium confidence
    r.tick(30)
    assert kinds(r) == []
    assert r.exit(s.id, 31)["status"] == "paid"


def test_repeated_medium_gaps_do_block():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    pick(r, "CHIPS", 1, seen=False)
    pick(r, "SOAP", 2, seen=False)
    assert r.exit(s.id, 31)["status"] == "hold"


def test_wrong_shelf_item_raises_restock_and_tracks_real_product():
    r = Reconciler(grace_s=10)
    s = shopper(r)
    r.shelf_event("A3-Z1", 1, 1, vision_sku="SHAMPOO", vision_conf=0.9)
    assert AlertKind.RESTOCK in kinds(r)
    assert s.picked["SHAMPOO"] == 1
