"""Plain-text rendering of results for the terminal. Pure functions: data in, lines out."""

from __future__ import annotations

from typing import Mapping, Sequence

from busbidder.criteria import Criterion, format_minutes
from busbidder.loader import Catalogue
from busbidder.models import Booking, BookingStatus, Offer
from busbidder.profiles import TravellerProfile
from busbidder.scoring import RankingResult
from busbidder.sensitivity import Scenario


def table(headers: Sequence[str], rows: Sequence[Sequence[object]], right: Sequence[int] = ()) -> list[str]:
    """Align columns; column indexes in `right` are right-aligned (numbers)."""
    cells = [[str(c) for c in row] for row in rows]
    widths = [max([len(h)] + [len(row[i]) for row in cells]) for i, h in enumerate(headers)]

    def line(values: Sequence[str]) -> str:
        return "  ".join(v.rjust(w) if i in right else v.ljust(w)
                         for i, (v, w) in enumerate(zip(values, widths))).rstrip()

    return [line(headers), line(["-" * w for w in widths])] + [line(row) for row in cells]


def heading(text: str) -> list[str]:
    return ["", text, "=" * len(text)]


def arrival_text(offer: Offer) -> str:
    days = (offer.arrival.date() - offer.departure.date()).days
    return f"{offer.arrival:%H:%M}" + (f"+{days}" if days else "")


def render_request(result: RankingResult, profile: TravellerProfile | None = None) -> list[str]:
    """Show hard constraints and soft preferences separately, so the user sees which is which."""
    request = result.request
    lines = []
    if profile:
        lines.append(f"Profile: {profile.name}. {profile.description}")
    lines.append(f"Trip: Madrid -> {request.destination.name}, {request.date_from:%d %b} to "
                 f"{request.date_to:%d %b %Y}, {request.passengers} passenger(s)")
    lines.append("Hard constraints (offers that break any of these are removed before ranking):")
    for _, description in result.filter_result.active:
        lines.append(f"  * {description}")
    lines.append("Soft preferences (scored and weighted, never used to remove offers):")
    departure = request.preferred_departure
    lines.append(f"  * preferred departure: {departure if departure else 'none'}")
    lines.append(f"  * weights: {result.weights.describe()}  [normalisation: {result.normalisation}]")
    return lines


def render_filter_summary(result: RankingResult) -> list[str]:
    f = result.filter_result
    lines = [f"Searched {f.total} offers: {f.in_scope} match the route and dates, "
             f"{len(f.accepted)} meet every hard constraint."]
    if f.violation_counts:
        labels = {c.key: c.label for c, _ in f.active}
        parts = [f"{labels[key].lower()} {count}" for key, count in f.violation_counts.most_common()]
        lines.append(f"Removed by: {', '.join(parts)} (an offer can break several).")
    return lines


def render_ranking(result: RankingResult, criteria: Mapping[str, Criterion], top: int = 10) -> list[str]:
    if not result.ranked:
        return ["No acceptable offers."]
    active = result.weights.active()
    headers = ["#", "Offer ID", "Company", "Date", "Dep", "Arr", "Length", "EUR", "Tr", "Fare", "Seats", "|"]
    headers += [criteria[key].header for key in active] + ["Total"]
    first_score_column = 12
    rows = []
    shown = result.ranked if top <= 0 else result.ranked[:top]
    for r in shown:
        o = r.offer
        row = [
            r.rank, o.offer_id, o.company.name, f"{o.departure:%d %b}", f"{o.departure:%H:%M}", arrival_text(o),
            format_minutes(o.duration_min), f"{o.price_eur:.2f}", o.transfers, f"{o.fare.name} {o.fare.flags}",
            o.seats_available, "|",
        ]
        row += [f"{r.scores[k].score:.1f}" + ("*" if r.scores[k].missing else "") for k in active]
        row.append(f"{r.total:.2f}" + ("=" if r.tied else ""))
        rows.append(row)
    right = {0, 7, 8, 10} | set(range(first_score_column, first_score_column + len(active) + 1))
    lines = table(headers, rows, right)
    legend = ", ".join(f"{criteria[k].header} = {criteria[k].label.lower()} ({result.weights[k]:.0%})" for k in active)
    lines.append(f"Scores are 0-100. {legend}. Total = weighted sum (0-100).")
    lines.append("Fare: C = changeable, R = refundable.  * = missing data, neutral score used.  = after a total means a tie.")
    if len(shown) < len(result.ranked):
        lines.append(f"Showing {len(shown)} of {len(result.ranked)} acceptable offers (use --top 0 to show all).")
    return lines


def render_excluded(result: RankingResult, limit: int = 15) -> list[str]:
    excluded = result.filter_result.excluded
    if not excluded:
        return []
    rows = [[o.offer_id, o.company.name, f"{o.price_eur:.2f}", "; ".join(reasons)] for o, reasons in excluded[:limit]]
    lines = [f"Excluded by hard constraints ({len(excluded)}, first {min(limit, len(excluded))} shown):"]
    return lines + table(["Offer ID", "Company", "EUR", "Reasons"], rows, right={2})


def render_warnings(result: RankingResult) -> list[str]:
    return [f"Note: {w}" for w in result.warnings]


def render_comparison(results: Sequence[tuple[TravellerProfile, RankingResult]]) -> list[str]:
    rows = []
    for profile, result in results:
        top = result.top
        if top is None:
            rows.append([profile.name, len(result.ranked), "-", "-", "-", "-", "-", "-", "-"])
            continue
        o = top.offer
        rows.append([profile.name, len(result.ranked), o.offer_id, o.company.name, f"{o.price_eur:.2f}",
                     format_minutes(o.duration_min), f"{o.departure:%a %H:%M}", f"{o.fare.name} {o.fare.flags}",
                     f"{top.total:.2f}"])
    return table(["Profile", "Acceptable", "Recommended offer", "Company", "EUR", "Length", "Departs", "Fare",
                  "Score"], rows, right={1, 4, 8})


def render_sensitivity(baseline: RankingResult, scenarios: Sequence[Scenario],
                       criteria: Mapping[str, Criterion]) -> list[str]:
    if baseline.top is None:
        return ["No acceptable offers, so there is nothing to test."]
    base = baseline.top.offer
    lines = [f"Baseline winner: {base.offer_id} ({base.company.name}), score {baseline.top.total:.2f}",
             f"Baseline weights: {baseline.weights.describe()}", ""]
    rows = []
    for s in scenarios:
        winner = s.top.offer if s.top else None
        rows.append([criteria[s.criterion].label, f"{s.delta * 100:+.0f} pts", s.weights.describe(),
                     f"{winner.offer_id} ({winner.company.name})" if winner else "-",
                     "CHANGED" if s.changed else "same"])
    lines += table(["Criterion", "Shift", "Weights tested", "Winner", "Result"], rows)
    stable = sum(1 for s in scenarios if not s.changed)
    lines.append("")
    lines.append(f"The winner is unchanged in {stable} of {len(scenarios)} scenarios.")
    sensitive = sorted({criteria[s.criterion].label.lower() for s in scenarios if s.changed})
    if sensitive:
        lines.append(f"The recommendation is sensitive to the weight on: {', '.join(sensitive)}.")
    else:
        lines.append("The recommendation is robust to +/-10 point changes in any single weight.")
    return lines


def render_booking(booking: Booking) -> list[str]:
    lines = [
        f"Booking reference : {booking.reference}",
        f"Status            : {booking.status.value} (payment {booking.payment_status})",
        f"Passenger         : {booking.passenger_name} (x{booking.passengers})",
        f"Journey           : Madrid -> {booking.destination} with {booking.company}",
        f"Departs / arrives : {booking.departure:%a %d %b %Y %H:%M} / {booking.arrival:%a %d %b %H:%M}",
        f"Offer             : {booking.offer_id}",
        f"Fare conditions   : {booking.fare.name}: {booking.fare.summary}, luggage {booking.fare.luggage_kg} kg",
        f"Total price       : EUR {booking.total_price_eur:.2f} (EUR {booking.unit_price_eur:.2f} per ticket)",
        f"Booked at         : {booking.created_at:%Y-%m-%d %H:%M}" + (f" via profile '{booking.profile}'" if booking.profile else ""),
    ]
    if booking.status is BookingStatus.CANCELLED:
        lines.append(f"Cancelled at      : {booking.cancelled_at:%Y-%m-%d %H:%M}, refund EUR {booking.refund_eur:.2f}")
    return lines


def render_bookings(bookings: Sequence[Booking]) -> list[str]:
    if not bookings:
        return ["No bookings yet."]
    rows = [[b.reference, b.status.value, b.passenger_name, b.passengers, b.offer_id,
             f"{b.departure:%d %b %H:%M}", f"{b.total_price_eur:.2f}", f"{b.refund_eur:.2f}"] for b in bookings]
    return table(["Reference", "Status", "Passenger", "Pax", "Offer", "Departs", "EUR", "Refund"], rows,
                 right={3, 6, 7})


def render_validation(catalogue: Catalogue, quarantine_file: object) -> list[str]:
    r = catalogue.report
    combos = len(catalogue.companies) * len(catalogue.destinations)
    lines = [
        f"Companies: {len(catalogue.companies)}   Destinations: {len(catalogue.destinations)}   "
        f"Company-destination combinations covered: {catalogue.coverage()}/{combos}",
        f"Offer rows read : {r.rows_read}",
        f"Accepted        : {r.accepted}",
        f"Rejected        : {len(r.rejected)}  (set aside for review in {quarantine_file})",
        f"Corrected       : {len(r.corrections)}",
        f"Sold out        : {r.sold_out}  (kept as valid data; removed by the seat-availability constraint)",
    ]
    if r.rejected:
        lines.append("")
        lines.append("Rejected rows:")
        for record in r.rejected:
            lines.append(f"  line {record.line:>5}  {record.row.get('offer_id') or '(no id)'}: {'; '.join(record.reasons)}")
    if r.corrections:
        lines.append("")
        lines.append("Corrections:")
        for c in r.corrections:
            lines.append(f"  line {c.line:>5}  {c.offer_id}: {c.field} '{c.old}' -> '{c.new}' ({c.reason})")
    return lines


def render_profiles(profiles: Mapping[str, TravellerProfile]) -> list[str]:
    lines = []
    for key, p in profiles.items():
        constraints = ", ".join(f"{k}={v}" for k, v in p.constraints.items()) or "none"
        lines += [f"{key}: {p.name}", f"    {p.description}",
                  f"    hard constraints: {constraints}",
                  f"    preferred departure: {p.preferred_departure or 'none'}",
                  f"    weights: {p.weights.describe()}"]
    return lines
