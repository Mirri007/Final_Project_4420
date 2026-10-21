"""Small builders for test objects, so each test states only what matters to it."""

from __future__ import annotations

import csv
import shutil
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from busbidder.config import PROJECT_ROOT, Settings, load_settings
from busbidder.loader import OFFER_COLUMNS
from busbidder.models import Company, Destination, FareConditions, Offer
from busbidder.request import DepartureWindow, TravelRequest, parse_clock

MALAGA = Destination("MLG", "Malaga", "Andalusia", True, 530)
VALENCIA = Destination("VLC", "Valencia", "Valencian Community", True, 355)
CAMINO = Company("CE", "Camino Express", "Fast direct connections")
SOLRUTA = Company("SR", "SolRuta", "Low-cost daytime services")
MORNING = DepartureWindow("morning", parse_clock("06:00"), parse_clock("12:00"))


def make_offer(offer_id: str = "CE-MLG-20260712-0800-S", *, company: Company = CAMINO,
               destination: Destination = MALAGA, departure: datetime = datetime(2026, 7, 12, 8, 0),
               duration: int = 360, price: float = 40.0, transfers: int = 0, changeable: bool = True,
               refundable: bool = False, luggage: int = 20, seats: int = 10, rating: float | None = 4.0,
               co2: float | None = 15.0, fare_name: str = "Standard") -> Offer:
    return Offer(
        offer_id=offer_id,
        company=company,
        destination=destination,
        departure=departure,
        arrival=departure + timedelta(minutes=duration),
        price_eur=price,
        transfers=transfers,
        seats_available=seats,
        fare=FareConditions(fare_name, changeable, refundable, 0.0, luggage),
        rating=rating,
        co2_kg=co2,
    )


def make_request(**overrides) -> TravelRequest:
    values = dict(destination=MALAGA, date_from=date(2026, 7, 12), date_to=date(2026, 7, 14))
    values.update(overrides)
    return TravelRequest(**values)


def test_settings(tmp_path: Path, **overrides) -> Settings:
    """Real data and profiles, but output and logs go to a temporary directory."""
    base = load_settings(PROJECT_ROOT / "config" / "settings.json")
    return replace(base, output_dir=tmp_path / "output", log_file=tmp_path / "logs" / "test.log", **overrides)


test_settings.__test__ = False  # not a test, despite the name


VALID_ROW = {
    "offer_id": "CE-MLG-20260712-0800-S", "company_code": "CE", "destination": "Malaga",
    "departure": "2026-07-12 08:00", "arrival": "2026-07-12 14:00", "duration_min": "360",
    "price_eur": "40.00", "transfers": "0", "fare_name": "Standard", "changeable": "true",
    "refundable": "false", "change_fee_eur": "10.00", "luggage_kg": "25", "seats_available": "12",
    "rating": "4.4", "co2_kg": "15.9",
}


def make_data_dir(tmp_path: Path, rows: list[dict]) -> Path:
    """A data directory with the real companies/destinations and the given offer rows."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    for name in ("companies.csv", "destinations.csv"):
        shutil.copy(PROJECT_ROOT / "data" / name, data_dir / name)
    with (data_dir / "offers.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OFFER_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return data_dir
