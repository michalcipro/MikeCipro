from PIL import Image

from igagent.media.graphics import GraphicsStudio, contrast_ratio, hex_to_rgb
from igagent.media.photos import PhotoStudio, smart_crop


def test_hex_parsing():
    assert hex_to_rgb("#FF5A36") == (255, 90, 54)
    assert hex_to_rgb("f00") == (255, 0, 0)
    assert hex_to_rgb("nesmysl", (1, 2, 3)) == (1, 2, 3)


def test_templates_render_at_feed_size(brand, tmp_path):
    studio = GraphicsStudio(brand, tmp_path, seed=1)
    outputs = [
        studio.quote("Krátká věta s diakritikou: příliš žluťoučký kůň.", kicker="Test"),
        studio.tip_list("Tři tipy", ["první", "druhý", "třetí"]),
        studio.stat("47 %", "lidí to vzdá", "kontext"),
        studio.cover("Titulek karuselu", "podtitulek", "kicker"),
        studio.outro("Ulož si to", "@testbrand"),
    ]
    for path in outputs:
        assert path.exists()
        assert Image.open(path).size == (1080, 1350)


def test_long_text_shrinks_instead_of_overflowing(brand, tmp_path):
    studio = GraphicsStudio(brand, tmp_path, seed=1)
    short = studio.quote("Krátce.", name="short")
    long = studio.quote("Tohle je výrazně delší věta, " * 6, name="long")
    assert short.exists() and long.exists()

    from PIL import ImageDraw

    img = Image.new("RGB", (1080, 1350))
    draw = ImageDraw.Draw(img)
    _, _, big = studio.fit_text(draw, "Krátce.", "bold", (900, 780))
    _, lines, small = studio.fit_text(draw, "Tohle je výrazně delší věta, " * 6,
                                      "bold", (900, 780))
    assert small < big                       # dlouhý text = menší písmo
    assert all(draw.textlength(line, font=studio.font("bold", small)) <= 900
               for line in lines)            # a nic nepřetéká


def test_carousel_has_cover_slides_and_outro(brand, tmp_path):
    studio = GraphicsStudio(brand, tmp_path, seed=2)
    paths = studio.carousel({
        "cover": {"title": "Titul", "subtitle": "pod", "kicker": "k"},
        "slides": [{"heading": "A", "body": "text"}, {"heading": "B", "body": "text"}],
        "outro": {"headline": "Konec", "cta": "sleduj"},
    }, name="c")
    assert len(paths) == 4
    assert all(p.exists() for p in paths)


def test_brand_colors_meet_contrast_guidance(brand):
    fg = hex_to_rgb(brand.colors["fg"])
    bg = hex_to_rgb(brand.colors["bg"])
    assert contrast_ratio(fg, bg) >= 4.5      # WCAG AA pro běžný text


def test_smart_crop_keeps_the_busy_side():
    img = Image.new("RGB", (2000, 1000), "black")
    # detail jen v pravé části — chytrý ořez ho musí udržet v záběru
    for x in range(1500, 1900, 4):
        for y in range(300, 700, 4):
            img.putpixel((x, y), (255, 255, 255))
    cropped = smart_crop(img, (1080, 1350))
    assert cropped.size == (1080, 1350)
    gray = cropped.convert("L")
    assert sum(gray.tobytes()) > 0           # bílá oblast zůstala uvnitř


def test_photo_studio_outputs_feed_ratio(brand, tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (3000, 2000), "darkgreen").save(src)
    studio = PhotoStudio(brand, tmp_path / "out")
    out = studio.prepare(src, ratio="portrait", watermark="@testbrand")
    assert Image.open(out).size == (1080, 1350)
