"""Generování grafiky v barvách značky (Pillow).

Šablony jsou schválně jednoduché a čitelné na malém displeji:
  quote      — silná věta na plném pozadí
  tip_list   — číslovaný seznam (3–6 bodů)
  stat       — jedno velké číslo + kontext
  cover      — titulní slide karuselu / obálka Reelu
  slide      — vnitřní slide karuselu
  outro      — poslední slide s CTA
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ..errors import MediaError
from ..util import ensure_dir, get_logger, slugify
from . import fonts as fontlib

log = get_logger(__name__)

SIZES = {
    "square": (1080, 1080),
    "portrait": (1080, 1350),   # 4:5 — v feedu zabere nejvíc místa
    "story": (1080, 1920),      # 9:16 — stories a obálky Reelů
}


def hex_to_rgb(value, default=(0, 0, 0)):
    if not value:
        return default
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return default
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def mix(color_a, color_b, ratio):
    return tuple(int(a + (b - a) * ratio) for a, b in zip(color_a, color_b))


def with_alpha(color, alpha):
    return (*color[:3], int(alpha))


class GraphicsStudio:
    """Vyrábí obrázkové příspěvky podle brand kitu."""

    def __init__(self, brand, out_dir, seed=None):
        self.brand = brand
        self.out_dir = ensure_dir(out_dir)
        self.random = random.Random(seed)
        self.colors = {k: hex_to_rgb(v) for k, v in (brand.colors or {}).items()}
        self.bg = self.colors.get("bg", (14, 17, 22))
        self.bg_alt = self.colors.get("bg_alt", mix(self.bg, (255, 255, 255), 0.06))
        self.fg = self.colors.get("fg", (245, 247, 250))
        self.muted = self.colors.get("muted", mix(self.fg, self.bg, 0.45))
        self.accent = self.colors.get("accent", (255, 90, 54))
        self.accent_alt = self.colors.get("accent_alt", self.accent)
        self.brand_fonts = brand.fonts or {}

    # ------------------------------------------------------------ primitiva
    def font(self, weight, size):
        return fontlib.load(weight, size, self.brand_fonts)

    def _canvas(self, size="portrait", style="gradient"):
        width, height = SIZES.get(size, SIZES["portrait"]) if isinstance(size, str) else size
        img = Image.new("RGB", (width, height), self.bg)
        if style == "gradient":
            img = self._gradient(width, height)
        elif style == "accent":
            img = self._gradient(width, height, top=self.accent,
                                 bottom=mix(self.accent, self.bg, 0.72))
        elif style == "solid":
            img = Image.new("RGB", (width, height), self.bg)
        self._add_grain(img)
        return img

    def _gradient(self, width, height, top=None, bottom=None):
        top = top or self.bg_alt
        bottom = bottom or self.bg
        base = Image.new("RGB", (1, height))
        pixels = base.load()
        for y in range(height):
            ratio = (y / max(height - 1, 1)) ** 1.15
            pixels[0, y] = mix(top, bottom, ratio)
        img = base.resize((width, height), Image.BILINEAR)
        # jemná světelná skvrna v barvě akcentu
        glow = Image.new("RGB", (width, height), (0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        cx = int(width * self.random.uniform(0.15, 0.85))
        cy = int(height * self.random.uniform(0.05, 0.3))
        radius = int(width * 0.75)
        gdraw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                      fill=mix((0, 0, 0), self.accent, 0.22))
        glow = glow.filter(ImageFilter.GaussianBlur(radius * 0.35))
        return Image.blend(img, Image.blend(img, glow, 0.5), 0.55)

    def _add_grain(self, img, strength=6):
        """Lehký šum — brání pruhování gradientu při kompresi Instagramu."""
        width, height = img.size
        noise = Image.effect_noise((width, height), strength).convert("L")
        img.paste(Image.blend(img, Image.merge("RGB", (noise, noise, noise)), 0.035), (0, 0))

    # ------------------------------------------------------------ text
    def wrap(self, draw, text, font, max_width):
        lines, current = [], ""
        for word in str(text).split():
            probe = f"{current} {word}".strip()
            if draw.textlength(probe, font=font) <= max_width or not current:
                current = probe
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def fit_text(self, draw, text, weight, box, max_size=140, min_size=28, line_spacing=1.16):
        """Najde největší velikost písma, při které se text vejde do boxu."""
        box_w, box_h = box
        best = None
        low, high = min_size, max_size
        while low <= high:
            size = (low + high) // 2
            font = self.font(weight, size)
            lines = self.wrap(draw, text, font, box_w)
            line_h = size * line_spacing
            total = line_h * len(lines)
            too_wide = any(draw.textlength(line, font=font) > box_w for line in lines)
            if total <= box_h and not too_wide:
                best = (font, lines, size)
                low = size + 1
            else:
                high = size - 1
        if best is None:
            font = self.font(weight, min_size)
            best = (font, self.wrap(draw, text, font, box_w), min_size)
        return best

    def draw_block(self, draw, text, weight, xy, box, color=None, max_size=140,
                   min_size=28, line_spacing=1.16, align="left"):
        font, lines, size = self.fit_text(draw, text, weight, box, max_size, min_size, line_spacing)
        x, y = xy
        line_h = size * line_spacing
        for line in lines:
            width = draw.textlength(line, font=font)
            offset = 0
            if align == "center":
                offset = (box[0] - width) / 2
            elif align == "right":
                offset = box[0] - width
            draw.text((x + offset, y), line, font=font, fill=color or self.fg)
            y += line_h
        return y

    def _pill(self, draw, xy, text, size=34, fg=None, bg=None, padding=(26, 14)):
        font = self.font("bold", size)
        width = draw.textlength(text, font=font)
        x, y = xy
        box = (x, y, x + width + padding[0] * 2, y + size + padding[1] * 2)
        draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=bg or self.accent)
        draw.text((x + padding[0], y + padding[1] - size * 0.08), text, font=font,
                  fill=fg or self._contrast(bg or self.accent))
        return box

    def _contrast(self, color):
        luminance = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
        return (14, 17, 22) if luminance > 150 else (255, 255, 255)

    def _footer(self, img, draw, note=None):
        width, height = img.size
        margin = int(width * 0.075)
        font = self.font("bold", int(width * 0.028))
        handle = f"@{self.brand.handle}"
        draw.text((margin, height - margin - font.size), handle, font=font, fill=self.muted)
        if note:
            note_font = self.font("regular", int(width * 0.026))
            note_w = draw.textlength(note, font=note_font)
            draw.text((width - margin - note_w, height - margin - note_font.size), note,
                      font=note_font, fill=self.muted)
        if self.brand.logo_path and Path(self.brand.logo_path).exists():
            self._paste_logo(img, margin, height - margin - int(width * 0.05))

    def _paste_logo(self, img, x, y, height=None):
        logo = Image.open(self.brand.logo_path).convert("RGBA")
        target_h = height or int(img.size[0] * 0.05)
        ratio = target_h / logo.size[1]
        logo = logo.resize((int(logo.size[0] * ratio), target_h), Image.LANCZOS)
        img.paste(logo, (int(x), int(y)), logo)

    def _save(self, img, name, subdir=""):
        target = ensure_dir(self.out_dir / subdir) if subdir else self.out_dir
        path = target / f"{slugify(name)}.jpg"
        img.convert("RGB").save(path, "JPEG", quality=92, subsampling=0, optimize=True)
        log.info("Grafika uložena: %s (%dx%d)", path, *img.size)
        return path

    # ------------------------------------------------------------ šablony
    def quote(self, text, kicker=None, name=None, size="portrait", style="gradient"):
        img = self._canvas(size, style)
        draw = ImageDraw.Draw(img)
        width, height = img.size
        margin = int(width * 0.09)
        top = margin

        if kicker:
            box = self._pill(draw, (margin, top), kicker.upper(), size=int(width * 0.028))
            top = box[3] + int(height * 0.045)

        body_box = (width - margin * 2, int(height * 0.58))
        end = self.draw_block(draw, text, "bold", (margin, top), body_box,
                              max_size=int(width * 0.115), min_size=int(width * 0.04))
        # akcentová linka pod textem
        draw.rounded_rectangle((margin, end + int(height * 0.03), margin + int(width * 0.17),
                                end + int(height * 0.03) + 10), radius=5, fill=self.accent)
        self._footer(img, draw)
        return self._save(img, name or text[:40], "graphics")

    def tip_list(self, title, items, kicker=None, name=None, size="portrait"):
        if not items:
            raise MediaError("tip_list potřebuje aspoň jednu položku.")
        img = self._canvas(size, "gradient")
        draw = ImageDraw.Draw(img)
        width, height = img.size
        margin = int(width * 0.085)
        top = margin

        if kicker:
            box = self._pill(draw, (margin, top), kicker.upper(), size=int(width * 0.026))
            top = box[3] + int(height * 0.035)

        top = self.draw_block(draw, title, "bold", (margin, top),
                              (width - margin * 2, int(height * 0.24)),
                              max_size=int(width * 0.082), min_size=int(width * 0.04))
        top += int(height * 0.045)

        items = list(items)[:6]
        available = height - top - int(height * 0.14)
        row_h = available / len(items)
        num_font = self.font("bold", int(row_h * 0.34))
        for index, item in enumerate(items, start=1):
            y = top + (index - 1) * row_h
            badge = int(row_h * 0.46)
            draw.rounded_rectangle((margin, y, margin + badge, y + badge),
                                   radius=int(badge * 0.32), fill=self.accent)
            num = str(index)
            nw = draw.textlength(num, font=num_font)
            draw.text((margin + (badge - nw) / 2, y + (badge - num_font.size) / 2 - badge * 0.05),
                      num, font=num_font, fill=self._contrast(self.accent))
            self.draw_block(draw, str(item), "regular",
                            (margin + badge + int(width * 0.035), y),
                            (width - margin * 2 - badge - int(width * 0.035), row_h * 0.82),
                            color=self.fg, max_size=int(row_h * 0.3), min_size=int(width * 0.026))
        self._footer(img, draw, "ulož si to ↓")
        return self._save(img, name or title, "graphics")

    def stat(self, value, label, context=None, name=None, size="portrait"):
        img = self._canvas(size, "gradient")
        draw = ImageDraw.Draw(img)
        width, height = img.size
        margin = int(width * 0.09)

        self.draw_block(draw, str(value), "bold", (margin, int(height * 0.24)),
                        (width - margin * 2, int(height * 0.26)),
                        color=self.accent, max_size=int(width * 0.34),
                        min_size=int(width * 0.12), align="left")
        y = int(height * 0.54)
        y = self.draw_block(draw, label, "bold", (margin, y),
                            (width - margin * 2, int(height * 0.18)),
                            max_size=int(width * 0.075), min_size=int(width * 0.035))
        if context:
            self.draw_block(draw, context, "regular", (margin, y + int(height * 0.03)),
                            (width - margin * 2, int(height * 0.12)),
                            color=self.muted, max_size=int(width * 0.04),
                            min_size=int(width * 0.024))
        self._footer(img, draw)
        return self._save(img, name or f"{value}-{label}", "graphics")

    def cover(self, title, subtitle=None, kicker=None, name=None, size="portrait",
              background=None):
        """Titulní slide karuselu nebo obálka Reelu."""
        if background and Path(background).exists():
            img = self._photo_background(background, size)
        else:
            img = self._canvas(size, "gradient")
        draw = ImageDraw.Draw(img)
        width, height = img.size
        margin = int(width * 0.085)
        top = int(height * 0.13)

        if kicker:
            box = self._pill(draw, (margin, top), kicker.upper(), size=int(width * 0.027))
            top = box[3] + int(height * 0.04)

        top = self.draw_block(draw, title, "bold", (margin, top),
                              (width - margin * 2, int(height * 0.42)),
                              max_size=int(width * 0.125), min_size=int(width * 0.05))
        if subtitle:
            self.draw_block(draw, subtitle, "regular", (margin, top + int(height * 0.035)),
                            (width - margin * 2, int(height * 0.16)),
                            color=self.muted, max_size=int(width * 0.042),
                            min_size=int(width * 0.026))
        self._swipe_hint(img, draw)
        self._footer(img, draw)
        return self._save(img, name or title, "graphics")

    def slide(self, heading, body=None, index=None, total=None, name=None, size="portrait"):
        img = self._canvas(size, "solid" if (index or 0) % 2 == 0 else "gradient")
        draw = ImageDraw.Draw(img)
        width, height = img.size
        margin = int(width * 0.085)
        top = int(height * 0.14)

        if index:
            label = f"{index:02d}" + (f"/{total:02d}" if total else "")
            font = self.font("bold", int(width * 0.05))
            draw.text((margin, int(height * 0.075)), label, font=font, fill=self.accent)

        top = self.draw_block(draw, heading, "bold", (margin, top),
                              (width - margin * 2, int(height * 0.3)),
                              max_size=int(width * 0.095), min_size=int(width * 0.045))
        if body:
            self.draw_block(draw, body, "regular", (margin, top + int(height * 0.04)),
                            (width - margin * 2, int(height * 0.36)),
                            color=mix(self.fg, self.bg, 0.15),
                            max_size=int(width * 0.05), min_size=int(width * 0.026))
        if index and total and index < total:
            self._swipe_hint(img, draw)
        self._footer(img, draw)
        return self._save(img, name or f"slide-{index or 0}-{heading}", "graphics")

    def outro(self, headline, cta, name=None, size="portrait"):
        img = self._canvas(size, "accent")
        draw = ImageDraw.Draw(img)
        width, height = img.size
        margin = int(width * 0.09)
        text_color = self._contrast(self.accent)

        top = self.draw_block(draw, headline, "bold", (margin, int(height * 0.28)),
                              (width - margin * 2, int(height * 0.3)),
                              color=text_color, max_size=int(width * 0.11),
                              min_size=int(width * 0.05))
        self.draw_block(draw, cta, "regular", (margin, top + int(height * 0.045)),
                        (width - margin * 2, int(height * 0.18)),
                        color=mix(text_color, self.accent, 0.25),
                        max_size=int(width * 0.05), min_size=int(width * 0.028))
        font = self.font("bold", int(width * 0.038))
        draw.text((margin, height - int(height * 0.1)), f"@{self.brand.handle}",
                  font=font, fill=text_color)
        return self._save(img, name or headline, "graphics")

    def _swipe_hint(self, img, draw):
        width, height = img.size
        cx = width - int(width * 0.085)
        cy = height - int(height * 0.085)
        radius = int(width * 0.045)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                     fill=with_alpha(self.accent, 255))
        arrow = self.font("bold", int(radius * 1.1))
        text = "›"
        tw = draw.textlength(text, font=arrow)
        draw.text((cx - tw / 2, cy - arrow.size * 0.62), text, font=arrow,
                  fill=self._contrast(self.accent))

    def _photo_background(self, path, size="portrait"):
        target = SIZES.get(size, SIZES["portrait"]) if isinstance(size, str) else size
        from .photos import smart_crop  # lokální import, ať se moduly netočí dokola

        img = smart_crop(Image.open(path).convert("RGB"), target)
        img = ImageEnhance.Brightness(img).enhance(0.55)
        img = img.filter(ImageFilter.GaussianBlur(1.2))
        overlay = Image.new("RGB", img.size, self.bg)
        return Image.blend(img, overlay, 0.35)

    # ------------------------------------------------------------ karusel
    def carousel(self, spec, name=None, size="portrait"):
        """spec = {'cover': {...}, 'slides': [{...}], 'outro': {...}}"""
        paths = []
        cover = spec.get("cover") or {}
        total = len(spec.get("slides") or []) + 1 + (1 if spec.get("outro") else 0)
        paths.append(self.cover(cover.get("title", "Bez názvu"), cover.get("subtitle"),
                                cover.get("kicker"), name=f"{name or 'carousel'}-00",
                                size=size, background=cover.get("background")))
        for index, slide in enumerate(spec.get("slides") or [], start=2):
            paths.append(self.slide(slide.get("heading", ""), slide.get("body"),
                                    index=index - 1, total=total - 1,
                                    name=f"{name or 'carousel'}-{index:02d}", size=size))
        if spec.get("outro"):
            outro = spec["outro"]
            paths.append(self.outro(outro.get("headline", "Chceš víc?"),
                                    outro.get("cta", f"Sleduj @{self.brand.handle}"),
                                    name=f"{name or 'carousel'}-99", size=size))
        return paths

    def reel_cover(self, title, subtitle=None, background=None, name=None):
        return self.cover(title, subtitle, name=name or f"cover-{slugify(title)}",
                          size="story", background=background)


def contrast_ratio(color_a, color_b):
    """WCAG kontrast — používá se v testech čitelnosti šablon."""

    def luminance(color):
        channels = []
        for value in color[:3]:
            srgb = value / 255
            channels.append(srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    light, dark = sorted((luminance(color_a), luminance(color_b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)
