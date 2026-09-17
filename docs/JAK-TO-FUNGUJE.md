# Jak to funguje v praxi

Provozní příručka. Co děláš ty, co dělá agent, a kam co patří.

---

## Rozdělení práce

| Ty | Agent |
|---|---|
| Natočíš video (talking head, telefon stačí) | Vybere z něj nejlepší úseky, vyhodí ticho |
| Nahraješ ho do `data/inbox/` | Překlopí do 9:16, vypálí hook a titulky |
| Zadáš moment k rozboru (pátky) | Napíše popisek, hashtagy, alt text |
| Schválíš, co vyleze | Vyrobí obálku |
| | Naplánuje čas a publikuje |
| | Změří výsledky a upraví další plán |

Agent nedokáže natočit video za tebe. Všechny čtyři série stojí na tom,
že jsi v záběru ty. Zbytek už je jeho.

---

## Tři složky, které tě zajímají

```
data/inbox/          ← SEM nahráváš natočená videa a fotky
data/inbox/hotovo/   ← agent sem sám odsune, co už zpracoval
data/music/          ← hudba, ke které máš práva (nepovinné)
out/                 ← hotové Reels a grafika (odsud je bere Instagram)
reports/             ← písemné analýzy profilu
```

Ostatní složky (`work/`, `data/igagent.sqlite3`) jsou agentovy interní věci.

---

## Klíčové pravidlo: číslo v názvu souboru

Každý námět ve frontě má číslo. **Soubor pojmenuj tímhle číslem.**

```
data/inbox/1-sebevedomi.mp4      → námět #1
data/inbox/4_reset.mov           → námět #4
data/inbox/7 wisconsin.mp4       → námět #7
data/inbox/5-a.jpg, 5-b.jpg      → oba k námětu #5 (karusel)
```

Oddělovač může být pomlčka, podtržítko, tečka nebo mezera. Zbytek názvu
je jen pro tebe.

Soubor **bez čísla** agent sám nikam nepřipne. Je to schválně: jinak by
přilepil jedno video ke třem různým námětům a vyrobil tři různé popisky
nad stejným záběrem. Když se ti nechce přejmenovávat, `igagent inbox link
--auto` je doplní podle pořadí termínů — ale kontroluj, co z toho vyšlo.

---

## Týden v praxi

### Neděle večer, 20 minut: natáčení

Podíváš se, co tě čeká:

```bash
igagent kalendar
```

```
kdy                 série                           stav
Mon 21.09. 18:00    Tvrdá pravda o výkonu           #1 planned
Wed 23.09. 18:00    Udělej to při příštím zápase    #4 planned
Fri 25.09. 19:00    Co se právě stalo               volné ← potřebuje aktuální moment
Sun 27.09. 19:00    Z terénu                        #7 planned
```

Zjistíš, o čem přesně ta videa mají být:

```bash
igagent queue show 1
```

Vypíše se úhel pohledu, body, které mají zaznít, a pokyny k sérii.
Natočíš tři videa na jeden zátah. Nemusíš řešit délku ani začátek —
agent si z každého vybere, co použije.

### Neděle večer, 2 minuty: nahrání

Videa nakopíruješ do `data/inbox/` a pojmenuješ je čísly:

```
data/inbox/1-sebevedomi.mp4
data/inbox/4-reset-po-chybe.mp4
data/inbox/7-wisconsin.mp4
```

Zkontroluješ, že se to spárovalo:

```bash
igagent inbox
```

```
Připraveno k připnutí (podle čísla v názvu):
  #1    ← 1-sebevedomi.mp4
        Sebevědomí před zápasem nepotřebuješ
  #4    ← 4-reset-po-chybe.mp4
        Co má sportovec udělat do deseti sekund po chybě

  Připnout: igagent inbox link
```

```bash
igagent inbox link
```

Hotovo. Od téhle chvíle už agent běží sám.

### Během týdne: agent pracuje

Cron spouští `igagent run` každou hodinu. V jednom cyklu agent:

1. **změří**, jak si vedou dosavadní příspěvky,
2. **přepočítá strategii** podle nových dat,
3. **naplánuje** chybějící termíny (napíše náměty pro prázdné sloty),
4. **zrecykluje** vítězný námět, pokud na to přišla řada,
5. **vyrobí** to, co poletí ven do 48 hodin,
6. **publikuje**, co má čas a je schválené,
7. v neděli **napíše analýzu** do `reports/`.

Výroba jednoho Reelu trvá pár minut. Z tvého videa vznikne sestřih,
titulky, obálka a popisek. Zdrojový soubor se odsune do `data/inbox/hotovo/`.

### Kdykoliv během týdne: pátky

Když uvidíš moment, který stojí za rozbor:

```bash
igagent moment add "Sinner po nevynucené chybě ve 4. gamu — reakce do 10 s"
```

Agent na nejbližší páteční termín naplánuje rozbor. Pak už jen natočíš
video a nahraješ ho jako kterékoliv jiné.

Dokud žádný moment nezadáš, páteční sloty zůstanou prázdné. Agent si
moment nevymyslí — je to pravidlo v jeho promptu, protože vymyšlený
rozbor cizího zápasu je horší než žádný.

### Před publikací: schválení

Ve výchozím režimu (`AUTOPILOT=review`) se nic nezveřejní bez tebe:

```bash
igagent queue list
```

```
○ #1    produced   Mon 21.09. 18:00   tvrda_pravda   REEL   Sebevědomí před zápasem…
```

Podíváš se na hotové video a popisek:

```bash
igagent queue show 1
```

Soubor otevřeš přímo z cesty, kterou vypíše (`out/q1-...-reel.mp4`).
Když to sedí:

```bash
igagent queue approve 1
```

Publikuje se samo v naplánovaný čas. Když to chceš hned:

```bash
igagent publish --id 1
```

Když se ti to nelíbí:

```bash
igagent queue reject 1              # zahodit
igagent produce --id 1              # nebo nechat vyrobit znovu
```

Až budeš agentovi věřit, přepneš `AUTOPILOT=full` v `.env` a krok
se schvalováním odpadne.

---

## Co sledovat

Ne views.

```bash
igagent kpi
```

```
datum      série             dosah  follow/1k  sdíl/1k  ulož/1k  dokouk.  skóre
2026-09-15 z_terenu           5625        0.4      0.9      1.8      14%     46
2026-08-24 tvrda_pravda       1500        4.7     12.7     22.7      48%    250
```

Video s nejvyšším dosahem má skóre 46, video s nejnižším 250. Tak to má být:
rozhoduje, kolik lidí ze zasažených něco udělalo, ne kolik jich to vidělo.

```bash
igagent strategy      # co se agent naučil — nejlepší série, hodiny, typy hooků
igagent analyze       # písemná analýza za posledních 28 dní
```

Počítej s **6–8 týdny**, než začne mít strategie oporu v datech. Do té doby
agent své závěry sám označuje jako hypotézy.

---

## Recyklace

Po každých 10 videích vezme agent dva nejlepší náměty a naplánuje jejich
novou verzi — jiný úhel, jiný formát, hlubší rozbor. Každý pátý recyklovaný
námět se natáčí anglicky.

```bash
igagent repurpose --status
```

Ve frontě se objeví jako běžný námět, jen s poznámkou `↻jiny_uhel` a odkazem
na původní video. Natočíš ho stejně jako ostatní.

---

## Typické situace

**Natočil jsem video navíc, mimo plán.**
Nahraj ho bez čísla a použij `igagent inbox link --auto`, nebo si založ
námět ručně přes `igagent plan -n 1` a připni ho číslem.

**Video jsem natočil znovu, to první bylo špatné.**
Přepiš soubor v `data/inbox/` a spusť `igagent produce --id N` znovu.
Když už se stihl uklidit do `hotovo/`, vytáhni ho zpět nebo nahraj nový.

**Nestíhám natáčet.**
Nic se nestane. Prázdný termín agent přeskočí a příště naplánuje další.
Žádné „musíš postovat každý den".

**Chci jiné znění popisku.**
`igagent produce --id N` ho napíše znovu. Nebo si ho uprav a publikuj
z aplikace ručně — agent se z ručně publikovaných videí učí taky.

**Agent vyrobil něco, co nechci zveřejnit.**
`igagent queue reject N`. Nic se nepublikuje bez `approve`.

**Vypršel token k Instagramu (po ~60 dnech).**
`igagent token exchange` a nový token zapiš do `.env`.

---

## Denní / týdenní rytmus, shrnuto

| Kdy | Co děláš | Kolik to zabere |
|---|---|---|
| Neděle | `igagent kalendar`, natočíš 3–4 videa, nahraješ, `igagent inbox link` | ~30 min |
| Během týdne | `igagent moment add …`, když něco uvidíš | ~1 min |
| Před publikací | `igagent queue approve N` | ~2 min na video |
| Jednou za měsíc | `igagent kpi`, `igagent analyze` | ~15 min |
| Jednou za dva měsíce | obnovit token | ~5 min |

Všechno ostatní běží z cronu bez tebe.
