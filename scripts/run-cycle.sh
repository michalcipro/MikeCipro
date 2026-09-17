#!/usr/bin/env bash
# Jeden cyklus agenta. Pouštěj z cronu.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x .venv/bin/igagent ]]; then
    BIN=".venv/bin/igagent"
elif [[ -x .venv/bin/python ]]; then
    BIN=".venv/bin/python -m igagent.cli"
else
    BIN="python3 -m igagent.cli"
fi

# jen jedna instance naráz — cykly se nesmí překrývat
LOCK="data/igagent.lock"
mkdir -p data
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "$(date -Is) Předchozí cyklus ještě běží, končím." >&2
    exit 0
fi

$BIN run "$@"
