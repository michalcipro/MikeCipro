"""Zpracování fotek: chytrý ořez, jemné doladění, příprava pro feed."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from ..errors import MediaError
from ..util import ensure_dir, get_logger, slugify

log = get_logger(__name__)

RATIOS = {
    "portrait": (4, 5),      # doporučené pro feed — největší plocha
    "square": (1, 1),
    "landscape": (1.91, 1),
    "story": (9, 16),
}
TARGETS = {
    "portrait": (1080, 1350),
    "square": (1080, 1080),
    "landscape": (1080, 566),
    "story": (1080, 1920),
}


def _energy_map(img, downscale=64):
    """Hrubá mapa „zajímavosti" obrazu podle hran (náhrada saliency)."""
    small = img.convert("L").resize((downscale, downscale), Image.BILINEAR)
    edges = small.filter(ImageFilter.FIND_EDGES)
    pixels = edges.tobytes()          # L mód → 1 bajt na pixel
    return [list(pixels[row * downscale:(row + 1) * downscale]) for row in range(downscale)]


def smart_crop(img, target_size):
    """Ořízne na cílový poměr tak, aby v záběru zůstala nejvýraznější část.

    Hledá okno s nejvyšší energií hran — v praxi to drží v záběru obličeje,
    text a hlavní objekt místo slepého ořezu na střed.
    """
    target_w, target_h = target_size
    target_ratio = target_w / target_h
    width, height = img.size
    current_ratio = width / height

    if abs(current_ratio - target_ratio) < 0.01:
        return img.resize(target_size, Image.LANCZOS)

    grid = 64
    energy = _energy_map(img, grid)

    if current_ratio > target_ratio:            # moc široké → ořez do stran
        crop_w = int(round(height * target_ratio))
        window = max(1, int(crop_w / width * grid))
        col_sums = [sum(row[c] for row in energy) for c in range(grid)]
        best_start, best_score = 0, -1
        for start in range(0, grid - window + 1):
            score = sum(col_sums[start:start + window])
            if score > best_score:
                best_score, best_start = score, start
        left = int((best_start + window / 2) / grid * width - crop_w / 2)
        left = max(0, min(width - crop_w, left))
        box = (left, 0, left + crop_w, height)
    else:                                        # moc vysoké → ořez nahoře/dole
        crop_h = int(round(width / target_ratio))
        window = max(1, int(crop_h / height * grid))
        row_sums = [sum(row) for row in energy]
        best_start, best_score = 0, -1
        for start in range(0, grid - window + 1):
            score = sum(row_sums[start:start + window])
            # mírná preference horní části — tam bývá obličej / hlavní motiv
            score *= 1.0 + 0.12 * (1 - start / max(grid - window, 1))
            if score > best_score:
                best_score, best_start = score, start
        top = int((best_start + window / 2) / grid * height - crop_h / 2)
        top = max(0, min(height - crop_h, top))
        box = (0, top, width, top + crop_h)

    return img.crop(box).resize(target_size, Image.LANCZOS)


def blurred_pad(img, target_size, blur=45):
    """Alternativa k ořezu: obrázek se vejde celý, okolí je rozmazané pozadí."""
    target_w, target_h = target_size
    background = smart_crop(img.copy(), target_size).filter(ImageFilter.GaussianBlur(blur))
    background = ImageEnhance.Brightness(background).enhance(0.75)
    fitted = ImageOps.contain(img, target_size, Image.LANCZOS)
    background.paste(fitted, ((target_w - fitted.size[0]) // 2,
                              (target_h - fitted.size[1]) // 2))
    return background


def enhance(img, strength=1.0):
    """Jemné doladění — cílem je, aby to nevypadalo jako filtr."""
    img = ImageEnhance.Brightness(img).enhance(1 + 0.03 * strength)
    img = ImageEnhance.Contrast(img).enhance(1 + 0.10 * strength)
    img = ImageEnhance.Color(img).enhance(1 + 0.08 * strength)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.6, percent=int(55 * strength), threshold=3))
    return img


class PhotoStudio:
    def __init__(self, brand, out_dir):
        self.brand = brand
        self.out_dir = ensure_dir(out_dir)

    def _open(self, path):
        p = Path(path)
        if not p.exists():
            raise MediaError(f"Fotka neexistuje: {p}")
        img = Image.open(p)
        img = ImageOps.exif_transpose(img)       # respektuj rotaci z fotoaparátu
        return img.convert("RGB")

    def _save(self, img, name, subdir="photos"):
        target = ensure_dir(self.out_dir / subdir)
        path = target / f"{slugify(name)}.jpg"
        img.save(path, "JPEG", quality=92, subsampling=0, optimize=True)
        log.info("Fotka připravena: %s (%dx%d)", path, *img.size)
        return path

    def prepare(self, path, ratio="portrait", fit="crop", enhance_strength=1.0,
                watermark=None, name=None):
        """Jedna fotka → hotový příspěvek ve správném poměru."""
        img = self._open(path)
        target = TARGETS.get(ratio, TARGETS["portrait"])
        img = blurred_pad(img, target) if fit == "pad" else smart_crop(img, target)
        if enhance_strength:
            img = enhance(img, enhance_strength)
        text = watermark if watermark is not None else self.brand.watermark_text
        if text:
            img = self._watermark(img, text)
        return self._save(img, name or Path(path).stem)

    def prepare_many(self, paths, ratio="portrait", **kwargs):
        """Sada fotek do karuselu — všechny ve stejném poměru, aby feed seděl."""
        return [self.prepare(p, ratio=ratio, name=f"{Path(p).stem}-{i:02d}", **kwargs)
                for i, p in enumerate(paths, start=1)]

    def _watermark(self, img, text):
        from PIL import ImageDraw

        from . import fonts as fontlib
        from .graphics import hex_to_rgb

        draw = ImageDraw.Draw(img, "RGBA")
        width, height = img.size
        font = fontlib.load("bold", int(width * 0.026), self.brand.fonts or {})
        margin = int(width * 0.04)
        text_w = draw.textlength(text, font=font)
        box = (width - margin - text_w - 24, height - margin - font.size - 16,
               width - margin + 8, height - margin + 8)
        draw.rounded_rectangle(box, radius=12, fill=(0, 0, 0, 110))
        draw.text((box[0] + 12, box[1] + 8), text, font=font,
                  fill=hex_to_rgb((self.brand.colors or {}).get("fg"), (255, 255, 255)))
        return img

    def cover_frame(self, path, target="story", name=None):
        """Z fotky udělá 9:16 podklad (např. obálku Reelu)."""
        img = self._open(path)
        return self._save(smart_crop(img, TARGETS.get(target, TARGETS["story"])),
                          name or f"{Path(path).stem}-cover")
