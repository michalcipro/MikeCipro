# reelcut – automatický střih Instagram Reels

`reelcut` projde video, zanalyzuje ho (záběry, pohyb, hlasitost, řeč, obličeje,
rytmus) a sestříhá z něj reel v cílové délce. **Řeč a rozhovory zachovává vcelku** –
nikdy nestřihne uprostřed věty. Výstup je rovnou připravený pro IG: 9:16,
1080×1920, 30 fps, H.264, hlasitost −14 LUFS, střihy na transienty zvuku a
nejsilnější moment jako „cold open“ na začátku.

```
reelcut cut video.mp4 -t 30            # analýza + plán + render do video_reel.mp4
reelcut analyze video.mp4 --timeline tl.png   # jen analýza + obrázek timeline
reelcut plan video.mp4 -t 45 --style talk     # jen plán střihu (JSON, dá se ručně upravit)
reelcut render video_plan.json -o reel.mp4    # render upraveného plánu
```

## Jak to funguje

1. **Probe** – ffprobe zjistí rozlišení, rotaci, fps, délku, zda je audio.
2. **Video analýza** (OpenCV, snímky streamované z ffmpeg v 384 px šířce, 8 fps)
   - detekce střihů z rozdílu barevných histogramů (adaptivní práh + pevný práh),
   - pohyb (rozdíl snímků), ostrost (variance Laplaciánu), barevnost
     (Hasler–Süsstrunk), expozice,
   - obličeje detektorem **YuNet** (přibalený model, Apache-2.0) – používají se
     pro skóre i pro **smart crop** na 9:16 (výřez sleduje obličej).
3. **Audio analýza** (numpy, mono 16 kHz)
   - křivka hlasitosti,
   - **detekce řeči (VAD)** – heuristika energie + řečové pásmo 300–3400 Hz +
     spektrální plochost + 2–8 Hz slabičná modulace (odlišuje řeč od hudby);
     s balíčkem `webrtcvad` se kombinuje s WebRTC VAD,
   - transienty (spektrální tok) a odhad tempa – střihy se přichytí na beat.
4. **Skórování** – všechny příznaky jsou normalizované na 0–1 na mřížce 0,1 s.
   Styl (`--style`) určuje váhy. Skóre záběru i „křivka zajímavosti“ se počítají
   až při plánování, takže jednu analýzu (cache `video.mp4.reelcut.json`) lze
   znovu použít pro různé délky a styly.
5. **Plán střihu**
   - řečové segmenty jsou atomické: buď celé, nebo vůbec; dlouhá řeč se dělí jen
     v jejích pauzách (`--max-speech-clip`),
   - ze zbytku každého záběru se vyberou nejlepší okna (1–5 s), nikdy nepřekročí
     hranici záběru, začátky/konce se přichytí na transient zvuku,
   - výběr: nejdřív řeč (režim `keep`), pak vizuální klipy podle skóre s penalizací
     za opakování stejného záběru; slabé klipy pod prahem kvality se vynechají,
   - **hook**: nejsilnější krátký moment (2,5 s) se přesune na začátek, zbytek
     jde chronologicky.
6. **Render** (ffmpeg) – seek na každý klip, crop/scale na 9:16, hard cut nebo
   crossfade, fade in/out, micro‑fade audia proti lupancům, `loudnorm`
   −14 LUFS, volitelně vypálené titulky.

## Instalace

Potřebujete **ffmpeg** (≥ 5) v PATH a Python ≥ 3.10.

```
cd tools/reelcut
pip install -e .                 # základ: numpy + opencv-python-headless
pip install -e ".[vad]"          # + přesnější detekce řeči (webrtcvad)
pip install -e ".[transcribe]"   # + faster-whisper: přepis, střih po větách, titulky
pip install -e ".[all,dev]"      # vše + pytest
```

## Použití

### `reelcut cut` – vše v jednom

```
reelcut cut vlog.mp4 -t 30 --style auto -o vlog_reel.mp4 --timeline vlog_tl.png
```

Vypíše plán (EDL) s důvody výběru každého klipu, uloží `vlog_reel_plan.json`
a vyrenderuje reel. Timeline PNG ukazuje křivku zajímavosti, řeč, obličeje,
hlasitost, hranice záběrů a vybrané klipy.

### Styly (`--style`)

| styl     | kdy                                 | co zvýhodňuje                    |
|----------|-------------------------------------|----------------------------------|
| `auto`   | výchozí, vyvážený                   | řeč, lidé, pohyb, zvuk           |
| `talk`   | rozhovory, talking head, podcast    | řeč ×2, obličeje                 |
| `action` | sport, akce, event, b‑roll          | pohyb, hlasitost                 |
| `vibe`   | travel, estetika                    | ostrost, barevnost, expozice     |

Váhy lze doladit: `--weights motion=2,speech=0.5`
(křivky: `audio, motion, faces, sharp, color, exposure, speech`).

### Řeč a rozhovory (`--speech`)

- `keep` (výchozí) – řeč se vybere jako první a vždy celá. Když se všechna
  řeč do cíle nevejde, vezmou se nejlepší celé části a plán to ohlásí.
- `prefer` – řeč soutěží s ostatními klipy, ale s bonusem.
- `ignore` – řeč se nebere v úvahu (čistě vizuální reel, hudba).

S `--transcribe` (faster-whisper) se řeč dělí přesně po větách, plán ukazuje
text každého klipu a `--captions` vypálí titulky ve stylu reels (skupiny
2–3 slov, velkým písmem) a uloží i `.srt`.

```
reelcut cut interview.mp4 -t 60 --style talk --transcribe --language cs --captions
```

### Formát výstupu

- `--aspect 9:16 | 4:5 | 1:1 | 16:9 | source`
- `--fit crop` (výchozí; výřez sleduje obličeje), `blur` (rozmazané pozadí,
  zachová celý obraz), `none`
- `--transition cut | fade`, `--transition-duration 0.25`
- `--crf 18`, `--preset medium`, `--fps 30`, `--no-loudnorm`

### Parametry plánu

```
-t/--target 30          cílová délka (IG: 15–90 s, sweet spot 20–40 s)
--min-clip 1.0          nejkratší vizuální klip
--max-clip 5.0          nejdelší vizuální klip
--max-speech-clip 15    delší řeč se rozdělí v pauzách
--hook-length 2.5       délka cold open; --no-hook vypne
--order chrono|score    pořadí klipů za hookem
--no-snap               nepřichytávat střihy na transienty
--min-quality 0.3       práh kvality vůči nejlepšímu klipu (0 = naplnit za každou cenu)
```

### Ruční doladění

`reelcut plan` uloží JSON se seznamem klipů (`start`, `end`, `kind`, `crop_cx`…).
Můžete ho upravit (posunout časy, smazat klip, změnit výřez) a poté:

```
reelcut render video_plan.json -o final.mp4 --transition fade
```

## Testy

```
pip install -e ".[dev]"
pytest
```

Integrační testy si vygenerují syntetické video pomocí ffmpeg (s řečí přes
`espeak-ng`, pokud je nainstalovaný) a prověří detekci střihů, řeči, tempa,
plán i render.

## Omezení a poznámky

- Detekce řeči bez `--transcribe` je heuristická; u hlasité hudby s vokálem
  může část zpěvu považovat za řeč (řešení: `--speech ignore` nebo
  `--vad-threshold 0.5`).
- Hodnocení „zajímavosti“ je signálové (pohyb, zvuk, lidé, ostrost), ne
  sémantické – nástroj neví, *o čem* se mluví. S přepisem lze v plánu podle
  textu ručně vybrat věty.
- `--fit crop` u horizontálního videa bez obličejů ořízne střed; pro záběry,
  kde je důležitý celý obraz, použijte `--fit blur`.
- Model YuNet (`reelcut/models/face_detection_yunet_2023mar.onnx`) pochází z
  OpenCV Zoo, licence Apache‑2.0.
