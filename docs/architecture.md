# Architecture

## Components and data flow

```mermaid
flowchart TD
    subgraph inputs["Input files"]
        CO[data/companies.csv]
        DE[data/destinations.csv]
        OF[data/offers.csv]
        ST[config/settings.json]
        PF[config/profiles.json]
    end

    CLI["cli.py<br/>subcommands + interactive menu"] --> MK["marketplace.py<br/>Marketplace (facade)"]
    ST --> CFG["config.py<br/>Settings"] --> MK

    CO & DE & OF --> LD["loader.py<br/>parse, validate, correct"]
    LD -->|valid offers| CAT[Catalogue]
    LD -->|rejected rows + reasons| QU[output/rejected_offers.csv]
    PF --> PR["profiles.py<br/>TravellerProfile + Weights"]

    MK --> CAT
    MK --> PR
    MK --> BK["booking.py<br/>BookingManager"]
    BK <-->|read / atomic write| BJ[output/bookings.json]
    BK -->|seats already booked| INV[current offers]
    CAT --> INV

    MK --> RK["scoring.py<br/>Ranker"]
    INV --> RK
    RK --> HC["constraints.py<br/>@hard_constraint registry"]
    RK --> CR["criteria.py<br/>Criterion subclass registry<br/>+ @normaliser registry"]
    RK --> RR[RankingResult]
    RR --> EX[explain.py]
    RR --> SE[sensitivity.py]
    RR --> DI["display.py<br/>text tables"] --> CLI

    LD & RK & BK & CLI -.-> LOG[logs/busbidder.log]
```

## Ranking pipeline

```mermaid
flowchart LR
    A[Traveller request<br/>+ profile weights] --> B{Scope:<br/>route and dates}
    B -->|out of scope| X1[ignored]
    B --> C{Hard constraints:<br/>seats, budget, transfers,<br/>duration, luggage,<br/>changeable, refundable}
    C -->|any violated| X2[excluded,<br/>all reasons recorded]
    C --> D[Score each soft criterion<br/>0-100]
    D --> E[Missing value?<br/>neutral 50, flagged]
    E --> F[Total = sum of weight x score]
    F --> G[Sort: total desc, then price,<br/>departure, offer ID]
    G --> H[Explanation for #1<br/>+ warnings]
    H --> I[Optional booking:<br/>re-check seats, save]
```

## Responsibilities

| Module | Responsibility | Key classes / functions |
|---|---|---|
| `models.py` | Immutable domain objects | `Company`, `Destination`, `FareConditions`, `Offer`, `Booking` |
| `request.py` | What the traveller asked for | `TravelRequest`, `DepartureWindow` |
| `loader.py` | Read CSVs, validate, correct, quarantine | `load_catalogue`, `Catalogue`, `ValidationReport` |
| `constraints.py` | Hard constraints (pass/fail) | `@hard_constraint`, `apply_hard_constraints`, `FilterResult` |
| `criteria.py` | Soft criteria (0-100 scores) | `Criterion` → `RelativeCriterion` / `AbsoluteCriterion`, `@normaliser` |
| `scoring.py` | Weights and ranking | `Weights`, `Ranker`, `RankingResult` |
| `explain.py` | Generated explanation of the top offer | `explain_recommendation` |
| `sensitivity.py` | Does the winner survive weight changes? | `weight_sensitivity` |
| `profiles.py` | Named traveller profiles from JSON | `TravellerProfile`, `load_profiles` |
| `booking.py` | Simulated bookings and capacity | `BookingManager` |
| `marketplace.py` | Facade that wires the parts together | `Marketplace` |
| `display.py` | Text formatting only | `render_*` functions |
| `cli.py` | Commands and interactive menu | `main`, `InteractiveMenu` |
| `config.py`, `logging_setup.py` | Settings and log file | `Settings`, `load_settings`, `configure_logging` |
