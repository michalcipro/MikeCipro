#!/usr/bin/env python3
"""Kontrola případů proti schématu a proti pravidlům sbírky.

Bez externích závislostí. Spouštěj po každém přidání případu:
    python3 scripts/validate.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "data" / "cases"
SCHEMA_PATH = ROOT / "schema" / "case.schema.json"

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL_RE = re.compile(r"^https?://")

QUOTE_FIELDS = ["speaker", "quote_original", "quote_cs", "context", "source_name", "source_url"]


def load_schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def check_quote(quote: dict, where: str, errors: list[str]) -> None:
    for field in QUOTE_FIELDS:
        if not quote.get(field):
            errors.append(f"{where}: citaci chybí povinné pole '{field}'")
    url = quote.get("source_url", "")
    if url and not URL_RE.match(url):
        errors.append(f"{where}: source_url není platná URL: {url!r}")


def check_case(path: Path, schema: dict, seen_ids: dict[str, Path]) -> list[str]:
    errors: list[str] = []
    try:
        with path.open(encoding="utf-8") as fh:
            case = json.load(fh)
    except json.JSONDecodeError as exc:
        return [f"{path.name}: nevalidní JSON — {exc}"]

    props = schema["properties"]

    for field in schema["required"]:
        if field not in case or case[field] in (None, "", [], {}):
            errors.append(f"{path.name}: chybí povinné pole '{field}'")

    for field in case:
        if field not in props:
            errors.append(f"{path.name}: neznámé pole '{field}'")

    case_id = case.get("id", "")
    if case_id and not ID_RE.match(case_id):
        errors.append(f"{path.name}: id '{case_id}' neodpovídá tvaru slug")
    if case_id and case_id != path.stem:
        errors.append(f"{path.name}: id '{case_id}' se neshoduje s názvem souboru")
    if case_id in seen_ids:
        errors.append(f"{path.name}: duplicitní id, už je v {seen_ids[case_id].name}")
    elif case_id:
        seen_ids[case_id] = path

    sport = case.get("sport")
    allowed = props["sport"]["enum"]
    if sport and sport not in allowed:
        errors.append(f"{path.name}: sport '{sport}' není v povoleném seznamu {allowed}")

    for field in ("date", "verified_at"):
        value = case.get(field)
        if value and not DATE_RE.match(value):
            errors.append(f"{path.name}: {field} '{value}' není ve tvaru YYYY-MM-DD")

    if not case.get("mental_mechanism"):
        errors.append(f"{path.name}: musí mít aspoň jeden mental_mechanism")

    evidence = case.get("evidence", {})
    if not isinstance(evidence.get("asked_about"), bool):
        errors.append(f"{path.name}: evidence.asked_about musí být true/false")

    athlete_quotes = evidence.get("athlete_quotes") or []
    if not athlete_quotes:
        # Podmínka 3 sbírky: bez doloženého výroku případ nepatří dovnitř.
        errors.append(f"{path.name}: chybí výrok protagonisty — případ nesplňuje podmínku zařazení")
    for i, quote in enumerate(athlete_quotes):
        check_quote(quote, f"{path.name}: athlete_quotes[{i}]", errors)
    for i, quote in enumerate(evidence.get("expert_quotes") or []):
        check_quote(quote, f"{path.name}: expert_quotes[{i}]", errors)

    videos = case.get("video") or []
    if not videos:
        errors.append(f"{path.name}: chybí odkaz na video")
    allowed_types = props["video"]["items"]["properties"]["type"]["enum"]
    for i, video in enumerate(videos):
        if video.get("type") not in allowed_types:
            errors.append(f"{path.name}: video[{i}].type '{video.get('type')}' není povolený")
        if not URL_RE.match(video.get("url", "")):
            errors.append(f"{path.name}: video[{i}].url není platná URL")

    for i, source in enumerate(case.get("sources") or []):
        if not URL_RE.match(source.get("url", "")):
            errors.append(f"{path.name}: sources[{i}].url není platná URL")

    return errors


def main() -> int:
    schema = load_schema()
    paths = sorted(CASES_DIR.glob("*.json"))
    if not paths:
        print("Žádné případy v data/cases/", file=sys.stderr)
        return 1

    seen_ids: dict[str, Path] = {}
    all_errors: list[str] = []
    for path in paths:
        all_errors.extend(check_case(path, schema, seen_ids))

    if all_errors:
        print(f"NEPROŠLO — {len(all_errors)} problémů:\n", file=sys.stderr)
        for error in all_errors:
            print(f"  ✗ {error}", file=sys.stderr)
        return 1

    print(f"OK — {len(paths)} případů, všechny validní.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
