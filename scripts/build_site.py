#!/usr/bin/env python3
"""Vygeneruje prohlížitelný přehled případů do site/index.html.

Výstup je zároveň platná stránka pro lokální otevření i pro publikaci jako Artifact.
    python3 scripts/build_site.py
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "data" / "cases"
OUT = ROOT / "site" / "index.html"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SPORT_LABELS = {
    "tenis": "Tenis",
    "fotbal": "Fotbal",
    "hokej": "Hokej",
    "mma": "MMA",
    "golf": "Golf",
    "atletika": "Atletika",
    "basketbal": "Basketbal",
    "box": "Box",
    "cyklistika": "Cyklistika",
    "f1": "Formule 1",
    "ostatni": "Ostatní",
}

VIDEO_LABELS = {
    "key_moment": "Klíčový moment",
    "highlights": "Sestřih",
    "full_match": "Celý záznam",
    "press_conference": "Tiskovka",
    "interview": "Rozhovor",
    "documentary": "Dokument",
    "search": "Vyhledat",
}

MONTHS = [
    "ledna", "února", "března", "dubna", "května", "června",
    "července", "srpna", "září", "října", "listopadu", "prosince",
]

FONTS = (
    "https://fonts.googleapis.com/css2?"
    "family=Archivo:wght@500;600;700"
    "&family=IBM+Plex+Mono:wght@400;500"
    "&family=Newsreader:ital,opsz,wght@0,6..72,400..600;1,6..72,400"
    "&display=swap"
)

CSS = """
:root {
  --ground: #f5f6f3;
  --surface: #ffffff;
  --ink: #151815;
  --ink-soft: #5d665f;
  --ink-faint: #8b948c;
  --rule: #dcdfd8;
  --rule-soft: #e8eae4;
  --turn-from: #973030;
  --turn-from-bg: #f6ecea;
  --turn-to: #0f6e4f;
  --turn-to-bg: #e7f1ec;
  --accent: #0f6e4f;
  --note-bg: #f1f0e8;
  --shadow: 0 1px 2px rgba(21, 24, 21, .05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #101311;
    --surface: #181c19;
    --ink: #e8eae6;
    --ink-soft: #9aa39b;
    --ink-faint: #6f7a72;
    --rule: #2b322d;
    --rule-soft: #232a25;
    --turn-from: #e0837b;
    --turn-from-bg: #2a1d1c;
    --turn-to: #58c396;
    --turn-to-bg: #14261f;
    --accent: #58c396;
    --note-bg: #1d211c;
    --shadow: 0 1px 2px rgba(0, 0, 0, .3);
  }
}
:root[data-theme="dark"] {
  --ground: #101311;
  --surface: #181c19;
  --ink: #e8eae6;
  --ink-soft: #9aa39b;
  --ink-faint: #6f7a72;
  --rule: #2b322d;
  --rule-soft: #232a25;
  --turn-from: #e0837b;
  --turn-from-bg: #2a1d1c;
  --turn-to: #58c396;
  --turn-to-bg: #14261f;
  --accent: #58c396;
  --note-bg: #1d211c;
  --shadow: 0 1px 2px rgba(0, 0, 0, .3);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: Newsreader, Georgia, "Times New Roman", serif;
  font-size: 17px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

.page {
  max-width: 820px;
  margin: 0 auto;
  padding-inline: 20px;
  padding-block: 56px 72px;
}

.eyebrow {
  font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin: 0;
}

.masthead { display: flex; flex-direction: column; gap: 18px; }

.masthead h1 {
  font-family: Archivo, "Helvetica Neue", Arial, sans-serif;
  font-weight: 700;
  font-size: clamp(2.5rem, 8vw, 4rem);
  line-height: .98;
  letter-spacing: -.025em;
  text-wrap: balance;
  margin: 0;
}

.lede {
  margin: 0;
  font-size: 1.2rem;
  line-height: 1.5;
  color: var(--ink-soft);
  max-width: 34em;
}

.criteria {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: grid;
  gap: 10px;
  border-top: 1px solid var(--rule);
  padding-top: 20px;
}
.criteria li {
  display: grid;
  grid-template-columns: 1.6rem 1fr;
  gap: 10px;
  align-items: baseline;
  font-size: .95rem;
  color: var(--ink-soft);
}
.criteria b {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: .75rem;
  font-weight: 500;
  color: var(--accent);
}

.filters button {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--ink-soft);
  background: transparent;
  border: 1px solid var(--rule);
  border-radius: 999px;
  padding: 7px 13px;
  cursor: pointer;
  transition: color .15s, border-color .15s, background-color .15s;
}
.filters button:hover { border-color: var(--ink-faint); color: var(--ink); }
.filters button[aria-pressed="true"] {
  background: var(--ink);
  border-color: var(--ink);
  color: var(--ground);
}
.filters button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.cases { display: grid; gap: 12px; margin-top: 28px; }

.case {
  background: var(--surface);
  border: 1px solid var(--rule-soft);
  border-radius: 4px;
  box-shadow: var(--shadow);
  padding: 30px clamp(18px, 4vw, 34px) 26px;
  display: grid;
  gap: 22px;
}
.case[hidden] { display: none; }

.case-head { display: grid; gap: 10px; }

.case-meta {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--ink-faint);
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.case-meta .sport { color: var(--accent); font-weight: 500; }
.case-meta .sep { color: var(--rule); }

.case h2 {
  font-family: Archivo, "Helvetica Neue", Arial, sans-serif;
  font-weight: 600;
  font-size: clamp(1.5rem, 4vw, 2rem);
  line-height: 1.1;
  letter-spacing: -.018em;
  text-wrap: balance;
  margin: 0;
}
.case h2 .vs { color: var(--ink-faint); font-weight: 500; }
.case-event { margin: 0; font-size: .98rem; color: var(--ink-soft); }

.turn {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 14px;
  align-items: stretch;
}
.turn-side {
  border-radius: 3px;
  padding: 14px 16px;
  display: grid;
  gap: 7px;
  align-content: start;
}
.turn-from { background: var(--turn-from-bg); }
.turn-to { background: var(--turn-to-bg); }
.turn-label {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .13em;
  text-transform: uppercase;
}
.turn-from .turn-label { color: var(--turn-from); }
.turn-to .turn-label { color: var(--turn-to); }
.turn-side p {
  margin: 0;
  font-size: .95rem;
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
}
.turn-arrow {
  align-self: center;
  font-family: Archivo, sans-serif;
  font-size: 1.3rem;
  color: var(--ink-faint);
}

.section-label {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin: 0 0 10px;
}

.prose p { margin: 0 0 12px; max-width: 62ch; }
.prose p:last-child { margin-bottom: 0; }

.pivot {
  margin: 0;
  padding-left: 16px;
  border-left: 2px solid var(--accent);
  font-size: .97rem;
  color: var(--ink-soft);
  max-width: 62ch;
}

.tags { display: flex; flex-wrap: wrap; gap: 6px; list-style: none; margin: 0; padding: 0; }
.tags li {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10.5px;
  letter-spacing: .05em;
  color: var(--ink-soft);
  border: 1px solid var(--rule);
  border-radius: 3px;
  padding: 4px 8px;
}

.quotes { display: grid; gap: 20px; }
.quote { margin: 0; display: grid; gap: 8px; }
.quote-cs {
  margin: 0;
  font-size: 1.24rem;
  line-height: 1.42;
  letter-spacing: -.005em;
  max-width: 32em;
}
.quote-orig {
  margin: 0;
  font-style: italic;
  font-size: .93rem;
  color: var(--ink-faint);
  max-width: 40em;
}
.quote-meta {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  line-height: 1.65;
  color: var(--ink-faint);
  display: grid;
  gap: 2px;
}
.quote-meta .who { color: var(--ink); font-weight: 500; letter-spacing: .02em; }
.quote-meta .role { color: var(--ink-faint); }
.quote-meta a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }

.stamp {
  display: inline-block;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--turn-to);
  border: 1px solid currentColor;
  border-radius: 3px;
  padding: 3px 7px;
}

.links { list-style: none; margin: 0; padding: 0; display: grid; gap: 7px; }
.links a {
  color: var(--ink);
  text-decoration: none;
  display: grid;
  grid-template-columns: 8.6rem 1fr;
  gap: 10px;
  align-items: baseline;
  font-size: .97rem;
}
.links a:hover { color: var(--accent); }
.links a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.vtype {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--ink-faint);
  border: 1px solid var(--rule);
  border-radius: 3px;
  padding: 3px 7px;
  flex: none;
}

.sources { display: flex; flex-wrap: wrap; gap: 6px 14px; list-style: none; margin: 0; padding: 0; }
.sources a {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  color: var(--ink-soft);
  text-underline-offset: 2px;
}
.sources a:hover { color: var(--accent); }

.note {
  background: var(--note-bg);
  border-radius: 3px;
  padding: 13px 16px;
  font-size: .9rem;
  line-height: 1.55;
  color: var(--ink-soft);
  max-width: 62ch;
}
.note b {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .11em;
  text-transform: uppercase;
  color: var(--ink-faint);
  display: block;
  margin-bottom: 5px;
  font-weight: 500;
}

.divider { height: 1px; background: var(--rule-soft); }

.empty {
  padding: 40px 0;
  color: var(--ink-faint);
  font-size: .97rem;
}

.colophon {
  margin-top: 44px;
  padding-top: 22px;
  border-top: 1px solid var(--rule);
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  line-height: 1.8;
  color: var(--ink-faint);
}

.controls {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  margin-block: 44px 8px;
  padding-top: 22px;
  border-top: 1px solid var(--rule);
}
.filters { display: flex; flex-wrap: wrap; gap: 8px; }

.views { display: flex; gap: 0; border: 1px solid var(--rule); border-radius: 999px; padding: 3px; }
.views button {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--ink-soft);
  background: transparent;
  border: 0;
  border-radius: 999px;
  padding: 6px 14px;
  cursor: pointer;
  transition: color .15s, background-color .15s;
}
.views button[aria-pressed="true"] { background: var(--accent); color: var(--surface); }
.views button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.concept {
  background: var(--note-bg);
  border-radius: 3px;
  padding: 22px clamp(16px, 3vw, 24px);
  display: grid;
  gap: 20px;
}
.concept-head { display: grid; gap: 8px; }
.concept-title {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.concept-title .dur { color: var(--accent); }
.concept-hook {
  margin: 0;
  font-family: Archivo, "Helvetica Neue", Arial, sans-serif;
  font-weight: 600;
  font-size: clamp(1.15rem, 3vw, 1.4rem);
  line-height: 1.2;
  letter-spacing: -.012em;
  text-wrap: balance;
}
.concept-logline { margin: 0; font-size: .95rem; color: var(--ink-soft); max-width: 52ch; }

.beats { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
.beat {
  display: grid;
  grid-template-columns: 7.6rem 1fr;
  gap: 14px;
  padding: 11px 0;
  border-top: 1px solid var(--rule-soft);
}
.beat-mark { display: grid; gap: 3px; align-content: start; }
.beat-t {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
}
.beat-name {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .11em;
  text-transform: uppercase;
  color: var(--accent);
}
.beat-body { display: grid; gap: 8px; }
.beat-visual { margin: 0; font-size: .93rem; line-height: 1.5; color: var(--ink-soft); max-width: 56ch; }
.beat-overlay {
  margin: 0;
  justify-self: start;
  font-family: Archivo, "Helvetica Neue", Arial, sans-serif;
  font-weight: 600;
  font-size: .95rem;
  line-height: 1.25;
  letter-spacing: -.008em;
  color: var(--ink);
  background: var(--surface);
  border-left: 3px solid var(--accent);
  border-radius: 2px;
  padding: 7px 11px;
  max-width: 30ch;
}

.beat-attrib {
  margin: 0;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.caption {
  margin: 0;
  white-space: pre-line;
  font-size: 1rem;
  line-height: 1.62;
  max-width: 54ch;
  background: var(--surface);
  border-radius: 3px;
  padding: 16px 18px;
}
.hashtags {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.hashtags li {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  color: var(--accent);
}
.audio-note {
  margin: 0;
  font-size: .9rem;
  line-height: 1.5;
  color: var(--ink-soft);
  padding-left: 14px;
  border-left: 2px solid var(--rule);
  max-width: 56ch;
}

.view-concepts .case > :not(.case-head):not(.concept) { display: none; }
.view-concepts .case:not([data-concept="1"]) { display: none; }

@media (max-width: 620px) {
  .beat { grid-template-columns: 1fr; gap: 8px; }
  .beat-mark { display: flex; gap: 10px; align-items: baseline; }
  .controls { gap: 16px; }
}

@media (max-width: 620px) {
  .links a { grid-template-columns: 1fr; gap: 5px; }
  .vtype { justify-self: start; }
  .turn { grid-template-columns: 1fr; gap: 8px; }
  .turn-arrow { justify-self: center; transform: rotate(90deg); font-size: 1.1rem; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
"""

JS = """
(function () {
  var buttons = Array.prototype.slice.call(document.querySelectorAll('.filters button'));
  var cases = Array.prototype.slice.call(document.querySelectorAll('.case'));
  var empty = document.getElementById('empty');

  function apply(sport) {
    var shown = 0;
    cases.forEach(function (el) {
      var match = sport === 'vse' || el.dataset.sport === sport;
      el.hidden = !match;
      if (match) shown++;
    });
    empty.hidden = shown > 0;
    buttons.forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.sport === sport));
    });
  }

  buttons.forEach(function (b) {
    b.addEventListener('click', function () { apply(b.dataset.sport); });
  });

  var viewButtons = Array.prototype.slice.call(document.querySelectorAll('.views button'));
  var page = document.querySelector('.page');

  viewButtons.forEach(function (b) {
    b.addEventListener('click', function () {
      page.classList.toggle('view-concepts', b.dataset.view === 'koncepty');
      viewButtons.forEach(function (o) {
        o.setAttribute('aria-pressed', String(o === b));
      });
    });
  });
})();
"""


def esc(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def cz_date(iso: str) -> str:
    try:
        year, month, day = iso.split("-")
        return f"{int(day)}. {MONTHS[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return iso


def render_quote(quote: dict) -> str:
    role = quote.get("role")
    date = quote.get("date")
    parts = [
        '<blockquote class="quote">',
        f'<p class="quote-cs">„{esc(quote["quote_cs"])}“</p>',
        f'<p class="quote-orig">“{esc(quote["quote_original"])}”</p>',
        '<footer class="quote-meta">',
        f'<span><span class="who">{esc(quote["speaker"])}</span>'
        + (f' <span class="role">— {esc(role)}</span>' if role else "")
        + "</span>",
        f"<span>{esc(quote['context'])}</span>",
        f'<span><a href="{esc(quote["source_url"])}" target="_blank" rel="noopener">'
        f"{esc(quote['source_name'])}</a>" + (f" · {esc(cz_date(date) if DATE_RE.match(date) else date)}" if date else "") + "</span>",
        "</footer>",
        "</blockquote>",
    ]
    return "".join(parts)


def render_concept(concept: dict) -> str:
    payoff = concept["payoff"]
    beats = "".join(
        '<li class="beat">'
        f'<div class="beat-mark"><span class="beat-t">{esc(b["t"])}</span>'
        f'<span class="beat-name">{esc(b["beat"])}</span></div>'
        '<div class="beat-body">'
        f'<p class="beat-visual">{esc(b["visual"])}</p>'
        f'<p class="beat-overlay">{esc(b["overlay"])}</p>'
        + (
            f'<p class="beat-attrib">{esc(payoff["speaker"])}</p>'
            if b["beat"] == "Důkaz"
            else ""
        )
        + "</div></li>"
        for b in concept["beats"]
    )
    tags = "".join(f"<li>{esc(t)}</li>" for t in concept["hashtags"])
    audio = (
        f'<p class="audio-note"><b>Zvuk:</b> {esc(concept["audio"])}</p>'
        if concept.get("audio")
        else ""
    )
    return (
        '<div class="concept">'
        '<div class="concept-head">'
        '<p class="concept-title"><span>Koncept pro Reel</span>'
        f'<span class="dur">9:16 · {concept["duration_s"]} s · 6 beatů</span></p>'
        f'<p class="concept-hook">{esc(concept["hook"])}</p>'
        f'<p class="concept-logline">{esc(concept["logline"])}</p>'
        "</div>"
        f'<div><p class="section-label">Sestřih</p><ul class="beats">{beats}</ul></div>'
        f'<div><p class="section-label">Caption</p><p class="caption">{esc(concept["caption"])}</p>'
        f'<ul class="hashtags">{tags}</ul></div>'
        + (f"<div>{audio}</div>" if audio else "")
        + "</div>"
    )


def render_case(case: dict) -> str:
    sport = case["sport"]
    opponent = case.get("opponent")
    stage = case.get("stage")

    blocks: list[str] = []

    meta = [
        f'<span class="sport">{esc(SPORT_LABELS.get(sport, sport))}</span>',
        '<span class="sep">/</span>',
        f"<span>{esc(cz_date(case['date']))}</span>",
    ]
    if stage:
        meta += ['<span class="sep">/</span>', f"<span>{esc(stage)}</span>"]

    title = esc(case["protagonist"])
    if opponent:
        title += f' <span class="vs">vs.</span> {esc(opponent)}'

    blocks.append(
        '<div class="case-head">'
        + f'<div class="case-meta">{"".join(meta)}</div>'
        + f"<h2>{title}</h2>"
        + f'<p class="case-event">{esc(case["event"])}</p>'
        + "</div>"
    )

    blocks.append(
        '<div class="turn">'
        '<div class="turn-side turn-from"><span class="turn-label">Stav</span>'
        f'<p>{esc(case["deficit"])}</p></div>'
        '<div class="turn-arrow" aria-hidden="true">→</div>'
        '<div class="turn-side turn-to"><span class="turn-label">Výsledek</span>'
        f'<p>{esc(case["result"])}</p></div>'
        "</div>"
    )

    blocks.append(f'<div class="prose"><p>{esc(case["story"])}</p></div>')

    blocks.append(
        '<div><p class="section-label">Zlom</p>'
        f'<p class="pivot">{esc(case["turning_point"])}</p></div>'
    )

    tags = "".join(f"<li>{esc(tag)}</li>" for tag in case["mental_mechanism"])
    blocks.append(f'<div><p class="section-label">Mechanismus</p><ul class="tags">{tags}</ul></div>')

    for field, label in (("uncertainty", "Co není doložené"), ("caveat", "Výhrada")):
        if case.get(field):
            blocks.append(f'<div class="note"><b>{label}</b>{esc(case[field])}</div>')

    blocks.append('<div class="divider"></div>')

    evidence = case["evidence"]
    stamp = "Tázán přímo" if evidence["asked_about"] else "Mluvil sám"
    quotes = "".join(render_quote(q) for q in evidence["athlete_quotes"])
    quotes += "".join(render_quote(q) for q in evidence.get("expert_quotes") or [])
    blocks.append(
        '<div><p class="section-label">Doložené výroky '
        f'<span class="stamp">{stamp}</span></p>'
        f'<div class="quotes">{quotes}</div></div>'
    )

    videos = "".join(
        f'<li><a href="{esc(v["url"])}" target="_blank" rel="noopener">'
        f'<span class="vtype">{esc(VIDEO_LABELS.get(v["type"], v["type"]))}</span>'
        f'<span>{esc(v["label"])}</span></a></li>'
        for v in case["video"]
    )
    blocks.append(f'<div><p class="section-label">Video</p><ul class="links">{videos}</ul></div>')

    sources = "".join(
        f'<li><a href="{esc(s["url"])}" target="_blank" rel="noopener">{esc(s["name"])}</a></li>'
        for s in case["sources"]
    )
    blocks.append(f'<div><p class="section-label">Zdroje</p><ul class="sources">{sources}</ul></div>')

    if case.get("concept"):
        blocks.append(render_concept(case["concept"]))

    has_concept = "1" if case.get("concept") else "0"
    return (
        f'<article class="case" data-sport="{esc(sport)}" data-concept="{has_concept}">'
        + "".join(blocks)
        + "</article>"
    )


def main() -> None:
    cases = []
    for path in sorted(CASES_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as fh:
            cases.append(json.load(fh))
    cases.sort(key=lambda c: c["date"], reverse=True)

    sports = sorted({c["sport"] for c in cases}, key=lambda s: SPORT_LABELS.get(s, s))
    n_concepts = sum(1 for c in cases if c.get("concept"))
    filters = ['<button type="button" data-sport="vse" aria-pressed="true">Vše</button>']
    filters += [
        f'<button type="button" data-sport="{esc(s)}" aria-pressed="false">'
        f"{esc(SPORT_LABELS.get(s, s))}</button>"
        for s in sports
    ]

    criteria = [
        ("1", "Doložená otočka — prokazatelně nepříznivý stav obrácený ve výsledek."),
        ("2", "Zlom byl mentální, ne primárně taktický, fyzický ani zaviněný soupeřem."),
        ("3", "Aktér o tom po události sám mluvil nebo na to byl tázán. Bez citace se případ nezařazuje."),
    ]
    criteria_html = "".join(f"<li><b>{n}</b><span>{esc(t)}</span></li>" for n, t in criteria)

    doc = f"""<title>Otočky v hlavě</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<style>{CSS}</style>
<div class="page">
  <header class="masthead">
    <p class="eyebrow">{len(cases)} případů · {len(sports)} sportů · {n_concepts} konceptů</p>
    <h1>Otočky v hlavě</h1>
    <p class="lede">Sportovní obraty, u kterých mentální zlom není domněnka novináře,
      ale doložený vlastními slovy aktéra. Z každého je hotový koncept na Reel.</p>
    <ol class="criteria">{criteria_html}</ol>
  </header>
  <div class="controls">
    <nav class="filters" aria-label="Filtr podle sportu">{"".join(filters)}</nav>
    <nav class="views" aria-label="Pohled">
      <button type="button" data-view="archiv" aria-pressed="true">Archiv</button>
      <button type="button" data-view="koncepty" aria-pressed="false">Koncepty</button>
    </nav>
  </div>
  <main class="cases">
    {"".join(render_case(c) for c in cases)}
    <p class="empty" id="empty" hidden>V tomto sportu zatím žádný případ není.</p>
  </main>
  <footer class="colophon">
    Každá citace je uvedena v originále i v překladu a odkazuje na dohledatelný zdroj.
    Případy bez doloženého výroku se do archivu nezařazují.<br>
    Generováno z <code>data/cases/*.json</code> skriptem <code>scripts/build_site.py</code>.
  </footer>
</div>
<script>{JS}</script>
"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(doc, encoding="utf-8")
    print(f"OK — {len(cases)} případů → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
