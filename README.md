# MikeCipro

Content, Agent, Etc

---

## Otočky v hlavě

Agent, který vyhledává a dokumentuje **sportovní obraty otočené hlavou** — zápasy, závody
a turnaje, kde mentální zlom není domněnka novináře, ale je **doložený vlastními slovy aktéra**
z rozhovoru nebo tiskovky po události.

**Přehled:** https://claude.ai/code/artifact/01056043-c9ee-4ff8-84a9-c682276e616e

### Podmínka zařazení

Případ se zařadí, jen když platí všechny tři body:

1. **Doložená otočka** — prokazatelně nepříznivý stav obrácený ve výsledek
2. **Mentální zlom** — ne primárně taktický, fyzický ani zaviněný soupeřem
3. **Doložený výrok** — aktér o tom po události sám mluvil nebo na to byl tázán

Bod 3 je tvrdý. Bez dohledatelné citace se případ nezařadí, ať je zápas sebeslavnější —
tím se sbírka liší od tisíce článků „největší comebacky historie".

### Jak to používat

```
/otocky                              # najdi nový případ
/otocky tenis 90. léta               # prohledej sport a období
/otocky doplň 2021-ufc-262-oliveira  # doplň nebo ověř existující případ
```

Po každé změně:

```bash
python3 scripts/validate.py     # kontrola schématu a podmínek zařazení
python3 scripts/build_site.py   # přegeneruje site/index.html
```

### Struktura

| Cesta | Obsah |
|---|---|
| `.claude/skills/otocky/SKILL.md` | Agent — kritéria, workflow, zdroje podle sportu, pravidla proti vymýšlení |
| `data/cases/*.json` | Jednotlivé případy |
| `data/rejected.md` | Odmítnuté případy a kandidáti k doplnění |
| `schema/case.schema.json` | Schéma případu |
| `scripts/validate.py` | Validace |
| `scripts/build_site.py` | Generátor přehledu |
| `site/index.html` | Prohlížitelný přehled (generovaný) |

### Aktuální obsah

6 případů napříč 6 sporty:

| Případ | Sport | Otočka |
|---|---|---|
| Djoković – Federer, Wimbledon 2019 | tenis | Dva odvrácené mečboly, publikum proti němu |
| Liverpool – Barcelona, LM 2019 | fotbal | 0:3 z prvního zápasu → 4:0 na Anfieldu |
| Sergio García, Masters 2017 | golf | 73 majorů bez titulu a pověst mentálně křehkého hráče |
| Boston Bruins – Toronto, Game 7 2013 | hokej | 1:4 deset minut před koncem |
| Charles Oliveira – Chandler, UFC 262 | mma | Dvakrát složen v prvním kole |
| Mo Farah, Rio 2016, 10 000 m | atletika | Pád uprostřed olympijského finále |

### Pravidla, která agent nesmí porušit

- **Nikdy nevymýšlet citace** — ani parafrázi neoznačovat jako citaci
- **Nikdy nevymýšlet URL** — neověřený odkaz se nahradí vyhledávacím a označí jako takový
- Ke každé citaci originál, překlad, kontext, zdroj a datum
- Co není doložené, se zapíše do pole `uncertainty`, ne domyslí
