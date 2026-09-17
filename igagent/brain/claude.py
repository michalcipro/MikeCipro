"""Vrstva nad Claude API.

Všechna volání jdou přes `_structured()`, takže odpovědi mají vždy pevný tvar
(`output_config.format` = json_schema). Stabilní část promptu (pravidla +
brand kit + strategický profil) je označená pro prompt caching — při běhu
každou hodinu se tak neplatí opakovaně za tentýž kontext.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from ..errors import BrainError
from ..util import get_logger
from . import prompts, schemas

log = get_logger(__name__)

MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp", ".gif": "image/gif"}


class Brain:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.brand = settings.brand
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic  # noqa: PLC0415 - ať se dá modul importovat i bez klíče

            kwargs = {"api_key": self.settings.anthropic_api_key} if self.settings.anthropic_api_key else {}
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    # ------------------------------------------------------------ jádro
    def _structured(self, system, user_blocks, schema, model=None, max_tokens=16000,
                    effort=None, cache_system=True):
        """Jedno volání Claude s vynuceným tvarem odpovědi."""
        system_blocks = [{"type": "text", "text": system}]
        if cache_system:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        if isinstance(user_blocks, str):
            user_blocks = [{"type": "text", "text": user_blocks}]

        try:
            response = self.client.messages.create(
                model=model or self.settings.model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=[{"role": "user", "content": user_blocks}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": effort or self.settings.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except Exception as exc:  # noqa: BLE001 - sjednotíme chyby SDK
            raise BrainError(f"Volání Claude selhalo: {exc}") from exc

        if getattr(response, "stop_reason", None) == "refusal":
            detail = getattr(response, "stop_details", None)
            raise BrainError(f"Claude požadavek odmítl ({detail}).")

        usage = getattr(response, "usage", None)
        if usage is not None:
            log.debug("Claude usage: in=%s cache_read=%s out=%s",
                      getattr(usage, "input_tokens", "?"),
                      getattr(usage, "cache_read_input_tokens", "?"),
                      getattr(usage, "output_tokens", "?"))

        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise BrainError("Claude nevrátil žádný text.")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise BrainError(f"Odpověď nešla načíst jako JSON: {text[:300]}") from exc

    def _context_block(self, strategy=None, extra=None):
        """Stabilní kontext: značka + naučený profil. Dává se do systémového promptu."""
        parts = [self.brand.prompt_block()]
        if strategy:
            parts.append("\n=== CO ÚČTU FUNGUJE (z vlastních dat) ===\n"
                         + json.dumps(strategy, ensure_ascii=False, indent=2, sort_keys=True))
        if extra:
            parts.append(extra)
        return "\n\n".join(parts)

    # ------------------------------------------------------------ plánování
    def plan_content(self, count, strategy=None, recent_posts=None, calendar_notes=None,
                     available_media=None):
        """Navrhne `count` konkrétních příspěvků."""
        system = prompts.PLANNER + "\n\n" + self._context_block(strategy)
        lines = [f"Navrhni {count} příspěvků na nejbližší dny."]
        if recent_posts:
            lines.append("\nCo už vyšlo nedávno (ať se neopakuješ):")
            for post in recent_posts[:15]:
                lines.append(f"- [{post.get('format')}] {(post.get('caption') or '')[:90]}"
                             f" | téma: {post.get('topic') or '?'}"
                             f" | skóre: {_fmt(post.get('score'))}")
        if available_media:
            lines.append("\nK dispozici mám tyhle soubory od majitele "
                         "(můžeš na ně stavět nápady):")
            lines.extend(f"- {m}" for m in available_media[:20])
        else:
            lines.append("\nŽádné fotky ani videa od majitele teď nemám — "
                         "návrhy s `needs_user_media: true` se zařadí jako čekající.")
        if calendar_notes:
            lines.append(f"\nPoznámky k období: {calendar_notes}")
        return self._structured(system, "\n".join(lines), schemas.CONTENT_PLAN)

    # ------------------------------------------------------------ psaní
    def write_post(self, item, strategy=None, reference_image=None):
        """Napíše popisek + texty do grafiky pro jednu položku fronty."""
        system = prompts.WRITER + "\n\n" + self._context_block(strategy)
        brief = item.brief or {}
        lines = [
            "Zadání příspěvku:",
            f"- formát: {item.format}",
            f"- šablona grafiky: {item.template}",
            f"- téma: {item.topic}",
            f"- pilíř: {item.pillar}",
            f"- typ hooku: {item.hook_style}",
            f"- typ CTA: {item.cta_type}",
            f"- úhel pohledu: {brief.get('angle', '')}",
        ]
        if brief.get("key_points"):
            lines.append("- body, které mají zaznít: "
                         + "; ".join(str(p) for p in brief["key_points"]))
        if item.format == "CAROUSEL":
            lines.append("\nVyplň `slides` (3–7 slidů). Každý slide = jedna myšlenka.")
        else:
            lines.append("\n`slides` nech prázdné.")
        if item.template == "stat":
            lines.append("Šablona `stat` potřebuje `stat_value` a `stat_label`. "
                         "Číslo použij jen takové, které vyplývá ze zadání.")
        if item.template == "tip_list":
            lines.append("Šablona `tip_list` potřebuje 3–6 krátkých `body_lines`.")

        blocks = [{"type": "text", "text": "\n".join(lines)}]
        if reference_image:
            blocks = [_image_block(reference_image),
                      {"type": "text", "text": "\n".join(lines)
                       + "\n\nPopisek piš k téhle fotce — popiš, co na ní opravdu je."}]
        return self._structured(system, blocks, schemas.POST_CONTENT)

    def write_reel(self, item, video_info=None, strategy=None, transcript=None):
        """Scénář Reelu: hook do obrazu, beaty s časy, popisek."""
        system = prompts.REEL_WRITER + "\n\n" + self._context_block(strategy)
        brief = item.brief or {}
        lines = [
            f"Téma: {item.topic} | pilíř: {item.pillar}",
            f"Úhel: {brief.get('angle', '')}",
            f"Typ hooku: {item.hook_style} | CTA: {item.cta_type}",
        ]
        if brief.get("key_points"):
            lines.append("Body: " + "; ".join(str(p) for p in brief["key_points"]))
        if video_info:
            lines.append(f"\nZdrojové video má {video_info.get('duration', 0):.0f} s, "
                         f"rozlišení {video_info.get('width')}×{video_info.get('height')}, "
                         f"zvuk: {'ano' if video_info.get('has_audio') else 'ne'}.")
        if transcript:
            lines.append(f"\nPřepis zvuku:\n{transcript[:6000]}")
        else:
            lines.append("\nPřepis nemám — beaty piš obecněji, ať sedí na jakýkoliv záběr.")
        return self._structured(system, "\n".join(lines), schemas.REEL_SCRIPT)

    # ------------------------------------------------------------ analýza
    def analyze_profile(self, snapshot, posts, strategy=None, comments=None, period_days=28):
        """Souhrnná analýza profilu za období."""
        system = prompts.ANALYST + "\n\n" + self._context_block(strategy)
        payload = {
            "obdobi_dni": period_days,
            "ucet": snapshot,
            "prispevky": posts[:60],
            "ukazky_komentaru": (comments or [])[:40],
        }
        user = ("Tady jsou data z účtu. Napiš analýzu.\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return self._structured(system, user, schemas.PROFILE_ANALYSIS, max_tokens=16000)

    def qa_image(self, image_path, expected_text=None):
        """Vizuální kontrola vyrobené grafiky před publikací."""
        system = prompts.QA + "\n\n" + self._context_block()
        text = "Zkontroluj tuhle grafiku."
        if expected_text:
            text += f"\n\nMá na ní být tenhle text:\n{expected_text}"
        blocks = [_image_block(image_path), {"type": "text", "text": text}]
        return self._structured(system, blocks, schemas.IMAGE_QA,
                                model=self.settings.model_fast, max_tokens=4000,
                                effort="medium")

    def draft_comment_replies(self, comments, strategy=None):
        """Návrhy odpovědí na komentáře (publikace je vždy na schválení)."""
        system = prompts.COMMENTER + "\n\n" + self._context_block(strategy)
        payload = [{"comment_id": c.get("comment_id"), "username": c.get("username"),
                    "text": c.get("text")} for c in comments]
        user = ("Navrhni, co s těmito komentáři:\n"
                + json.dumps(payload, ensure_ascii=False, indent=2))
        return self._structured(system, user, schemas.COMMENT_REPLIES,
                                model=self.settings.model_fast, effort="medium")


def _image_block(path):
    path = Path(path)
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if not media_type:
        raise BrainError(f"Nepodporovaný typ obrázku: {path.suffix}")
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data}}


def _fmt(value):
    return "—" if value is None else f"{float(value):.0f}"
