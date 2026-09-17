"""Hledání fontů. Musí umět česká diakritická znaménka."""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

from ..util import get_logger

log = get_logger(__name__)

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"

# Pořadí = preference. Všechny uvedené mají plnou českou diakritiku.
CANDIDATES = {
    "bold": [
        "Inter-Bold.ttf", "Montserrat-Bold.ttf", "Poppins-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    "regular": [
        "Inter-Regular.ttf", "Montserrat-Regular.ttf", "Poppins-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    "mono": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ],
}

_cache = {}


def resolve(weight="bold", brand_fonts=None):
    """Vrátí cestu k fontu; `brand_fonts` je dict z brand.yaml (např. {'bold': '…'})."""
    key = (weight, str(sorted((brand_fonts or {}).items())))
    if key in _cache:
        return _cache[key]

    tries = []
    if brand_fonts and brand_fonts.get(weight):
        tries.append(brand_fonts[weight])
    tries.extend(CANDIDATES.get(weight, CANDIDATES["regular"]))

    for candidate in tries:
        path = Path(candidate)
        if not path.is_absolute():
            path = ASSETS / candidate
        if path.exists():
            _cache[key] = str(path)
            return str(path)

    log.warning("Nenašel jsem font pro '%s'. Nainstaluj DejaVu nebo vlož TTF do %s.", weight, ASSETS)
    _cache[key] = None
    return None


def load(weight="bold", size=48, brand_fonts=None):
    path = resolve(weight, brand_fonts)
    if path:
        return ImageFont.truetype(path, size=size)
    return ImageFont.load_default(size=size)


def supports_czech(font_path):
    """Rychlý test, jestli font umí ěščřžýáíé."""
    if not font_path:
        return False
    try:
        font = ImageFont.truetype(font_path, 24)
        return all(font.getmask(ch).getbbox() is not None for ch in "ěščřžýáíéůú")
    except Exception:  # noqa: BLE001 - diagnostika
        return False
