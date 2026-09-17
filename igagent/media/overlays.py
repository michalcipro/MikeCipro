"""Textové vrstvy pro video se kreslí v Pillow a do videa se vkládají jako PNG.

Proč ne ffmpeg `drawtext`: ten vyžaduje ffmpeg přeložený s libfreetype, což
spousta buildů (včetně statické binárky z `imageio-ffmpeg`) nemá. Kreslení
v Pillow navíc dá stejnou typografii jako u grafiky do feedu — stejný font,
stejné barvy, zaoblené podklady.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..util import ensure_dir, short_hash
from . import fonts as fontlib
from .graphics import hex_to_rgb


class TextOverlayRenderer:
    """Vyrábí průhledné PNG ve velikosti videa s vypáleným textem."""

    def __init__(self, brand, work_dir, size=(1080, 1920)):
        self.brand = brand
        self.work_dir = ensure_dir(work_dir)
        self.size = size
        colors = brand.colors or {}
        self.fg = hex_to_rgb(colors.get("fg"), (255, 255, 255))
        self.accent = hex_to_rgb(colors.get("accent"), (255, 90, 54))
        self.fonts = brand.fonts or {}

    # ------------------------------------------------------------ pomocné
    def _wrap(self, draw, text, font, max_width):
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

    def _fit(self, draw, text, weight, max_width, max_size, min_size=28, max_lines=4):
        size = max_size
        while size > min_size:
            font = fontlib.load(weight, size, self.fonts)
            lines = self._wrap(draw, text, font, max_width)
            if len(lines) <= max_lines:
                return font, lines, size
            size -= 4
        font = fontlib.load(weight, min_size, self.fonts)
        return font, self._wrap(draw, text, font, max_width), min_size

    # ------------------------------------------------------------ vrstvy
    def text_layer(self, text, y_ratio=0.5, weight="bold", max_size=86, min_size=34,
                   max_lines=4, color=None, box=True, box_color=(0, 0, 0), box_alpha=150,
                   accent_bar=False, margin_ratio=0.09, name=None):
        width, height = self.size
        img = Image.new("RGBA", self.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        margin = int(width * margin_ratio)
        max_width = width - margin * 2

        font, lines, size = self._fit(draw, text, weight, max_width, max_size, min_size, max_lines)
        line_h = int(size * 1.22)
        block_h = line_h * len(lines)
        top = int(height * y_ratio - block_h / 2)

        if box:
            pad_x, pad_y = int(size * 0.42), int(size * 0.34)
            widest = max((draw.textlength(line, font=font) for line in lines), default=0)
            box_w = min(max_width + pad_x, widest + pad_x * 2)
            left = (width - box_w) / 2
            draw.rounded_rectangle(
                (left, top - pad_y, left + box_w, top + block_h + pad_y),
                radius=int(size * 0.32), fill=(*box_color[:3], box_alpha))

        y = top
        for line in lines:
            line_w = draw.textlength(line, font=font)
            draw.text(((width - line_w) / 2, y), line, font=font,
                      fill=(*(color or self.fg)[:3], 255))
            y += line_h

        if accent_bar:
            bar_w = int(width * 0.16)
            bar_y = top + block_h + int(size * 0.7)
            draw.rounded_rectangle(((width - bar_w) / 2, bar_y,
                                    (width + bar_w) / 2, bar_y + 10),
                                   radius=5, fill=(*self.accent, 255))
        return self._save(img, name or f"text-{short_hash(text, y_ratio, max_size)}")

    def handle_layer(self, y_ratio=0.93, size=38, name=None):
        width, height = self.size
        img = Image.new("RGBA", self.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font = fontlib.load("bold", size, self.fonts)
        text = f"@{self.brand.handle}"
        text_w = draw.textlength(text, font=font)
        x, y = (width - text_w) / 2, height * y_ratio
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 140))
        draw.text((x, y), text, font=font, fill=(*self.fg, 215))
        return self._save(img, name or f"handle-{short_hash(self.brand.handle, y_ratio)}")

    def progress_layer(self, ratio, name=None):
        """Tenký proužek postupu dole — drží diváka u videa."""
        width, height = self.size
        img = Image.new("RGBA", self.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bar_y = height - 28
        draw.rounded_rectangle((0, bar_y, width, bar_y + 8), radius=4, fill=(255, 255, 255, 60))
        draw.rounded_rectangle((0, bar_y, max(8, width * ratio), bar_y + 8), radius=4,
                               fill=(*self.accent, 235))
        return self._save(img, name or f"progress-{int(ratio * 100)}")

    def _save(self, img, name):
        path = self.work_dir / f"{name}.png"
        img.save(path, "PNG")
        return path
