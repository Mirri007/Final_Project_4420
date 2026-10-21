"""Load and validate companies, destinations and offers from CSV files.

Validation policy
-----------------
* Reference data (companies, destinations) is small and hand-curated, and every offer
  depends on it. Any error there is fatal and raises DataFileError.
* Offers are bulk data. Each row is checked field by field and *all* problems in the row
  are collected. A row with any error is rejected and written to a quarantine CSV, with its
  reasons, for review. Nothing is dropped silently.
* A row is corrected only when the intended value is unambiguous:
    - a destination name that differs only in case, accents or whitespace;
    - a duration column that disagrees with (or is missing next to) valid departure and
      arrival timestamps; the timestamps win.
  Every correction is recorded in the report and the log.
* Sold-out offers (0 seats) are valid data. They are kept and counted, and the
  seat-availability hard constraint removes them from search results.
"""

from __future__ import annotations

import csv
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from busbidder.exceptions import DataFileError, RecordValidationError
from busbidder.models import Company, Destination, FareConditions, Offer

logger = logging.getLogger(__name__)

COMPANY_COLUMNS = ("code", "name", "profile")
DESTINATION_COLUMNS = ("code", "name", "region", "coastal", "distance_km")
OFFER_COLUMNS = (
    "offer_id", "company_code", "destination", "departure", "arrival", "duration_min",
    "price_eur", "transfers", "fare_name", "changeable", "refundable", "change_fee_eur",
    "luggage_kg", "seats_available", "rating", "co2_kg",
)

# Offer IDs encode company, destination, date, time and fare: SR-MLG-20260712-0830-B.
# The named groups let the validator cross-check the ID against the row's own fields.
OFFER_ID_PATTERN = re.compile(
    r"^(?P<company>[A-Z]{2})-(?P<destination>[A-Z]{3})-(?P<date>\d{8})-(?P<time>\d{4})-(?P<fare>[A-Z])$"
)
COMPANY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
DESTINATION_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")

DATETIME_FORMAT = "%Y-%m-%d %H:%M"
RATING_MIN, RATING_MAX = 1.0, 5.0
MAX_PRICE_EUR = 500.0
MAX_TRANSFERS = 3
MAX_DURATION_MIN = 24 * 60
MAX_SEATS = 80
MAX_LUGGAGE_KG = 50
MAX_CO2_KG = 1000.0
DURATION_TOLERANCE_MIN = 1

_TRUE = {"true", "yes", "1"}
_FALSE = {"false", "no", "0"}
_EXTRA_FIELDS = "__extra__"


@dataclass
class RejectedRecord:
    line: int
    row: dict[str, str]
    reasons: list[str]


@dataclass
class Correction:
    line: int
    offer_id: str
    field: str
    old: str
    new: str
    reason: str


@dataclass
class ValidationReport:
    source: Path
    rows_read: int = 0
    accepted: int = 0
    sold_out: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)


@dataclass
class Catalogue:
    """Validated reference data and offers, plus the report describing what was rejected."""

    companies: dict[str, Company]
    destinations: dict[str, Destination]
    offers: list[Offer]
    report: ValidationReport

    def __post_init__(self) -> None:
        self._by_id = {offer.offer_id: offer for offer in self.offers}
        self._by_name = {normalise_name(d.name): d for d in self.destinations.values()}

    def offer(self, offer_id: str) -> Offer | None:
        return self._by_id.get(offer_id.strip().upper())

    def find_destination(self, text: str) -> Destination | None:
        """Look a destination up by name (any case/accents) or by its three-letter code."""
        return self._by_name.get(normalise_name(text)) or self.destinations.get(text.strip().upper())

    def coverage(self) -> int:
        """Number of distinct company-destination combinations among accepted offers."""
        return len({(o.company.code, o.destination.code) for o in self.offers})


def normalise_name(text: str) -> str:
    """'  Cádiz ' -> 'cadiz': strip accents, case and surplus whitespace."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


# ---------------------------------------------------------------- reading CSV files

def _read_csv(path: Path, required: Sequence[str]) -> list[tuple[int, dict[str, str]]]:
    """Return (line number, stripped row) pairs; structural problems raise DataFileError."""
    if not path.exists():
        raise DataFileError(f"Data file not found: {path}")
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise DataFileError(f"{path.name} is empty")
            header = [name.strip() for name in reader.fieldnames]
            missing = [column for column in required if column not in header]
            if missing:
                raise DataFileError(f"{path.name} is missing column(s): {', '.join(missing)}")
            rows = []
            for row in reader:
                extra = row.pop(None, None)
                clean = {key.strip(): (value or "").strip() for key, value in row.items()}
                if extra:
                    clean[_EXTRA_FIELDS] = "yes"
                rows.append((reader.line_num, clean))
            return rows
    except UnicodeDecodeError as exc:
        raise DataFileError(f"{path.name} is not valid UTF-8 text") from exc
    except OSError as exc:
        raise DataFileError(f"Could not read {path}: {exc}") from exc


# ---------------------------------------------------------------- field parsers

def _text(row: dict[str, str], name: str) -> str:
    value = row.get(name, "")
    if not value:
        raise RecordValidationError(name, "missing value")
    return value


def _check_range(name: str, value: float, low: float | None, high: float | None,
                 exclusive_low: bool = False) -> None:
    if low is not None and (value <= low if exclusive_low else value < low):
        bound = "greater than" if exclusive_low else "at least"
        raise RecordValidationError(name, f"must be {bound} {low:g} (got {value:g})")
    if high is not None and value > high:
        raise RecordValidationError(name, f"must be at most {high:g} (got {value:g})")


def _integer(row: dict[str, str], name: str, low: int | None = None, high: int | None = None) -> int:
    text = _text(row, name)
    try:
        value = int(text)
    except ValueError:
        raise RecordValidationError(name, f"'{text}' is not a whole number") from None
    _check_range(name, value, low, high)
    return value


def _decimal(row: dict[str, str], name: str, low: float | None = None, high: float | None = None,
             *, optional: bool = False, exclusive_low: bool = False) -> float | None:
    text = row.get(name, "")
    if not text:
        if optional:
            return None
        raise RecordValidationError(name, "missing value")
    try:
        value = float(text)
    except ValueError:
        raise RecordValidationError(name, f"'{text}' is not a number") from None
    if not math.isfinite(value):
        raise RecordValidationError(name, f"'{text}' is not a finite number")
    _check_range(name, value, low, high, exclusive_low)
    return value


def _boolean(row: dict[str, str], name: str) -> bool:
    text = _text(row, name).lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise RecordValidationError(name, f"'{text}' is not true/false")


def _datetime(row: dict[str, str], name: str) -> datetime:
    text = _text(row, name)
    try:
        return datetime.strptime(text, DATETIME_FORMAT)
    except ValueError:
        raise RecordValidationError(name, f"'{text}' is not a valid date and time (YYYY-MM-DD HH:MM)") from None


# ---------------------------------------------------------------- reference data

def load_companies(path: Path) -> dict[str, Company]:
    companies: dict[str, Company] = {}
    for line, row in _read_csv(path, COMPANY_COLUMNS):
        try:
            code = _text(row, "code").upper()
            if not COMPANY_CODE_PATTERN.match(code):
                raise RecordValidationError("code", f"'{code}' is not a two-letter company code")
            if code in companies:
                raise RecordValidationError("code", f"duplicate company code '{code}'")
            companies[code] = Company(code, _text(row, "name"), row.get("profile", ""))
        except RecordValidationError as exc:
            raise DataFileError(f"{path.name} line {line}: {exc}") from exc
    if not companies:
        raise DataFileError(f"{path.name} contains no companies")
    return companies


def load_destinations(path: Path) -> dict[str, Destination]:
    destinations: dict[str, Destination] = {}
    seen_names: set[str] = set()
    for line, row in _read_csv(path, DESTINATION_COLUMNS):
        try:
            code = _text(row, "code").upper()
            if not DESTINATION_CODE_PATTERN.match(code):
                raise RecordValidationError("code", f"'{code}' is not a three-letter destination code")
            name = _text(row, "name")
            if code in destinations or normalise_name(name) in seen_names:
                raise RecordValidationError("code", f"duplicate destination '{code}' / '{name}'")
            destinations[code] = Destination(
                code=code,
                name=name,
                region=row.get("region", ""),
                coastal=_boolean(row, "coastal"),
                distance_km=_integer(row, "distance_km", 1, 3000),
            )
            seen_names.add(normalise_name(name))
        except RecordValidationError as exc:
            raise DataFileError(f"{path.name} line {line}: {exc}") from exc
    if not destinations:
        raise DataFileError(f"{path.name} contains no destinations")
    return destinations


# ---------------------------------------------------------------- offers

def _validate_offer(line: int, row: dict[str, str], companies: dict[str, Company],
                    destination_lookup: dict[str, Destination], seen_ids: dict[str, int],
                    report: ValidationReport) -> Offer | None:
    errors: list[str] = []
    corrections: list[Correction] = []

    def attempt(parse: Callable, *args, **kwargs):
        try:
            return parse(row, *args, **kwargs)
        except RecordValidationError as exc:
            errors.append(str(exc))
            return None

    if row.get(_EXTRA_FIELDS):
        errors.append("row has more fields than the header")

    offer_id = attempt(_text, "offer_id")
    id_parts = None
    if offer_id is not None:
        id_parts = OFFER_ID_PATTERN.match(offer_id)
        if id_parts is None:
            errors.append(f"offer_id: '{offer_id}' does not match the pattern XX-XXX-YYYYMMDD-HHMM-F")
        elif offer_id in seen_ids:
            errors.append(f"offer_id: duplicate of the offer on line {seen_ids[offer_id]}")

    company = None
    company_code = attempt(_text, "company_code")
    if company_code is not None:
        company = companies.get(company_code.upper())
        if company is None:
            errors.append(f"company_code: unknown company '{company_code}'")

    destination = None
    destination_text = attempt(_text, "destination")
    if destination_text is not None:
        destination = destination_lookup.get(normalise_name(destination_text))
        if destination is None:
            errors.append(f"destination: unknown destination '{destination_text}'")
        elif destination_text != destination.name:
            corrections.append(Correction(line, offer_id or "?", "destination", destination_text,
                                          destination.name, "normalised case/accents/whitespace"))

    departure = attempt(_datetime, "departure")
    arrival = attempt(_datetime, "arrival")
    price = attempt(_decimal, "price_eur", 0, MAX_PRICE_EUR, exclusive_low=True)
    transfers = attempt(_integer, "transfers", 0, MAX_TRANSFERS)
    fare_name = attempt(_text, "fare_name")
    changeable = attempt(_boolean, "changeable")
    refundable = attempt(_boolean, "refundable")
    change_fee = attempt(_decimal, "change_fee_eur", 0, MAX_PRICE_EUR)
    luggage = attempt(_integer, "luggage_kg", 0, MAX_LUGGAGE_KG)
    seats = attempt(_integer, "seats_available", 0, MAX_SEATS)
    rating = attempt(_decimal, "rating", RATING_MIN, RATING_MAX, optional=True)
    co2 = attempt(_decimal, "co2_kg", 0, MAX_CO2_KG, optional=True, exclusive_low=True)

    # Duration is derivable from the timestamps, so a bad duration alone is corrected, not rejected.
    try:
        stated_duration: int | None = _integer(row, "duration_min")
    except RecordValidationError:
        stated_duration = None

    if departure is not None and arrival is not None:
        actual = int((arrival - departure).total_seconds() // 60)
        if actual <= 0:
            errors.append(f"arrival: {arrival:%Y-%m-%d %H:%M} is not after departure {departure:%Y-%m-%d %H:%M}")
        elif actual > MAX_DURATION_MIN:
            errors.append(f"arrival: journey of {actual} min exceeds the {MAX_DURATION_MIN} min plausibility limit")
        elif stated_duration is None or abs(actual - stated_duration) > DURATION_TOLERANCE_MIN:
            corrections.append(Correction(line, offer_id or "?", "duration_min", row.get("duration_min", ""),
                                          str(actual), "recomputed from departure/arrival timestamps"))

    if id_parts and company and destination and departure:
        mismatched = [
            label for label, expected, actual in (
                ("company", id_parts["company"], company.code),
                ("destination", id_parts["destination"], destination.code),
                ("date", id_parts["date"], f"{departure:%Y%m%d}"),
                ("time", id_parts["time"], f"{departure:%H%M}"),
            ) if expected != actual
        ]
        if mismatched:
            errors.append(f"offer_id: identifier disagrees with the {', '.join(mismatched)} field(s)")

    if errors:
        report.rejected.append(RejectedRecord(line, row, errors))
        logger.warning("Rejected offer on line %d (%s): %s", line, offer_id or "no id", "; ".join(errors))
        return None

    for correction in corrections:
        report.corrections.append(correction)
        logger.info("Corrected %s on line %d: %s '%s' -> '%s' (%s)", correction.offer_id, line,
                    correction.field, correction.old, correction.new, correction.reason)
    seen_ids[offer_id] = line
    if seats == 0:
        report.sold_out += 1

    return Offer(
        offer_id=offer_id,
        company=company,
        destination=destination,
        departure=departure,
        arrival=arrival,
        price_eur=price,
        transfers=transfers,
        seats_available=seats,
        fare=FareConditions(fare_name, changeable, refundable, change_fee, luggage),
        rating=rating,
        co2_kg=co2,
    )


def load_offers(path: Path, companies: dict[str, Company],
                destinations: dict[str, Destination]) -> tuple[list[Offer], ValidationReport]:
    report = ValidationReport(source=path)
    lookup = {normalise_name(d.name): d for d in destinations.values()}
    seen_ids: dict[str, int] = {}
    offers = []
    for line, row in _read_csv(path, OFFER_COLUMNS):
        report.rows_read += 1
        offer = _validate_offer(line, row, companies, lookup, seen_ids, report)
        if offer is not None:
            offers.append(offer)
    report.accepted = len(offers)
    logger.info("Loaded %s: %d rows, %d accepted, %d rejected, %d corrected, %d sold out",
                path.name, report.rows_read, report.accepted, len(report.rejected),
                len(report.corrections), report.sold_out)
    return offers, report


def write_quarantine(report: ValidationReport, path: Path) -> None:
    """Write rejected rows, with their reasons, to a CSV for manual review."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=("line", *OFFER_COLUMNS, "reasons"),
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for record in report.rejected:
            writer.writerow({**record.row, "line": record.line, "reasons": " | ".join(record.reasons)})


def load_catalogue(data_dir: Path, quarantine_path: Path | None = None) -> Catalogue:
    companies = load_companies(data_dir / "companies.csv")
    destinations = load_destinations(data_dir / "destinations.csv")
    offers, report = load_offers(data_dir / "offers.csv", companies, destinations)
    if quarantine_path is not None:
        write_quarantine(report, quarantine_path)
    return Catalogue(companies, destinations, offers, report)
