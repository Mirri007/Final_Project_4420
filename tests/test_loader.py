"""Loading and validating data files: normal, invalid, missing-data and boundary cases."""

import csv

import pytest

from busbidder.config import PROJECT_ROOT
from busbidder.exceptions import DataFileError
from busbidder.loader import load_catalogue, load_companies, normalise_name
from tests.factories import VALID_ROW, make_data_dir


def load_rows(tmp_path, *rows):
    return load_catalogue(make_data_dir(tmp_path, list(rows)), tmp_path / "rejected.csv")


def row(**changes):
    return {**VALID_ROW, **changes}


# ---------------------------------------------------------------- the real dataset

def test_real_dataset_loads_with_expected_counts():
    catalogue = load_catalogue(PROJECT_ROOT / "data")
    report = catalogue.report
    assert len(catalogue.companies) == 5
    assert len(catalogue.destinations) == 15
    assert catalogue.coverage() == 75  # every company-destination combination is offered
    assert report.rows_read == 2004
    assert len(report.rejected) == 13
    assert len(report.corrections) == 3
    assert report.accepted == report.rows_read - len(report.rejected)


def test_valid_row_is_converted_to_typed_offer(tmp_path):
    catalogue = load_rows(tmp_path, row())
    [offer] = catalogue.offers
    assert offer.price_eur == 40.0
    assert offer.duration_min == 360
    assert offer.fare.changeable is True and offer.fare.refundable is False
    assert offer.destination.code == "MLG"
    assert offer.company.name == "Camino Express"


# ---------------------------------------------------------------- rejected rows

@pytest.mark.parametrize("changes, expected", [
    ({"price_eur": "-12.50"}, "price_eur: must be greater than 0"),
    ({"price_eur": "0"}, "price_eur: must be greater than 0"),
    ({"price_eur": ""}, "price_eur: missing value"),
    ({"price_eur": "abc"}, "price_eur: 'abc' is not a number"),
    ({"price_eur": "nan"}, "not a finite number"),
    ({"destination": "Lisbon"}, "unknown destination 'Lisbon'"),
    ({"company_code": "XB"}, "unknown company 'XB'"),
    ({"departure": "2026-07-32 08:00"}, "departure: '2026-07-32 08:00' is not a valid date"),
    ({"arrival": "2026-07-12 07:00"}, "is not after departure"),
    ({"arrival": "2026-07-12 08:00"}, "is not after departure"),
    ({"arrival": "2026-07-14 08:00"}, "exceeds the 1440 min plausibility limit"),
    ({"rating": "7.5"}, "rating: must be at most 5"),
    ({"rating": "0.5"}, "rating: must be at least 1"),
    ({"seats_available": "-3"}, "seats_available: must be at least 0"),
    ({"transfers": "one"}, "transfers: 'one' is not a whole number"),
    ({"transfers": "4"}, "transfers: must be at most 3"),
    ({"changeable": "maybe"}, "changeable: 'maybe' is not true/false"),
    ({"offer_id": "SR_MLG_123"}, "does not match the pattern"),
    ({"offer_id": "CE-VLC-20260712-0800-S"}, "identifier disagrees with the destination"),
    ({"offer_id": "CE-MLG-20260713-0800-S"}, "identifier disagrees with the date"),
    ({"co2_kg": "-1"}, "co2_kg: must be greater than 0"),
])
def test_invalid_rows_are_rejected_with_reason(tmp_path, changes, expected):
    catalogue = load_rows(tmp_path, row(**changes))
    assert catalogue.offers == []
    [rejected] = catalogue.report.rejected
    assert any(expected in reason for reason in rejected.reasons), rejected.reasons


def test_all_errors_in_a_row_are_reported_together(tmp_path):
    catalogue = load_rows(tmp_path, row(price_eur="-1", rating="9", destination="Lisbon"))
    reasons = catalogue.report.rejected[0].reasons
    assert len(reasons) == 3


def test_duplicate_offer_id_keeps_first_and_rejects_second(tmp_path):
    catalogue = load_rows(tmp_path, row(), row(price_eur="1.00"))
    assert [o.price_eur for o in catalogue.offers] == [40.0]
    assert "duplicate of the offer on line 2" in catalogue.report.rejected[0].reasons[0]


def test_rejected_rows_are_written_to_quarantine_with_reasons(tmp_path):
    load_rows(tmp_path, row(), row(offer_id="CE-MLG-20260712-0900-S", departure="2026-07-12 09:00",
                                   arrival="2026-07-12 15:00", price_eur="-5"))
    with (tmp_path / "rejected.csv").open(encoding="utf-8") as fh:
        records = list(csv.DictReader(fh))
    assert len(records) == 1
    assert records[0]["offer_id"] == "CE-MLG-20260712-0900-S"
    assert "price_eur" in records[0]["reasons"]
    assert records[0]["line"] == "3"


# ---------------------------------------------------------------- corrections and missing optional data

@pytest.mark.parametrize("spelling", ["malaga", "  MALAGA ", "Málaga"])
def test_destination_spelling_is_corrected_and_recorded(tmp_path, spelling):
    catalogue = load_rows(tmp_path, row(destination=spelling))
    assert catalogue.offers[0].destination.name == "Malaga"
    [correction] = catalogue.report.corrections
    assert correction.field == "destination" and correction.new == "Malaga"


@pytest.mark.parametrize("stated", ["999", "", "six hours"])
def test_wrong_or_missing_duration_is_recomputed_from_timestamps(tmp_path, stated):
    catalogue = load_rows(tmp_path, row(duration_min=stated))
    assert catalogue.offers[0].duration_min == 360
    assert catalogue.report.corrections[0].field == "duration_min"
    assert catalogue.report.corrections[0].new == "360"


def test_duration_within_tolerance_is_not_corrected(tmp_path):
    catalogue = load_rows(tmp_path, row(duration_min="361"))
    assert catalogue.report.corrections == []


def test_missing_optional_rating_and_co2_are_kept_as_none(tmp_path):
    catalogue = load_rows(tmp_path, row(rating="", co2_kg=""))
    offer = catalogue.offers[0]
    assert offer.rating is None and offer.co2_kg is None


# ---------------------------------------------------------------- boundaries

@pytest.mark.parametrize("changes", [
    {"rating": "1.0"}, {"rating": "5.0"}, {"transfers": "3"}, {"seats_available": "0"},
    {"seats_available": "80"}, {"price_eur": "0.01"}, {"change_fee_eur": "0"},
])
def test_boundary_values_are_accepted(tmp_path, changes):
    catalogue = load_rows(tmp_path, row(**changes))
    assert len(catalogue.offers) == 1, catalogue.report.rejected


def test_sold_out_offer_is_kept_and_counted(tmp_path):
    catalogue = load_rows(tmp_path, row(seats_available="0"))
    assert catalogue.offers[0].seats_available == 0
    assert catalogue.report.sold_out == 1


# ---------------------------------------------------------------- structural / fatal problems

def test_missing_offer_file_raises(tmp_path):
    data_dir = make_data_dir(tmp_path, [])
    (data_dir / "offers.csv").unlink()
    with pytest.raises(DataFileError, match="not found"):
        load_catalogue(data_dir)


def test_missing_column_raises(tmp_path):
    data_dir = make_data_dir(tmp_path, [])
    (data_dir / "offers.csv").write_text("offer_id,company_code\nX,Y\n", encoding="utf-8")
    with pytest.raises(DataFileError, match="missing column"):
        load_catalogue(data_dir)


def test_empty_file_raises(tmp_path):
    data_dir = make_data_dir(tmp_path, [])
    (data_dir / "offers.csv").write_text("", encoding="utf-8")
    with pytest.raises(DataFileError, match="empty"):
        load_catalogue(data_dir)


def test_row_with_extra_fields_is_rejected(tmp_path):
    data_dir = make_data_dir(tmp_path, [row()])
    with (data_dir / "offers.csv").open("a", encoding="utf-8") as fh:
        fh.write(",".join(row(offer_id="CE-MLG-20260712-0900-S").values()) + ",surplus\n")
    catalogue = load_catalogue(data_dir)
    assert "more fields than the header" in catalogue.report.rejected[0].reasons[0]


def test_duplicate_company_code_is_fatal(tmp_path):
    path = tmp_path / "companies.csv"
    path.write_text("code,name,profile\nSR,SolRuta,x\nSR,Other,y\n", encoding="utf-8")
    with pytest.raises(DataFileError, match="duplicate company code"):
        load_companies(path)


def test_normalise_name_ignores_case_accents_and_spacing():
    assert normalise_name("  San   Sebastián ") == normalise_name("san sebastian")
