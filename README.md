# MikeCipro

Content, Agent, Etc

---

## Otočky v hlavě

Agent, který vyhledává a dokumentuje **sportovní obraty otočené hlavou** — zápasy, závody
a turnaje, kde mentální zlom není domněnka novináře, ale je **doložený vlastními slovy aktéra**
z rozhovoru nebo tiskovky po události. Z každého případu pak udělá **hotový koncept
na Instagram Reel**: sestřih po beatech, texty na obraz, caption a hashtagy.

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
/otocky                              # najdi nový případ i s konceptem
/otocky tenis 90. léta               # prohledej sport a období
/otocky doplň 2021-ufc-262-oliveira  # doplň nebo ověř existující případ
/otocky koncept 2016-rio-farah       # přepiš koncept a caption
```

Přehled má dva pohledy: **Archiv** (rešerše — citace, videa, zdroje) a **Koncepty**
(produkční list — sestřih po beatech, texty na obraz, caption).

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

### Struktura konceptu — šest beatů

Každý příběh jede stejný oblouk. To je to, co z jednotlivých videí dělá sérii.

**Konec** (dno jako první záběr) → **Kontext** (proč to bylo ztracené) → **Zlom** (co se stalo
v hlavě) → **Obrat** (otočka) → **Důkaz** (sportovec to říká sám, s titulky) → **Pointa**
(jedna věta pro diváka).

Beat **Důkaz** je celý smysl série. Comeback umí sestříhat každý. Záběr z tiskovky, kde to
hráč vysvětluje vlastními slovy, dělá rozdíl mezi highlightem a příběhem.

### Aktuální obsah

6 případů napříč 6 sporty, u každého hotový koncept:

| Případ | Sport | Otočka | Pointa |
|---|---|---|---|
| Djoković – Federer, Wimbledon 2019 | tenis | Dva odvrácené mečboly, publikum proti němu | Vstup si nevybereš. To, co si z něj uděláš, ano. |
| Liverpool – Barcelona, LM 2019 | fotbal | 0:3 z prvního zápasu → 4:0 na Anfieldu | Někdy je cesta ven přes to, že se přestaneš bát prohry. |
| Sergio García, Masters 2017 | golf | 73 majorů bez titulu a pověst mentálně křehkého hráče | Nezměnil švih. Změnil to, co dělá po špatné ráně. |
| Boston Bruins – Toronto, Game 7 2013 | hokej | 1:4 deset minut před koncem | 1:4 otočit nedokážeš. Jeden gól dát dokážeš. |
| Charles Oliveira – Chandler, UFC 262 | mma | Dvakrát složen v prvním kole | Nálepka, kterou ti dají ostatní, není rozsudek. |
| Mo Farah, Rio 2016, 10 000 m | atletika | Pád uprostřed olympijského finále | Pád trval dvě vteřiny. Rozhodnutí míň. |

### Pravidla, která agent nesmí porušit

- **Nikdy nevymýšlet citace** — ani parafrázi neoznačovat jako citaci
- **Nikdy nevymýšlet URL** — neověřený odkaz se nahradí vyhledávacím a označí jako takový
- Ke každé citaci originál, překlad, kontext, zdroj a datum
- Co není doložené, se zapíše do pole `uncertainty`, ne domyslí
- **Cokoliv v konceptu podané jako citace — titulek na obraze i pointa — musí odpovídat
  výroku z `evidence`.** Validátor to kontroluje a bez shody neprojde
- K záběrům velkých soutěží (UEFA, UFC, NHL, Wimbledon, MOV) se vážou autorská práva —
  viz poznámka v SKILL.md
