"""Products and shelf zones for the planned four-zone shelf (simulated in the demo).

Each zone sits on its own load cell and holds one product. Unit weights are
what the ESP32 uses to turn a weight change into a unit count.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: float  # rupees
    unit_weight_g: float


@dataclass(frozen=True)
class Zone:
    zone_id: str
    aisle: str
    sku: str


PRODUCTS: dict[str, Product] = {
    p.sku: p
    for p in [
        Product("CHIPS", "Potato chips 52 g", 20.0, 55.0),
        Product("SOAP", "Bath soap 125 g", 45.0, 135.0),
        Product("SHAMPOO", "Shampoo 340 ml", 199.0, 380.0),
        Product("DRYFRUIT", "Dry fruits 200 g", 350.0, 215.0),
    ]
}

ZONES: dict[str, Zone] = {
    z.zone_id: z
    for z in [
        Zone("A3-Z1", "A3", "CHIPS"),
        Zone("A3-Z2", "A3", "SOAP"),
        Zone("A3-Z3", "A3", "SHAMPOO"),
        Zone("A3-Z4", "A3", "DRYFRUIT"),
    ]
}

# Products whose shelves carry sensors. Scans of other products are never
# questioned, because the store cannot see those picks.
INSTRUMENTED_SKUS = {z.sku for z in ZONES.values()}
