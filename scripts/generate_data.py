"""Generate the simulated offer dataset (data/offers.csv).

The dataset is generated once with a fixed random seed and committed to the repository,
so every run of the application and every number in the report uses identical data.
Re-running this script reproduces the same file byte for byte.

Each company's behaviour follows its profile in the project brief (SolRuta is cheap and
slow, Camino Express is fast and direct to major cities, and so on). The rules live in
COMPANY_RULES below so they can be quoted and discussed in the report.

The last rows of the file are deliberately faulty or messy records that exercise the
validator (see FAULTY_ROWS and the "Dataset" section of the README).

Usage:  python scripts/generate_data.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 4420
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FIRST_DAY = date(2026, 7, 10)
DAYS = 7  # 10-16 July 2026, so a 12-14 July travel window actually filters something

MAJOR_CITIES = {
    "Barcelona", "Valencia", "Seville", "Malaga", "Granada",
    "Cordoba", "Bilbao", "Zaragoza", "Alicante",
}

COLUMNS = [
    "offer_id", "company_code", "destination", "departure", "arrival", "duration_min",
    "price_eur", "transfers", "fare_name", "changeable", "refundable", "change_fee_eur",
    "luggage_kg", "seats_available", "rating", "co2_kg",
]

# Fare tuple: (id letter, name, changeable, refundable, change fee EUR, price factor, luggage kg)
COMPANY_RULES = {
    "SR": {  # SolRuta: low-cost daytime, older fleet, more transfers on long routes
        "speed_kmh": 72, "eur_per_km": 0.052, "co2_per_km": 0.035, "co2_missing": 0.30,
        "rating": 3.5, "rating_missing": 0.10,
        "fares": [("B", "Basic", False, False, 0, 1.00, 15), ("P", "Plus", True, False, 5, 1.18, 20)],
    },
    "CE": {  # Camino Express: fast, direct to major cities, premium price
        "speed_kmh": 90, "eur_per_km": 0.100, "co2_per_km": 0.030, "co2_missing": 0.0,
        "rating": 4.4, "rating_missing": 0.03,
        "fares": [("S", "Standard", True, False, 10, 1.00, 25), ("X", "Business", True, True, 0, 1.30, 30)],
    },
    "CL": {  # CostaLink: cheap and direct on the coast, one transfer inland
        "speed_kmh": 78, "eur_per_km": 0.075, "co2_per_km": 0.031, "co2_missing": 0.0,
        "rating": 4.0, "rating_missing": 0.05,
        "fares": [("B", "Basic", False, False, 0, 1.00, 20), ("F", "Flex", True, True, 0, 1.25, 20)],
    },
    "VB": {  # VerdeBus: modern low-emission fleet, mid price, changeable by default
        "speed_kmh": 80, "eur_per_km": 0.085, "co2_per_km": 0.014, "co2_missing": 0.0,
        "rating": 4.3, "rating_missing": 0.05,
        "fares": [("E", "Eco", True, False, 5, 1.00, 23), ("F", "Eco Flex", True, True, 0, 1.20, 23)],
    },
    "NV": {  # NocheVia: late-evening departures, slower with a rest stop, cheap
        "speed_kmh": 70, "eur_per_km": 0.060, "co2_per_km": 0.033, "co2_missing": 0.15,
        "rating": 3.8, "rating_missing": 0.05,
        "fares": [("S", "Night Saver", False, False, 0, 1.00, 20), ("F", "Night Flex", True, False, 5, 1.15, 20)],
    },
}


def load_destinations() -> list[dict]:
    with (DATA_DIR / "destinations.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["coastal"] = row["coastal"] == "true"
        row["distance_km"] = int(row["distance_km"])
    return rows


def pick_times(rng: random.Random, count: int, first: int, last: int) -> list[int]:
    """Distinct departure times (minutes after midnight) on a 15-minute grid."""
    return sorted(rng.sample(range(first, last + 1, 15), count))


def departure_times(code: str, dest: dict, rng: random.Random) -> list[int]:
    if code == "SR":
        return pick_times(rng, 2, 7 * 60, 17 * 60)
    if code == "CE":
        return [pick_times(rng, 1, lo * 60, hi * 60)[0] for lo, hi in ((7, 9), (12, 14), (17, 19))]
    if code == "CL":
        if dest["coastal"]:
            return pick_times(rng, 1, 6 * 60, 11 * 60) + pick_times(rng, 1, 13 * 60, 21 * 60)
        return pick_times(rng, 1, 8 * 60, 18 * 60)
    if code == "VB":
        return pick_times(rng, 1, 8 * 60, 12 * 60) + pick_times(rng, 1, 14 * 60, 19 * 60)
    return pick_times(rng, 1, 21 * 60, 23 * 60 + 45)  # NV


def transfer_count(code: str, dest: dict, rng: random.Random) -> int:
    dist = dest["distance_km"]
    if code == "SR":
        return 0 if dist < 250 else rng.choice([0, 1, 1, 2])
    if code == "CE":
        return 0 if dest["name"] in MAJOR_CITIES else 1
    if code == "CL":
        return 0 if dest["coastal"] or dist < 150 else 1
    if code == "VB":
        return 0 if dist < 250 else rng.choice([0, 0, 1])
    return 0 if dist < 300 else rng.choice([0, 1])  # NV


def generate_offers(rng: random.Random) -> list[dict]:
    rows = []
    destinations = load_destinations()
    for day_index in range(DAYS):
        day = FIRST_DAY + timedelta(days=day_index)
        day_factor = 1.08 if day.weekday() in (4, 6) else 1.0  # Friday and Sunday peaks
        for dest in destinations:
            dist = dest["distance_km"]
            for code, rules in COMPANY_RULES.items():
                for minutes in departure_times(code, dest, rng):
                    transfers = transfer_count(code, dest, rng)
                    duration = dist / rules["speed_kmh"] * 60
                    duration += transfers * rng.randint(35, 70) + rng.randint(-10, 15)
                    if code == "NV" and dist >= 300:
                        duration += 60  # overnight rest stop
                    duration = max(30, int(5 * round(duration / 5)))
                    departure = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minutes)
                    arrival = departure + timedelta(minutes=duration)

                    rate = rules["eur_per_km"] * (0.88 if code == "CL" and dest["coastal"] else 1.0)
                    base_price = dist * rate * (1 - 0.08 * transfers) * day_factor * (1 + rng.uniform(-0.12, 0.12))

                    rating = None
                    if rng.random() >= rules["rating_missing"]:
                        rating = round(min(5.0, max(1.0, rng.gauss(rules["rating"], 0.3))), 1)
                    co2 = None
                    if rng.random() >= rules["co2_missing"]:
                        co2 = round(dist * rules["co2_per_km"] * (1 + 0.15 * transfers) * (1 + rng.uniform(-0.1, 0.1)), 1)

                    for letter, fare, changeable, refundable, fee, factor, luggage in rules["fares"]:
                        seats = 0 if rng.random() < 0.06 else rng.randint(1, 45)
                        rows.append({
                            "offer_id": f"{code}-{dest['code']}-{departure:%Y%m%d-%H%M}-{letter}",
                            "company_code": code,
                            "destination": dest["name"],
                            "departure": f"{departure:%Y-%m-%d %H:%M}",
                            "arrival": f"{arrival:%Y-%m-%d %H:%M}",
                            "duration_min": duration,
                            "price_eur": f"{max(4.5, base_price * factor):.2f}",
                            "transfers": transfers,
                            "fare_name": fare,
                            "changeable": str(changeable).lower(),
                            "refundable": str(refundable).lower(),
                            "change_fee_eur": f"{fee:.2f}",
                            "luggage_kg": luggage,
                            "seats_available": seats,
                            "rating": "" if rating is None else rating,
                            "co2_kg": "" if co2 is None else co2,
                        })
    return rows


def faulty(offer_id: str, company: str, destination: str, departure: str, arrival: str,
           duration: object, price: object, **overrides: object) -> dict:
    """A record that is valid apart from the fields passed in overrides (or the obvious one)."""
    row = {
        "offer_id": offer_id, "company_code": company, "destination": destination,
        "departure": departure, "arrival": arrival, "duration_min": duration, "price_eur": price,
        "transfers": 0, "fare_name": "Basic", "changeable": "false", "refundable": "false",
        "change_fee_eur": "0.00", "luggage_kg": 20, "seats_available": 10, "rating": 4.0, "co2_kg": 12.0,
    }
    row.update(overrides)
    return row


# Minutes ending in 7 never occur in generated data (15-minute grid), so these IDs cannot
# accidentally collide with generated offers.
FAULTY_ROWS = [
    # Rejected: impossible or inconsistent values
    faulty("SR-MLG-20260713-0807-B", "SR", "Malaga", "2026-07-13 08:07", "2026-07-13 15:22", 435, "-12.50"),
    faulty("SR-LIS-20260713-0907-B", "SR", "Lisbon", "2026-07-13 09:07", "2026-07-13 17:07", 480, "35.00"),
    faulty("CL-VLC-20260732-1007-B", "CL", "Valencia", "2026-07-32 10:07", "2026-07-32 14:37", 270, "24.00"),
    faulty("CE-BCN-20260713-1107-S", "CE", "Barcelona", "2026-07-13 11:07", "2026-07-13 09:30", 0, "61.00",
           changeable="true", change_fee_eur="10.00"),
    faulty("VB-SVQ-20260713-1207-E", "VB", "Seville", "2026-07-13 12:07", "2026-07-13 18:47", 400, "44.00",
           changeable="true", rating=7.5),
    faulty("NV-GRX-20260713-2107-S", "NV", "Granada", "2026-07-13 21:07", "2026-07-14 04:17", 430, ""),
    faulty("SR_MLG_123", "SR", "Malaga", "2026-07-13 10:15", "2026-07-13 17:45", 450, "28.00"),
    faulty("CL-ALC-20260713-1307-B", "CL", "Alicante", "2026-07-13 13:07", "2026-07-13 18:37", 330, "29.00",
           seats_available=-3),
    faulty("CE-MLG-20260713-1407-S", "CE", "Valencia", "2026-07-13 14:07", "2026-07-13 18:07", 240, "36.00",
           changeable="true"),
    faulty("XB-BCN-20260713-1507-B", "XB", "Barcelona", "2026-07-13 15:07", "2026-07-13 23:07", 480, "30.00"),
    faulty("SR-ZAZ-20260713-1607-B", "SR", "Zaragoza", "2026-07-13 16:07", "2026-07-13 20:37", 270, "17.00",
           transfers="one"),
    faulty("NV-SCQ-20260713-2207-S", "NV", "Santiago de Compostela", "2026-07-13 22:07", "2026-07-15 23:07",
           2940, "38.00"),
    # Corrected: the intended value is unambiguous
    faulty("VB-VLC-20260713-0937-E", "VB", "  valencia ", "2026-07-13 09:37", "2026-07-13 14:07", 270, "31.50",
           changeable="true", change_fee_eur="5.00"),
    faulty("CL-CAD-20260713-0707-F", "CL", "Cádiz", "2026-07-13 07:07", "2026-07-13 15:37", 510, "54.00",
           fare_name="Flex", changeable="true", refundable="true"),
    faulty("CE-BIO-20260713-0807-S", "CE", "Bilbao", "2026-07-13 08:07", "2026-07-13 12:37", 999, "42.00",
           changeable="true", change_fee_eur="10.00"),
]


def main() -> None:
    rng = random.Random(SEED)
    rows = generate_offers(rng)
    duplicate = dict(rows[0], price_eur="1.00")  # same ID as the first generated offer
    rows.extend(FAULTY_ROWS + [duplicate])
    path = DATA_DIR / "offers.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows ({len(FAULTY_ROWS) + 1} deliberately faulty or messy) to {path}")


if __name__ == "__main__":
    main()
