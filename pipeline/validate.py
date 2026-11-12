"""Plausibility checks. Anything that fails is dropped, never stored."""

# Plausible ranges in LBP. Wide enough for a large swing, narrow enough to catch
# typos such as a missing digit (143,300 instead of 1,433,000).
RANGES = {
    "g95": (700_000, 8_000_000),
    "g98": (700_000, 8_000_000),
    "diesel": (700_000, 8_000_000),
    "gas": (400_000, 5_000_000),
    "urban": (10_000, 200_000),
    "canister": (700_000, 8_000_000),
}

# 98 octane is always a little above 95 (historically 17,000 to 40,000 LL).
MAX_98_PREMIUM = 120_000

# Ministry formula: urban kWh = A + B * diesel canister. Published figures match within ~50 LL.
TARIFF_A = 5_476.0
TARIFF_B = 0.017705
TARIFF_TOLERANCE = 3_000


def in_range(key, v):
    lo, hi = RANGES[key]
    return v is not None and lo <= v <= hi


def fuel_ok(row):
    """row: dict with g95, g98, diesel and optional gas. Returns (ok, reason)."""
    for k in ("g95", "g98", "diesel"):
        if not in_range(k, row.get(k)):
            return False, f"{k}={row.get(k)} out of range"
    if row.get("gas") is not None and not in_range("gas", row["gas"]):
        return False, f"gas={row['gas']} out of range"
    premium = row["g98"] - row["g95"]
    if not (0 < premium <= MAX_98_PREMIUM):
        return False, f"98 not just above 95 (98-95={premium})"
    return True, ""


def tariff_ok(row):
    if not in_range("urban", row.get("urban")):
        return False, f"urban={row.get('urban')} out of range"
    c = row.get("canister")
    if c is not None:
        if not in_range("canister", c):
            return False, f"canister={c} out of range"
        est = TARIFF_A + TARIFF_B * c
        if abs(est - row["urban"]) > TARIFF_TOLERANCE:
            return False, f"urban {row['urban']} far from formula {est:.0f}"
    return True, ""
