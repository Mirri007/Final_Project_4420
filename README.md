# BusBidder Spain

A command-line marketplace that filters and ranks competing bus offers from Madrid for an
individual traveller, explains its recommendation, and makes simulated bookings.

Five fictional companies (SolRuta, Camino Express, CostaLink, VerdeBus, NocheVia) offer trips
from Madrid to fifteen destinations. A traveller's request is handled in two stages:

1. **Hard constraints** remove every offer the traveller cannot accept, for example one over budget or with too many transfers.
2. A **transparent weighted model** ranks what is left, and the top offer gets a generated
   explanation of its strengths, its disadvantages and its lead over the runner-up.

## Requirements

| | |
|---|---|
| Python | 3.10 or newer (developed and tested on 3.11.1) |
| Runtime libraries | none; standard library only |
| Test libraries | `pytest` (see `requirements.txt`) |
| Entry point | `busbidder/__main__.py`, run as `python -m busbidder` |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Run all commands from the project root. Inside the virtual environment, `python` and `python3`
are the same interpreter.

## Running the program

```bash
python -m busbidder                 # interactive menu: search, rank, adjust, book, cancel
python -m busbidder --help          # all subcommands
```

The subcommands reproduce every result in the report:

```bash
python -m busbidder rank                                   # brief's example: Madrid -> Malaga, 12-14 Jul, example profile
python -m busbidder rank --profile business --top 0        # one profile, all acceptable offers
python -m busbidder rank -d Barcelona --weights price=25,duration=25,departure=25,flexibility=25 --departure evening
python -m busbidder rank --max-price 35 --require-refundable --show-excluded
python -m busbidder compare --destination Malaga           # same offers, every profile side by side
python -m busbidder sensitivity --profile example          # does the winner survive +/-10 point weight shifts?
python -m busbidder profiles                               # list traveller profiles
python -m busbidder validate                               # data validation report
python -m busbidder book --offer CL-MLG-20260712-1100-F --name "Ana Lopez" --passengers 2
python -m busbidder bookings                               # list bookings
python -m busbidder show BB-261021-9PNLFP                  # one booking (use a reference from your own run)
python -m busbidder cancel BB-261021-9PNLFP
python -m busbidder --config config/settings_minmax.json rank   # same ranking with min-max normalisation
```

Search options: `--destination/-d` (name or code), `--from`/`--to` (YYYY-MM-DD, default
2026-07-12 to 2026-07-14), `--passengers/-n`, `--profile/-p` (default `example`). You can
override any hard constraint (`--max-price`, `--max-transfers`, `--max-duration`,
`--min-luggage`, `--require-changeable`, `--require-refundable`) and either soft preference
(`--departure morning|afternoon|evening|night|HH:MM-HH:MM|any`, `--weights`). Add
`--verbose` to also print log messages to the terminal.

User errors, such as an unknown destination, weights that don't total 100% or a missing file,
print `Error: …` and exit with status 2.

## Running the tests

```bash
python -m pytest            # 187 tests, about 2 seconds
python -m pytest -v         # list every test
```

The tests cover normal, invalid, missing-data and boundary cases for loading, constraints,
scoring, profiles, explanations, sensitivity, booking and the CLI. They write only to
temporary directories.

## Project layout

```
busbidder/            application package (see docs/architecture.md for the diagram)
  cli.py              subcommands and interactive menu
  marketplace.py      facade that wires the parts together
  loader.py           CSV loading, validation, correction, quarantine
  constraints.py      hard constraints (@hard_constraint registry)
  criteria.py         soft criteria (self-registering Criterion subclasses, @normaliser registry)
  scoring.py          Weights, Ranker, RankingResult
  explain.py          generated explanation for the top offer
  sensitivity.py      weight-sensitivity analysis
  profiles.py         traveller profiles from JSON
  booking.py          simulated bookings, references, capacity
  models.py           immutable domain objects
  request.py          TravelRequest, DepartureWindow
  config.py           settings
  display.py          text tables
  logging_setup.py    log file configuration
config/
  settings.json         paths, normalisation method, tolerances, departure windows, fees
  settings_minmax.json  identical except "normalisation": "minmax" (for comparison)
  profiles.json         five traveller profiles
data/
  companies.csv, destinations.csv, offers.csv
scripts/
  generate_data.py    regenerates data/offers.csv (fixed seed, byte-identical)
  make_examples.sh    regenerates examples/
examples/             saved outputs used as evidence in the report
docs/architecture.md  component and pipeline diagrams, module responsibilities
tests/                pytest suite
output/               created at run time: bookings.json, rejected_offers.csv (git-ignored)
logs/                 created at run time: busbidder.log (git-ignored)
```

## Dataset

`data/offers.csv` comes from `scripts/generate_data.py` with a fixed seed (4420), so
every run uses the same 2,004 rows. Each departure is sold in two fare types: a basic fare and
a more flexible one. The data covers 10-16 July 2026, so the default 12-14 July travel window
genuinely filters by date. All 75 company-destination combinations are present. Company
behaviour follows the brief's profiles (`COMPANY_RULES` in the generator):

| Company | Speed | Price | Departures | Transfers | Fares |
|---|---|---|---|---|---|
| SolRuta | slowest daytime | cheapest | 07:00-17:00 | often 1-2 on long routes | Basic; Plus (changeable) |
| Camino Express | fastest | most expensive | morning, midday, evening | 0 to major cities | Standard (changeable); Business (changeable + refundable) |
| CostaLink | medium | cheap on the coast | 2/day coastal, 1/day inland | 0 coastal, 1 inland | Basic; Flex (changeable + refundable) |
| VerdeBus | medium | mid | morning and afternoon | mostly 0 | Eco (changeable); Eco Flex (changeable + refundable) |
| NocheVia | slow, with a rest stop | cheap | 21:00-23:45 | 0-1 | Night Saver; Night Flex (changeable) |

Missing values are part of the data on purpose: about 30% of SolRuta offers have no CO2
figure, and about 6% of offers have no rating. About 6% of offers are sold out.

### Validation policy

| Situation | Action | Examples in the data |
|---|---|---|
| Missing/unreadable file, missing columns, bad reference data | **Fatal** (`DataFileError`); the program stops with a message | n/a |
| Impossible or inconsistent offer | **Rejected** and set aside for review in `output/rejected_offers.csv`, with every reason | negative price; missing price; unknown destination (Lisbon); unknown company; invalid date (32 July); arrival before departure; journey > 24 h; rating 7.5 on a 1-5 scale; negative seats; non-numeric transfers; malformed ID; ID that disagrees with its own fields; duplicate ID |
| Unambiguous error | **Corrected** and logged | `"  valencia "` / `"Cádiz"` → canonical name; wrong duration → recomputed from timestamps |
| Missing optional value (rating, CO2) | **Kept** as missing; scored neutrally (50) and flagged `*` | 272 offers (118 without rating, 178 without CO2) |
| 0 seats | **Kept** (valid data); removed by the seat constraint | 120 offers |

Regular expressions are used where the format really is structured: offer IDs
(`SR-MLG-20260712-0830-B`, whose groups are cross-checked against the row's company,
destination, date and time), company and destination codes, booking references
(`BB-YYMMDD-XXXXXX`), passenger names, custom departure windows (`07:00-10:00`) and weight
strings (`price=40,duration=25`).

## How the ranking works

### Hard constraints vs soft preferences

| Hard constraint (offer removed) | Why hard |
|---|---|
| Destination and travel window | They define the search; an offer outside them answers a different question |
| Seat availability ≥ passengers | An offer that cannot be booked is useless |
| Budget per ticket (optional) | A budget is a limit the traveller cannot exceed, not a wish |
| Max transfers, max duration, min luggage (optional) | Practical limits, e.g. a business traveller who must arrive the same day |
| Changeable / refundable required (optional) | A yes/no ticket condition the traveller needs |

| Soft criterion (weighted) | Direction | Scoring (0-100) |
|---|---|---|
| Price | lower better | relative: `100 × cheapest / price` |
| Duration | lower better | relative: `100 × fastest / duration` |
| Departure time | closer to the preferred window is better | absolute: 100 inside the window, falling linearly to 0 at 240 min outside it |
| Flexibility | more flexible is better | categorical: changeable + refundable 100, changeable 60, refundable 40, neither 0 |
| Transfers (optional) | fewer better | 0 → 100, 1 → 50, 2+ → 0 |
| Rating (optional) | higher better | linear: 1 → 0, 5 → 100 |
| CO2 emissions (optional) | lower better | relative, like price |

The output always lists hard constraints and soft preferences under separate headings.

`Total = Σ weight × score`. Weights total 100%, so the total is 0-100 and each criterion's
contribution reads directly as points.

### Design decisions the brief asks for

- **Normalisation.** Price, duration and CO2 use *ratio* scaling against the best acceptable
  offer. Min-max scaling (also implemented, `config/settings_minmax.json`) stretches any
  difference to the full 0-100 range: two offers at EUR 30 and EUR 31 would score 100 and 0.
  That lets a trivial difference dominate. Ratio scaling keeps a 1-euro gap at about 3 points,
  and a single extreme outlier does not compress everyone else's scores. Criteria with a
  meaningful fixed scale (departure fit, flexibility, transfers, rating) use absolute rules.
  Their scores therefore do not depend on which other offers happen to be in the result.
- **Lower vs higher is better.** Each criterion declares its direction. Relative criteria invert
  the ratio for lower-is-better values, and absolute rules are written so that better = higher.
- **Weight validation.** Weights must be non-negative and total 100 (or 1.0), and the four core
  criteria must always be listed (0 is allowed). An incorrect total is rejected, not silently
  rescaled, because 40+25+15+10 = 90 is more likely a typo than an intention.
- **Ties.** Totals are rounded to 2 decimals. Tied offers are marked `=` and ordered by lower
  price, then earlier departure, then offer ID, so the ranking is fully reproducible.
- **Missing values.** A missing rating or CO2 value gets a neutral 50. It is marked `*` in the
  table and counted in a warning, so a missing value is neither rewarded nor treated as the worst.
- **Extreme values.** The validator rejects impossible values (price ≤ 0 or > EUR 500, duration
  > 24 h). Hard constraints bound the rest, and ratio scaling limits an outlier's effect.
- **Unintended dominance.** All criteria share the 0-100 scale. Ratio scaling avoids inflating
  tiny differences. Any weight above 50% triggers a warning, and the per-criterion scores are
  shown for every offer. The `sensitivity` command shows whether the winner changes when any one
  weight moves ±10 points.

### Object-oriented design

- **Composition.** `Offer` *has* `FareConditions`, a `Company` and a `Destination`. The
  `Marketplace` facade *has* a catalogue, profiles, a ranker and a booking manager. Each part is
  tested on its own.
- **Inheritance**, where behaviour really differs: `Criterion` → `RelativeCriterion` (scored
  against the other offers through a pluggable normaliser) and `AbsoluteCriterion` (fixed
  scale). Concrete criteria only implement what is specific to them.
- **Metaprogramming:**
  - `Criterion.__init_subclass__` registers every concrete criterion class automatically.
  - The `@hard_constraint` and `@normaliser` decorators register functions in registries.
  - The ranker, weight validation, profiles and CLI discover criteria and constraints from
    these registries, so adding one means writing one class or function.
- **Immutability.** Offers are frozen dataclasses. Booked seats are never written back to the
  offer data: remaining seats = original seats − seats in confirmed bookings, so capacity
  cannot become inconsistent. A cancellation restores seats automatically.
- **Bookings** are saved atomically (write to a temp file, then replace) in
  `output/bookings.json`. Each booking stores a snapshot of the fare conditions. No payment
  data is collected; payment status is simulated.

## Reproducing the report's findings

```bash
sh scripts/make_examples.sh       # set PYTHON=python if python3 is not on your PATH
```

This regenerates `examples/`. The headline comparison (Madrid → Malaga, 12-14 July,
`examples/04_compare_profiles_malaga.txt`):

| Profile | Acceptable offers | Recommended | Price | Duration | Fare | Score |
|---|---|---|---|---|---|---|
| Example traveller | 20 | CostaLink 12 Jul 11:00 | 41.83 | 6h50 | Flex C+R | 82.44 |
| Budget traveller | 21 | SolRuta 14 Jul 14:30 | 24.55 | 7h30 | Basic | 85.82 |
| Business traveller | 33 | Camino Express 13 Jul 07:00 | 71.32 | 6h10 | Business C+R | 91.45 |
| Flexibility-focused tourist | 53 | VerdeBus 12 Jul 15:45 | 54.76 | 6h45 | Eco Flex C+R | 86.43 |
| Environment-focused traveller | 48 | VerdeBus 13 Jul 15:45 | 52.46 | 6h45 | Eco Flex C+R | 83.10 |

Under min-max normalisation (`examples/08_rank_example_minmax.txt`), the example traveller's
winner changes to SolRuta Plus. The sensitivity run (`examples/06_…`) shows the same
recommendation flips when the price weight rises by 10 points.

## Known limitations

- The data is simulated, and company behaviour is an assumption encoded in the generator.
- Ratio scores are relative to the result set: adding or removing one offer can change other
  offers' scores, though not the order between two unaffected offers on that criterion.
- The flexibility and transfer scales and the 240-minute departure tolerance are judgement
  calls. They are documented and configurable, but not derived from traveller data.
- A neutral 50 for missing data can favour offers whose true value would be poor. Because
  SolRuta is most often missing CO2 data, this affects one company more than the others.
- Bookings are single-user: there is no locking for concurrent writers.
- No real dates are checked against today's date, so results stay reproducible.
