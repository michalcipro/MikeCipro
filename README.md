# MikeCipro — autonomní agent pro Instagram

Agent, který se stará o instagramový profil od nápadu po vyhodnocení:
vymyslí obsah, napíše texty, vyrobí grafiku, sestříhá Reel, naplánuje čas,
publikuje, změří výsledky — a podle nich upraví, co bude dělat příště.

```
 sběr dat ──► učení ──► plán ──► výroba ──► publikace ──► měření ──┐
    ▲                                                              │
    └──────────────────────────────────────────────────────────────┘
```

---

## Co to umí

| Oblast | Konkrétně |
|---|---|
| **Série** | Čtyři opakovatelné série s pevným dnem v týdnu — agent nevymýšlí novou identitu každý den, jen plní daný formát |
| **Texty** | Popisky, hooky, hashtagy, alt texty, první komentář, scénáře Reelů — česky, v tónu tvojí značky (Claude) |
| **Grafika** | Citátové karty, číslované tipy, statistiky, obálky, celé karusely (cover → slidy → outro) v barvách a fontech značky |
| **Fotky** | Chytrý ořez podle obsahu (ne slepě na střed), jemné doladění, vodoznak, karusel z fotek, poměry 4:5 / 1:1 / 9:16 |
| **Reels** | Automatický výběr nejzajímavějších úseků z delšího videa, jump cut (vyhození ticha), překlopení do 9:16, vypálený hook a titulky, hudba s uhnutím pod hlasem, srovnání hlasitosti, obálka |
| **Reel z fotek** | Slideshow s pomalým nájezdem (Ken Burns) a prolínačkami |
| **Publikace** | Fotka, karusel, Reel i story přes oficiální Instagram Graph API, včetně prvního komentáře |
| **Analýza** | Skóre podle konverze dosahu (nová sledování, sdílení, uložení, dokoukání), **ne podle views**; týdenní písemná analýza |
| **Učení** | Bandita nad vlastnostmi příspěvků — série, formát, téma, typ hooku, CTA, hodina, den, jazyk. Sám zjistí, co funguje, a posune k tomu plán |
| **Recyklace** | Po každých 10 videích vezme dva nejlepší náměty a naplánuje jejich novou verzi; každý 5. anglicky |
| **Komentáře** | Sběr, návrhy odpovědí, eskalace toho, co má řešit člověk |

---

## Co si musíš ohlídat (přečti si to dřív, než začneš)

Tohle nejsou moje omezení, ale pravidla Meta a Instagramu:

1. **Musíš mít profesionální účet** (Business nebo Creator) propojený
   s Facebook stránkou. Osobní účet přes oficiální API publikovat **nelze**
   a obcházet to neoficiálními cestami znamená riziko zablokování účtu —
   tohle to nedělá a dělat nebude. Přepnutí je v aplikaci zdarma:
   *Nastavení → Typ účtu → Přejít na profesionální účet*.
2. **Publikace jde jen z veřejné URL.** Instagram si soubor stáhne sám —
   nejde ho nahrát přímo. Proto potřebuješ `MEDIA_PUBLIC_BASE` (vlastní web,
   S3, Cloudflare R2…). Návod níž.
3. **Limit 50 příspěvků za 24 hodin** na účet. Agent si hlídá vlastní, ještě
   přísnější limit (`MAX_POSTS_PER_DAY`).
4. **Hudba.** Do Reelů skládaných přes API nelze přidat hudbu z knihovny
   Instagramu. Agent přimíchá jen soubor, který mu dáš do `data/music/` —
   a **je na tobě, abys k němu měl práva.** Cizí komerční hudba znamená
   ztlumení, omezení dosahu nebo strike.
5. **Stories** mají přes API kratší životnost funkcí než z aplikace
   (nejde přidat anketa, odkaz, hudba).

---

## Instalace

```bash
git clone <tvůj-repozitář> MikeCipro
cd MikeCipro

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .                   # nebo: pip install -r requirements.txt

# volitelně, když chceš hostovat média na S3/R2:
pip install -e ".[s3]"
```

**ffmpeg** — agent ho hledá v tomhle pořadí: `FFMPEG_BINARY` → systémový
`ffmpeg` → statická binárka z `imageio-ffmpeg` (nainstaluje se sama).
Doporučuju systémový, je rychlejší a umí víc:

```bash
sudo apt install ffmpeg      # Debian/Ubuntu
brew install ffmpeg          # macOS
```

**Fonty** — bez vlastních použije agent systémový DejaVu (umí českou
diakritiku). Pro lepší vzhled si stáhni třeba Inter nebo Montserrat, dej TTF
do `assets/fonts/` a zapiš do `config/brand.yaml`:

```yaml
fonts:
  bold: "Inter-Bold.ttf"
  regular: "Inter-Regular.ttf"
```

---

## Nastavení

### 1. Brand kit

```bash
cp config/brand.example.yaml config/brand.yaml
```

Tohle je nejdůležitější soubor — říká agentovi, kdo jsi, o čem píšeš, jakým
tónem, jaké máš barvy a co nikdy nepoužívat. Věnuj mu deset minut, vrátí se to.

### 2. Přístup k Instagramu

```bash
cp .env.example .env
```

Získání `IG_USER_ID` a `IG_ACCESS_TOKEN`:

1. **Profesionální účet** propojený s Facebook stránkou
   (*Nastavení účtu → Propojené účty*).
2. Na [developers.facebook.com](https://developers.facebook.com) vytvoř aplikaci
   (typ **Business**) a přidej produkt **Instagram Graph API**.
3. V **Graph API Exploreru** vyber svou aplikaci a vyžádej oprávnění:
   `instagram_basic`, `instagram_content_publish`,
   `instagram_manage_insights`, `instagram_manage_comments`,
   `pages_show_list`, `pages_read_engagement`.
4. Vygeneruj token a zjisti ID účtu:
   ```
   GET /me/accounts                                  → ID stránky
   GET /<ID_STRÁNKY>?fields=instagram_business_account → IG_USER_ID
   ```
5. Token z Exploreru platí ~1 hodinu. Prodluž ho na 60 dní:
   ```bash
   igagent token exchange        # potřebuje FB_APP_ID a FB_APP_SECRET v .env
   ```
   Výsledek zapiš do `.env` jako `IG_ACCESS_TOKEN`.
   **Token je potřeba obnovit zhruba jednou za dva měsíce** —
   `igagent token info` ukáže, do kdy platí.

### 3. Hosting médií

Instagram si soubor stahuje z veřejné adresy. Vyber si jednu cestu:

**A) Vlastní web / VPS** — ať tvůj nginx nebo Caddy servíruje složku `out/`:

```nginx
location /ig/ {
    alias /cesta/k/MikeCipro/out/;
    autoindex off;
}
```
```env
MEDIA_HOST=local
MEDIA_PUBLIC_BASE=https://tvujweb.cz/ig
```

**B) S3 / Cloudflare R2 / Backblaze B2** (`pip install boto3`):

```env
MEDIA_HOST=s3
S3_BUCKET=muj-bucket
S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
# MEDIA_PUBLIC_BASE nech prázdné → použijí se předpodepsané URL na 1 hodinu
```

### 4. Claude

```env
ANTHROPIC_API_KEY=sk-ant-...
```

### 5. Kontrola

```bash
igagent doctor
```

Vypíše, co je nastavené a co chybí. Než půjdeš dál, mělo by být zeleně vše.

---

## Používání

### Denní režim (doporučený start)

```env
AUTOPILOT=review     # agent vyrobí a naplánuje, ty schválíš
```

```bash
igagent run                    # cyklus: změř → nauč se → naplánuj → recykluj → vyrob
igagent kalendar               # co kdy natočit
igagent queue list             # co čeká
igagent queue show 4           # detail včetně popisku a cest k souborům
igagent queue approve 4        # schválím
igagent publish --id 4         # publikuju hned (jinak počká na svůj čas)
```

### Plný autopilot

```env
AUTOPILOT=full
MAX_POSTS_PER_DAY=2
```

```bash
igagent run     # naplánuje, vyrobí i publikuje sám
```

### Cron — aby to jelo bez tebe

```cron
# každou hodinu jeden cyklus
0 * * * * cd /cesta/k/MikeCipro && .venv/bin/igagent run >> data/cron.log 2>&1

# v neděli večer písemná analýza
0 20 * * 0 cd /cesta/k/MikeCipro && .venv/bin/igagent analyze --days 28 >> data/cron.log 2>&1
```

Hotová varianta: `scripts/crontab.example` a `scripts/run-cycle.sh`.

### Vlastní materiál

Nasyp fotky a videa do `data/inbox/`. Agent je uvidí při plánování a bude
kolem nich stavět nápady. Ručně:

```bash
igagent queue attach 7 ~/Videa/natoceno.mp4
igagent produce --id 7
```

### Ruční nástroje (bez fronty)

```bash
# Reel z dlouhého videa: vybere nejzajímavější úseky, 9:16, hook, obálka
igagent reel video.mp4 --seconds 25 --hook "Tohle jsem zjistil až po roce"

# mluvené video: vyhodí ticho
igagent reel rozhovor.mp4 --jumpcut --seconds 45

# fotky → příspěvek 4:5 (chytrý ořez)
igagent photos foto1.jpg foto2.jpg --ratio portrait

# fotky → Reel se slideshow
igagent photos *.jpg --reel --hook "Léto 2026" --seconds-each 2.4

# jednotlivá grafika
igagent graphic quote "Nejlepší čas začít byl včera." --kicker "Připomínka"
igagent graphic tips "Tři ranní věci" --items "Žádný telefon" "Tři priority" "Pohyb"
igagent graphic stat "47 %" --value "47 %" --context "Z vlastního dotazníku, n=312"
```

### Analýza a učení

```bash
igagent collect       # stáhne data z Instagramu
igagent learn         # přepočítá strategii
igagent strategy      # co se agent naučil
igagent kpi           # tabulka KPI po videích
igagent analyze       # písemná analýza do reports/
```

---

## Čtyři série a týdenní režim

Páteř profilu jsou **čtyři opakovatelné série**, každá má svůj den. Agent
nevymýšlí každý den novou identitu — ptá se „jak dneska udělat tuhle sérii
co nejlíp". Nastavené jsou v `config/brand.yaml`, sekce `series:`.

| Den | Série | Co slibuje divákovi |
|---|---|---|
| **pondělí 18:00** | Tvrdá pravda o výkonu | Vyvrátím jednu věc, které o mentální přípravě většina lidí věří |
| **středa 18:00** | Udělej to při příštím zápase | Jeden nástroj použitelný hned v dalším zápase |
| **pátek 19:00** | Co se právě stalo | Rozbor konkrétního momentu elitního sportovce |
| **neděle 19:00** | Z terénu | Co jsem viděl v reálné práci a v diagnostice |

Čtyři Reels týdně. Stories agent nepublikuje sám — náměty na ně má v brand
kitu jako připomínku (`stories:`).

```bash
igagent kalendar              # co kdy natočit, včetně volných termínů
igagent plan                  # naplní termíny konkrétními náměty
```

### Série „Co se právě stalo" potřebuje moment od tebe

Reakce na konkrétní situaci se nedá naplánovat dopředu a agent si ji
**nesmí vymýšlet** — od toho je pravidlo v promptu. Když něco uvidíš:

```bash
igagent moment add "Sinner po nevynucené chybě ve 4. gamu — reakce do 10 s"
igagent moment list
```

Agent to použije při nejbližším plánování pátečního termínu. Dokud žádný
moment nezadáš, páteční sloty zůstanou v kalendáři prázdné.

> **K právům:** komentovat cizí moment je v pořádku, ale nepoužívej záběry
> z přenosu ani cizí hudbu. Brand kit to má napsané jako pravidlo, takže
> s tím Claude počítá už při psaní scénáře.

### Prvních osm videí

Startovní dávka je připravená v `config/seed-first-8.yaml`:

```bash
igagent seed
```

Každý námět dostane nejbližší volný termín své série, takže se osm videí
samo rozloží do tří týdnů. Pak už jen natáčíš:

```bash
igagent queue attach 1 ~/Videa/sebevedomi.mp4
igagent produce --id 1        # jump cut, 9:16, hook, titulky, obálka, popisek
igagent queue approve 1
```

---

## Jak se vyhodnocuje výkon

**Views nejsou cíl a do skóre nevstupují.** Video pro 30 000 lidí, které
nikoho nepřivede, je horší než video pro 800 lidí s osmi novými sledujícími.
Proto se všechno dělí počtem zasažených lidí — skóre měří **konverzi dosahu**:

| KPI | Váha | Co to je |
|---|---|---|
| nová sledování / 1 000 zasažených | 35 % | hlavní ukazatel růstu |
| sdílení / 1 000 zasažených | 20 % | obsah, který lidé posílají dál |
| uložení / 1 000 zasažených | 20 % | obsah, ke kterému se vracejí |
| zhlédnutí / dosah | 15 % | udržení na začátku videa |
| podíl zhlédnuté délky | 10 % | průměrná doba sledování |

```bash
igagent kpi                   # tabulka po videích
```

Každá složka se porovnává s **mediánem tvého vlastního účtu**, takže
100 = tvůj průměrný příspěvek. Skóre nezestárne, když účet poroste.
Váhy se dají přenastavit v `igagent/analytics/metrics.py` (`KPI_WEIGHTS`).

**Poctivě k „udržení prvních tří sekund":** tenhle údaj Instagram Graph API
nedává. Nejbližší dostupná náhrada je poměr zhlédnutí k dosahu — trend
sleduje dobře, ale není to totéž číslo, jaké vidíš v aplikaci u retence.
Agent to tak i označuje a nepředstírá přesnost, kterou nemá.

**Malý dosah = velký šum.** Jedno sledování od 40 lidí by jinak vypadalo
jako zázrak, proto se každá míra stahuje k mediánu podle velikosti dosahu
(empirický Bayes). A doporučení typu „tvoje nejlepší hodina je…" se nikdy
nedělá z jediného příspěvku.

---

## Recyklace vítězných námětů

Dobrá myšlenka se neopouští po jednom videu. Po každých **10 publikovaných
videích** vezme agent dva nejlepší náměty a naplánuje jejich novou verzi:

- `jiny_uhel` — stejná myšlenka z pohledu trenéra místo hráče
- `jiny_format` — co bylo video, může být karusel s kroky
- `hlubsi` — původní video řeklo CO, nová verze řekne PROČ
- `prakticky` — z názoru se udělá nástroj do zápasu
- `anglicky` — **každý 5. recyklovaný námět** se natočí samostatně anglicky

```bash
igagent repurpose --status    # kolik videí od minule, kdo jsou vítězové
igagent repurpose             # naplánuje nové verze
```

Recykluje se jen to, co překonalo průměr účtu (skóre ≥ 115), a každý námět
jen jednou. Nikdy se nemíchají dva jazyky v jednom videu — anglická verze je
samostatné video, ne titulky pod českým.


---

## Jak se agent učí

Každý publikovaný příspěvek si nese sadu vlastností: **série, formát, téma,
typ hooku, typ CTA, šablona, hodina, den v týdnu, jazyk.** Po 24 hodinách se
změří a dostane skóre podle KPI z předchozí sekce (100 = tvůj průměr).

Nad každou vlastností si agent drží odhad:

```
odhad    = (součet skóre + 100 × 3) / (počet + 3)      ← stažení k průměru
priorita = odhad + 1,2 × směrodatná chyba              ← ochota zkoušet nejisté
```

Stažení k průměru je tam schválně: **jeden náhodně virální příspěvek nesmí
určit strategii na měsíc.** Druhý člen zase brání tomu, aby se agent zasekl
na první věci, která mu vyšla — nevyzkoušené varianty mají přednost, dokud
je nezkusí aspoň jednou (`EXPLORE_RATE` řídí, jak často zkouší dál).

Výsledkem je strategický profil, který jde **přímo do promptu pro Claude**
i do plánovače časů. Smyčka se tak uzavírá: data → strategie → obsah → data.

Agent se učí i z příspěvků, které publikuješ **ručně z telefonu** — při
každém sběru si je stáhne a označí jako `human`.

> Při malém vzorku (pod ~8 měřených příspěvků) to agent sám přizná
> a doporučení označí jako hypotézy. Reálně počítej s **6–8 týdny**, než
> začne mít strategie oporu v datech.

---

## Pojistky

| Pojistka | Co dělá |
|---|---|
| `AUTOPILOT=off` | Agent jen měří a navrhuje. Nic nepublikuje. |
| `AUTOPILOT=review` | Vyrobí a naplánuje, publikuje až po `queue approve`. |
| `MAX_POSTS_PER_DAY` | Vlastní denní strop, nezávisle na limitu Instagramu. |
| `MIN_HOURS_BETWEEN_POSTS` | Nedovolí dva příspěvky hned za sebou. |
| Kontrola kvóty | Před každou publikací se ptá API na zbývající limit. |
| `QA_IMAGES=1` | Claude se na vyrobenou grafiku podívá a hlídá přetečený či nečitelný text. |
| `--dry-run` | Projde celý postup, ale na Instagram nic neodešle. |
| Komentáře | Odpovědi se **nikdy** neodesílají samy — jen se navrhnou. |
| Audit log | Tabulka `events` v databázi drží všechno, co agent udělal. |

---

## Struktura

```
igagent/
  config.py          nastavení (.env) a brand kit (YAML)
  instagram/         Graph API: klient, publikace, hosting médií
  media/             grafika (Pillow), fotky, střih Reelů (ffmpeg), textové vrstvy
  brain/             Claude: prompty, JSON schémata, plánování/psaní/analýza
  analytics/         sběr dat, skórování, učící smyčka
  store/             SQLite (schéma v schema.sql)
  pipeline/          plánovač sérií, výroba, publikace, recyklace, cyklus
  cli.py             příkazová řádka
tests/               100 testů včetně celého cyklu proti napodobeninám
config/brand.yaml         ← brand kit + definice čtyř sérií
config/seed-first-8.yaml  ← startovní dávka námětů
data/moments.txt          ← momenty pro páteční sérii
data/inbox/          ← sem dávej fotky a videa
data/music/          ← hudba, ke které máš práva
out/                 hotová média (tahle složka se publikuje na web)
reports/             písemné analýzy
```

## Testy

```bash
pip install pytest
pytest                      # celá sada
pytest -k "not video"       # bez testů, které renderují video (rychlejší)
```

---

## Když něco nefunguje

| Problém | Řešení |
|---|---|
| `Chybí IG_USER_ID` | Projdi krok 2 výš; `igagent doctor` ukáže, co přesně chybí |
| `The user is not an Instagram Business` | Účet není profesionální, nebo není propojený s FB stránkou |
| `Media upload has failed` | Instagram nedosáhl na tvoji URL — otevři ji v anonymním okně. Musí být veřejná a přes HTTPS |
| Reel se nezpracuje | Musí být H.264 + AAC, 3 s–15 min, do 1 GB. Agent to tak vyrábí, ale ruční soubory ne vždy |
| `OAuthException: Session has expired` | Vypršel token → `igagent token exchange` |
| Text v grafice je malý | Zkrať texty v `brand.yaml` nebo v zadání — šablona písmo zmenšuje, aby se vešlo |
| Chybí diakritika ve videu | Font bez českých znaků; nech prázdné `fonts:` a použije se DejaVu |
| `No such filter: 'drawtext'` | Nevadí — texty do videa se kreslí v Pillow, ne v ffmpeg |

---

## Licence a odpovědnost

Agent publikuje pod tvým jménem. Za obsah odpovídáš ty — proto je výchozí
režim `review` a proto se nikdy neposílají automatické odpovědi na komentáře.
Než pustíš `AUTOPILOT=full`, nech si pár příspěvků projít ručně.
