"""End-to-end runs of the command-line interface, and settings validation."""

import json

import pytest

from busbidder.cli import main, parse_weights
from busbidder.config import PROJECT_ROOT, load_settings
from busbidder.exceptions import ConfigError, InvalidWeightsError


@pytest.fixture
def config(tmp_path):
    """A settings file that uses the real data but writes output and logs to tmp_path."""
    settings = json.loads((PROJECT_ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    settings.update({
        "data_dir": str(PROJECT_ROOT / "data"),
        "profiles_file": str(PROJECT_ROOT / "config" / "profiles.json"),
        "output_dir": str(tmp_path / "output"),
        "log_file": str(tmp_path / "logs" / "busbidder.log"),
    })
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    return path


def run(config, *args):
    return main(["--config", str(config), *args])


def test_rank_shows_constraints_preferences_scores_and_explanation(config, capsys):
    assert run(config, "rank", "--top", "5") == 0
    output = capsys.readouterr().out
    assert "Hard constraints" in output and "Soft preferences" in output
    assert "price <= EUR 50.00 per ticket" in output
    assert "Total" in output and "ranks first" in output
    assert "not an objectively best ticket" in output


def test_rank_with_overrides(config, capsys):
    assert run(config, "rank", "-d", "barcelona", "--weights", "price=25,duration=25,departure=25,flexibility=25",
               "--departure", "evening", "--max-price", "45", "--show-excluded") == 0
    output = capsys.readouterr().out
    assert "Madrid -> Barcelona" in output and "evening window" in output
    assert "Excluded by hard constraints" in output


def test_compare_lists_every_profile(config, capsys):
    assert run(config, "compare") == 0
    output = capsys.readouterr().out
    for name in ("Budget traveller", "Business traveller", "Flexibility-focused tourist"):
        assert name in output


def test_validate_reports_rejected_and_corrected_rows(config, capsys, tmp_path):
    assert run(config, "validate") == 0
    output = capsys.readouterr().out
    assert "Rejected        : 13" in output and "Corrected       : 3" in output
    assert (tmp_path / "output" / "rejected_offers.csv").exists()


def test_book_list_and_cancel_round_trip(config, capsys, tmp_path):
    assert run(config, "book", "--offer", "CL-MLG-20260712-1100-F", "--name", "Ana Lopez") == 0
    output = capsys.readouterr().out
    reference = next(line.split(":")[1].strip() for line in output.splitlines() if line.startswith("Booking reference"))
    assert "Seats left on this offer: 20" in output
    assert run(config, "bookings") == 0
    assert reference in capsys.readouterr().out
    assert run(config, "cancel", reference) == 0
    assert "CANCELLED" in capsys.readouterr().out
    log = (tmp_path / "logs" / "busbidder.log").read_text(encoding="utf-8")
    assert f"Booked {reference}" in log and f"Cancelled {reference}" in log


@pytest.mark.parametrize("args, message", [
    (("rank", "-d", "Lisbon"), "Unknown destination"),
    (("rank", "--weights", "price=50,duration=50"), "core criteria"),
    (("rank", "--profile", "astronaut"), "Unknown profile"),
    (("rank", "--from", "2026-07-14", "--to", "2026-07-12"), "ends"),
    (("book", "--offer", "NOPE", "--name", "Ana"), "Unknown offer"),
    (("cancel", "BB-260701-ABCDEF"), "No booking"),
])
def test_user_errors_exit_with_status_2_and_a_message(config, capsys, args, message):
    assert run(config, *args) == 2
    assert message in capsys.readouterr().err


def test_missing_config_file_is_an_error(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.json"), "validate"]) == 2
    assert "Config file not found" in capsys.readouterr().err


def test_parse_weights():
    weights = parse_weights("price=40, duration:25, departure=15%, flexibility=20")
    assert weights["duration"] == pytest.approx(0.25)
    for bad in ("price 40", "price=40,price=60"):
        with pytest.raises(InvalidWeightsError):
            parse_weights(bad)


@pytest.mark.parametrize("content, message", [
    ("{not json", "not valid JSON"),
    ('{"colour": 1}', "Unknown setting"),
    ('{"departure_tolerance_min": 0}', "between"),
    ('{"missing_value_score": 150}', "between"),
    ('{"departure_windows": {"morning": ["6am", "12:00"]}}', "not a valid HH:MM"),
])
def test_invalid_settings(tmp_path, content, message):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_settings(path)
