#!/bin/sh
# Regenerate the example outputs in examples/ that the report refers to.
# Run from the project root:  sh scripts/make_examples.sh
set -e
PY="${PYTHON:-python3}"  # override with PYTHON=python on systems without python3
mkdir -p examples
"$PY" -m busbidder validate > examples/01_validation_report.txt
"$PY" -m busbidder profiles > examples/02_profiles.txt
"$PY" -m busbidder rank --profile example --top 0 --show-excluded > examples/03_rank_example_malaga.txt
"$PY" -m busbidder compare --destination Malaga > examples/04_compare_profiles_malaga.txt
"$PY" -m busbidder compare --destination Barcelona > examples/05_compare_profiles_barcelona.txt
"$PY" -m busbidder sensitivity --profile example > examples/06_sensitivity_example_malaga.txt
"$PY" -m busbidder sensitivity --profile business > examples/07_sensitivity_business_malaga.txt
"$PY" -m busbidder --config config/settings_minmax.json rank --profile example > examples/08_rank_example_minmax.txt
cp output/rejected_offers.csv examples/rejected_offers.csv
echo "Examples written to examples/"
