"""Command-line interface: scriptable subcommands plus an interactive menu.

    python -m busbidder                 interactive menu
    python -m busbidder rank ...        rank offers for one profile
    python -m busbidder compare ...     same trip, every profile side by side
    python -m busbidder --help          all commands

Subcommands make every result in the report reproducible with one command. The menu is
for a traveller exploring the offers and making a booking.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import replace
from datetime import date
from typing import Callable, Sequence, TypeVar

from busbidder import display
from busbidder.booking import validate_passenger_name
from busbidder.config import PROJECT_ROOT, load_settings
from busbidder.exceptions import BusBidderError, InvalidRequestError, InvalidWeightsError
from busbidder.explain import explain_recommendation
from busbidder.logging_setup import configure_logging
from busbidder.marketplace import Marketplace
from busbidder.profiles import TravellerProfile
from busbidder.request import TravelRequest, parse_departure_preference
from busbidder.scoring import RankingResult, Weights

logger = logging.getLogger(__name__)

DEFAULT_DESTINATION = "Malaga"
DEFAULT_FROM = date(2026, 7, 12)
DEFAULT_TO = date(2026, 7, 14)
DEFAULT_PROFILE = "example"
WEIGHT_ITEM = re.compile(r"^\s*(?P<key>[a-z_]+)\s*[=:]\s*(?P<value>\d+(?:\.\d+)?)\s*%?\s*$")

T = TypeVar("T")


def out(lines: Sequence[str]) -> None:
    print("\n".join(lines))


def parse_weights(text: str) -> Weights:
    """'price=40, duration=25, departure=15, flexibility=20' -> Weights."""
    raw: dict[str, float] = {}
    for item in filter(str.strip, text.split(",")):
        match = WEIGHT_ITEM.match(item.lower())
        if not match:
            raise InvalidWeightsError(f"'{item.strip()}' is not in the form criterion=number")
        if match["key"] in raw:
            raise InvalidWeightsError(f"'{match['key']}' is given twice")
        raw[match["key"]] = float(match["value"])
    return Weights(raw)


def parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise InvalidRequestError(f"'{text}' is not a date in the form YYYY-MM-DD") from None


def _arg_date(text: str) -> date:
    try:
        return parse_date(text)
    except InvalidRequestError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


# ---------------------------------------------------------------- shared views

def show_ranking(market: Marketplace, request: TravelRequest, weights: Weights,
                 profile: TravellerProfile | None, top: int = 10, show_excluded: bool = False) -> RankingResult:
    result = market.rank(request, weights)
    title = f"Ranking for {profile.name}" if profile else "Ranking"
    out(display.heading(title))
    out(display.render_request(result, profile))
    print()
    out(display.render_filter_summary(result))
    print()
    out(display.render_ranking(result, market.ranker.criteria, top))
    if result.warnings:
        print()
        out(display.render_warnings(result))
    explanation = explain_recommendation(result, market.ranker.criteria, profile.name if profile else None)
    if explanation:
        print()
        out(explanation)
    if show_excluded:
        print()
        out(display.render_excluded(result))
    return result


def show_comparison(market: Marketplace, destination: str, date_from: date, date_to: date, passengers: int,
                    profile_keys: Sequence[str], top: int = 3) -> None:
    profiles = [market.profile(key) for key in profile_keys]
    results = []
    for profile in profiles:
        request = market.build_request(profile, destination, date_from, date_to, passengers)
        results.append((profile, market.rank(request, profile.weights)))
    request = results[0][1].request
    out(display.heading(f"Profile comparison: Madrid -> {request.destination.name}, "
                        f"{date_from:%d %b} to {date_to:%d %b %Y}"))
    print(f"Every profile ranks the same {results[0][1].filter_result.in_scope} offers on this route and dates.\n")
    out(display.render_comparison(results))
    for profile, result in results:
        out(display.heading(profile.name))
        out(display.render_request(result, profile))
        print()
        out(display.render_filter_summary(result))
        out(display.render_ranking(result, market.ranker.criteria, top))
        if result.warnings:
            out(display.render_warnings(result))
        explanation = explain_recommendation(result, market.ranker.criteria, profile.name)
        if explanation:
            print()
            out(explanation)


def show_sensitivity(market: Marketplace, request: TravelRequest, weights: Weights,
                     profile: TravellerProfile | None, step: float) -> None:
    baseline, scenarios = market.sensitivity(request, weights, step)
    who = f" for {profile.name}" if profile else ""
    out(display.heading(f"Weight sensitivity{who}: Madrid -> {request.destination.name}"))
    out(display.render_sensitivity(baseline, scenarios, market.ranker.criteria))


# ---------------------------------------------------------------- subcommands

def _request_from_args(market: Marketplace, args: argparse.Namespace) -> tuple[TravelRequest, Weights, TravellerProfile]:
    profile = market.profile(args.profile)
    request = market.build_request(profile, args.destination, args.date_from, args.date_to, args.passengers)
    overrides: dict[str, object] = {}
    if args.max_price is not None:
        overrides["max_price_eur"] = args.max_price
    if args.max_transfers is not None:
        overrides["max_transfers"] = args.max_transfers
    if args.max_duration is not None:
        overrides["max_duration_min"] = args.max_duration
    if args.min_luggage is not None:
        overrides["min_luggage_kg"] = args.min_luggage
    if args.require_changeable:
        overrides["require_changeable"] = True
    if args.require_refundable:
        overrides["require_refundable"] = True
    if args.departure is not None:
        overrides["preferred_departure"] = parse_departure_preference(args.departure, market.settings.departure_windows)
    if overrides:
        request = replace(request, **overrides)  # replace() re-runs TravelRequest validation
    weights = parse_weights(args.weights) if args.weights else profile.weights
    return request, weights, profile


def cmd_rank(market: Marketplace, args: argparse.Namespace) -> int:
    request, weights, profile = _request_from_args(market, args)
    show_ranking(market, request, weights, profile, args.top, args.show_excluded)
    return 0


def cmd_compare(market: Marketplace, args: argparse.Namespace) -> int:
    keys = [k.strip() for k in args.profiles.split(",")] if args.profiles else list(market.profiles)
    show_comparison(market, args.destination, args.date_from, args.date_to, args.passengers, keys, args.top)
    return 0


def cmd_sensitivity(market: Marketplace, args: argparse.Namespace) -> int:
    request, weights, profile = _request_from_args(market, args)
    show_sensitivity(market, request, weights, profile, args.step / 100)
    return 0


def cmd_book(market: Marketplace, args: argparse.Namespace) -> int:
    booking = market.book(args.offer, args.name, args.passengers, args.profile)
    out(display.heading("Booking confirmed"))
    out(display.render_booking(booking))
    print(f"\nSeats left on this offer: {market.remaining_seats(booking.offer_id)}")
    return 0


def cmd_bookings(market: Marketplace, args: argparse.Namespace) -> int:
    out(display.render_bookings(market.all_bookings()))
    return 0


def cmd_show(market: Marketplace, args: argparse.Namespace) -> int:
    out(display.render_booking(market.bookings.get(args.reference)))
    return 0


def cmd_cancel(market: Marketplace, args: argparse.Namespace) -> int:
    booking = market.bookings.cancel(args.reference)
    out(display.heading("Booking cancelled"))
    out(display.render_booking(booking))
    return 0


def cmd_validate(market: Marketplace, args: argparse.Namespace) -> int:
    out(display.heading(f"Data validation report ({market.catalogue.report.source.name})"))
    quarantine = market.settings.quarantine_file
    if quarantine.is_relative_to(PROJECT_ROOT):
        quarantine = quarantine.relative_to(PROJECT_ROOT)
    out(display.render_validation(market.catalogue, quarantine))
    return 0


def cmd_profiles(market: Marketplace, args: argparse.Namespace) -> int:
    out(display.render_profiles(market.profiles))
    return 0


def cmd_menu(market: Marketplace, args: argparse.Namespace) -> int:
    return InteractiveMenu(market).run()


# ---------------------------------------------------------------- interactive menu

class InteractiveMenu:
    MENU = [
        ("1", "Search, rank and book"),
        ("2", "Compare traveller profiles on one trip"),
        ("3", "Weight sensitivity check"),
        ("4", "List bookings"),
        ("5", "Cancel a booking"),
        ("6", "Data validation report"),
        ("7", "Show traveller profiles"),
        ("0", "Quit"),
    ]

    def __init__(self, market: Marketplace):
        self.market = market

    def ask(self, prompt: str, default: object = None, parse: Callable[[str], T] = str) -> T:
        """Prompt until `parse` accepts the answer; Enter takes the default."""
        while True:
            suffix = f" [{default}]" if default is not None else ""
            text = input(f"{prompt}{suffix}: ").strip()
            if not text and default is not None:
                text = str(default)
            try:
                return parse(text)
            except (BusBidderError, ValueError) as exc:
                print(f"  {exc}")

    def yes(self, prompt: str, default: bool = False) -> bool:
        answer = input(f"{prompt} ({'Y/n' if default else 'y/N'}): ").strip().lower()
        return default if not answer else answer.startswith("y")

    def run(self) -> int:
        print("BusBidder Spain: bus offers from Madrid, ranked for you.")
        while True:
            print()
            for key, label in self.MENU:
                print(f"  {key}) {label}")
            choice = input("Choose an option: ").strip()
            actions = {"1": self.search, "2": self.compare, "3": self.sensitivity, "4": self.list_bookings,
                       "5": self.cancel, "6": self.validate, "7": self.profiles}
            if choice in ("0", "q", "quit"):
                return 0
            action = actions.get(choice)
            if action is None:
                print("Please choose a number from the menu.")
                continue
            try:
                action()
            except BusBidderError as exc:
                logger.error("%s", exc)
                print(f"Error: {exc}")

    # ------------------------------------------------------------ prompts

    def _trip(self) -> tuple[str, date, date, int]:
        names = ", ".join(d.name for d in self.market.catalogue.destinations.values())
        print(f"Destinations: {names}")
        destination = self.ask("Destination", DEFAULT_DESTINATION, lambda t: self.market.destination(t).name)
        date_from = self.ask("Travel window from (YYYY-MM-DD)", DEFAULT_FROM, parse_date)
        date_to = self.ask("Travel window to (YYYY-MM-DD)", max(DEFAULT_TO, date_from), parse_date)
        if date_to < date_from:
            raise InvalidRequestError("The travel window ends before it starts")
        passengers = self.ask("Passengers", 1, int)
        return destination, date_from, date_to, passengers

    def _profile(self) -> TravellerProfile:
        keys = list(self.market.profiles)
        for number, key in enumerate(keys, start=1):
            profile = self.market.profiles[key]
            print(f"  {number}) {profile.name}: {profile.description}")

        def pick(text: str) -> TravellerProfile:
            if text.isdigit() and 1 <= int(text) <= len(keys):
                return self.market.profiles[keys[int(text) - 1]]
            return self.market.profile(text)

        return self.ask("Traveller profile (number or name)", 1, pick)

    def _optional(self, prompt: str, current: object, parse: Callable[[str], T]) -> T | None:
        shown = "-" if current is None else current
        return self.ask(f"{prompt} ('-' = no limit)", shown, lambda t: None if t == "-" else parse(t))

    def _adjust(self, request: TravelRequest, weights: Weights) -> tuple[TravelRequest, Weights]:
        print("Hard constraints:")
        max_price = self._optional("  Max price per ticket (EUR)", request.max_price_eur, float)
        max_transfers = self._optional("  Max transfers", request.max_transfers, int)
        max_duration = self._optional("  Max journey (minutes)", request.max_duration_min, int)
        min_luggage = self._optional("  Min luggage allowance (kg)", request.min_luggage_kg, int)
        changeable = self.yes("  Require changeable ticket?", request.require_changeable)
        refundable = self.yes("  Require refundable ticket?", request.require_refundable)
        print("Soft preferences:")
        windows = self.market.settings.departure_windows
        current = request.preferred_departure
        if current is None:
            shown = "any"
        elif current.label in windows:
            shown = current.label
        else:
            shown = f"{current.start:%H:%M}-{current.end:%H:%M}"
        departure = self.ask(f"  Preferred departure ({'/'.join(windows)}/HH:MM-HH:MM/any)", shown,
                             lambda t: parse_departure_preference(t, windows))
        current_weights = ", ".join(f"{k}={v * 100:g}" for k, v in weights.items())
        new_weights = self.ask("  Weights in % (must total 100)", current_weights, parse_weights)
        request = replace(request, max_price_eur=max_price, max_transfers=max_transfers,
                          max_duration_min=max_duration, min_luggage_kg=min_luggage, require_changeable=changeable,
                          require_refundable=refundable, preferred_departure=departure)
        return request, new_weights

    # ------------------------------------------------------------ actions

    def search(self) -> None:
        destination, date_from, date_to, passengers = self._trip()
        profile = self._profile()
        request = self.market.build_request(profile, destination, date_from, date_to, passengers)
        weights = profile.weights
        while True:
            result = show_ranking(self.market, request, weights, profile)
            print()
            choice = input("Enter a rank number to book, 'a' to adjust constraints/weights, "
                           "or press Enter to return: ").strip().lower()
            if choice == "a":
                try:
                    request, weights = self._adjust(request, weights)
                except BusBidderError as exc:
                    print(f"  {exc} (keeping the previous settings)")
                    continue
                if not profile.name.endswith("(adjusted)"):
                    profile = replace(profile, name=f"{profile.name} (adjusted)")
                continue
            if choice.isdigit() and 1 <= int(choice) <= len(result.ranked):
                self._book(result.ranked[int(choice) - 1].offer.offer_id, request.passengers, profile.key)
            elif choice:
                print("No such rank.")
                continue
            return

    def _book(self, offer_id: str, passengers: int, profile_key: str) -> None:
        offer = self.market.catalogue.offer(offer_id)
        seats = self.market.remaining_seats(offer_id)
        print(f"{offer_id}: {offer.company.name}, {offer.departure:%a %d %b %H:%M}, "
              f"'{offer.fare.name}' fare ({offer.fare.summary}), {seats} seat(s) left.")
        name = self.ask("Passenger name", None, validate_passenger_name)
        total = offer.price_eur * passengers
        if not self.yes(f"Confirm booking for {passengers} passenger(s), total EUR {total:.2f}? "
                        "No payment details are needed (simulated payment)"):
            print("Booking not made.")
            return
        booking = self.market.book(offer_id, name, passengers, profile_key)
        out(display.heading("Booking confirmed"))
        out(display.render_booking(booking))
        print(f"\nSeats left on this offer: {self.market.remaining_seats(offer_id)}")

    def compare(self) -> None:
        destination, date_from, date_to, passengers = self._trip()
        show_comparison(self.market, destination, date_from, date_to, passengers, list(self.market.profiles))

    def sensitivity(self) -> None:
        destination, date_from, date_to, passengers = self._trip()
        profile = self._profile()
        request = self.market.build_request(profile, destination, date_from, date_to, passengers)
        show_sensitivity(self.market, request, profile.weights, profile, 0.10)

    def list_bookings(self) -> None:
        out(display.render_bookings(self.market.all_bookings()))

    def cancel(self) -> None:
        booking = self.market.bookings.get(input("Booking reference: "))
        out(display.render_booking(booking))
        refund = "refundable, minus the cancellation fee" if booking.fare.refundable else "non-refundable: no refund"
        if self.yes(f"Cancel this booking? ({refund})"):
            out(display.render_booking(self.market.bookings.cancel(booking.reference)))

    def validate(self) -> None:
        cmd_validate(self.market, argparse.Namespace())

    def profiles(self) -> None:
        cmd_profiles(self.market, argparse.Namespace())


# ---------------------------------------------------------------- argument parsing

def _add_search_args(parser: argparse.ArgumentParser, with_profile: bool = True) -> None:
    parser.add_argument("--destination", "-d", default=DEFAULT_DESTINATION, help="name or code (default: Malaga)")
    parser.add_argument("--from", dest="date_from", type=_arg_date, default=DEFAULT_FROM, help="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", type=_arg_date, default=DEFAULT_TO, help="YYYY-MM-DD")
    parser.add_argument("--passengers", "-n", type=int, default=1)
    if not with_profile:
        return
    parser.add_argument("--profile", "-p", default=DEFAULT_PROFILE, help="traveller profile key (see 'profiles')")
    parser.add_argument("--weights", "-w", help="override weights, e.g. price=40,duration=25,departure=15,flexibility=20")
    parser.add_argument("--departure", help="override preference: morning|afternoon|evening|night|HH:MM-HH:MM|any")
    parser.add_argument("--max-price", type=float, help="override budget per ticket (EUR)")
    parser.add_argument("--max-transfers", type=int)
    parser.add_argument("--max-duration", type=int, help="minutes")
    parser.add_argument("--min-luggage", type=int, help="kg")
    parser.add_argument("--require-changeable", action="store_true")
    parser.add_argument("--require-refundable", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="busbidder", description="Filter and rank bus offers from Madrid.")
    parser.add_argument("--config", help="settings file (default: config/settings.json)")
    parser.add_argument("--verbose", "-v", action="store_true", help="also print log messages to the terminal")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("menu", help="interactive menu (the default)").set_defaults(func=cmd_menu)

    rank = sub.add_parser("rank", help="rank offers for one traveller profile")
    _add_search_args(rank)
    rank.add_argument("--top", type=int, default=10, help="rows to show; 0 = all")
    rank.add_argument("--show-excluded", action="store_true", help="list offers removed by hard constraints")
    rank.set_defaults(func=cmd_rank)

    compare = sub.add_parser("compare", help="rank the same trip for several profiles")
    _add_search_args(compare, with_profile=False)
    compare.add_argument("--profiles", help="comma-separated profile keys (default: all)")
    compare.add_argument("--top", type=int, default=3)
    compare.set_defaults(func=cmd_compare)

    sensitivity = sub.add_parser("sensitivity", help="test whether the winner survives weight changes")
    _add_search_args(sensitivity)
    sensitivity.add_argument("--step", type=float, default=10, help="percentage points to shift (default 10)")
    sensitivity.set_defaults(func=cmd_sensitivity)

    book = sub.add_parser("book", help="book an offer by its ID")
    book.add_argument("--offer", required=True)
    book.add_argument("--name", required=True, help="passenger name")
    book.add_argument("--passengers", "-n", type=int, default=1)
    book.add_argument("--profile", help="profile used to find the offer (recorded with the booking)")
    book.set_defaults(func=cmd_book)

    sub.add_parser("bookings", help="list bookings").set_defaults(func=cmd_bookings)
    show = sub.add_parser("show", help="show one booking")
    show.add_argument("reference")
    show.set_defaults(func=cmd_show)
    cancel = sub.add_parser("cancel", help="cancel a booking")
    cancel.add_argument("reference")
    cancel.set_defaults(func=cmd_cancel)

    sub.add_parser("validate", help="data validation report").set_defaults(func=cmd_validate)
    sub.add_parser("profiles", help="list traveller profiles").set_defaults(func=cmd_profiles)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = getattr(args, "func", cmd_menu)
    try:
        settings = load_settings(args.config)
        configure_logging(settings.log_file, args.verbose)
        logger.info("Command: %s", " ".join(sys.argv[1:] if argv is None else argv) or "menu")
        market = Marketplace(settings)
        return handler(market, args)
    except BusBidderError as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
