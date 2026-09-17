"""JSON schémata pro strukturované odpovědi Claude.

Používáme `output_config.format` (json_schema), takže odpověď je vždy validní
JSON v očekávaném tvaru a nemusíme ji parsovat z volného textu.
"""

FORMATS = ["REEL", "CAROUSEL", "IMAGE"]
HOOK_STYLES = ["otazka", "kontrarian", "cislo", "pribeh", "chyba", "navod", "srovnani"]
CTA_TYPES = ["uloz", "komentar", "sdilej", "sleduj", "odkaz_v_biu", "zadne"]
TEMPLATES = ["quote", "tip_list", "stat", "cover", "photo", "video"]

CONTENT_PLAN = {
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "Krátce (2–4 věty) proč právě tenhle mix vzhledem k datům.",
        },
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 14,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Pracovní název pro frontu."},
                    "series": {
                        "type": "string",
                        "description": "Klíč série, do které námět patří (přesně jak je "
                                       "zadaný v termínech). Prázdné jen u obsahu mimo série.",
                    },
                    "format": {"type": "string", "enum": FORMATS},
                    "template": {"type": "string", "enum": TEMPLATES},
                    "topic": {"type": "string"},
                    "pillar": {"type": "string"},
                    "hook_style": {"type": "string", "enum": HOOK_STYLES},
                    "cta_type": {"type": "string", "enum": CTA_TYPES},
                    "angle": {"type": "string", "description": "O čem to je, jednou větou."},
                    "key_points": {"type": "array", "items": {"type": "string"},
                                   "maxItems": 6},
                    "needs_user_media": {
                        "type": "boolean",
                        "description": "True, když to bez tvojí fotky/videa nejde vyrobit.",
                    },
                    "why": {"type": "string", "description": "Co z dat tenhle nápad opírá."},
                },
                "required": ["title", "series", "format", "template", "topic", "pillar",
                             "hook_style", "cta_type", "angle", "key_points",
                             "needs_user_media", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reasoning", "items"],
    "additionalProperties": False,
}

POST_CONTENT = {
    "type": "object",
    "properties": {
        "hook": {"type": "string", "description": "První řádek popisku, max ~60 znaků."},
        "caption": {"type": "string", "description": "Celý popisek včetně hooku, bez hashtagů."},
        "hashtags": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "first_comment": {"type": "string"},
        "alt_text": {"type": "string", "description": "Popis obrázku pro nevidomé, česky."},
        "graphic": {
            "type": "object",
            "description": "Texty do grafiky. Krátké — musí se vejít na plochu.",
            "properties": {
                "kicker": {"type": "string", "maxLength": 24},
                "title": {"type": "string", "maxLength": 90},
                "subtitle": {"type": "string", "maxLength": 110},
                "body_lines": {"type": "array", "items": {"type": "string", "maxLength": 95},
                               "maxItems": 6},
                "stat_value": {"type": "string", "maxLength": 12},
                "stat_label": {"type": "string", "maxLength": 70},
                "outro_headline": {"type": "string", "maxLength": 60},
                "outro_cta": {"type": "string", "maxLength": 80},
            },
            "required": ["kicker", "title", "subtitle", "body_lines", "stat_value",
                         "stat_label", "outro_headline", "outro_cta"],
            "additionalProperties": False,
        },
        "slides": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string", "maxLength": 70},
                    "body": {"type": "string", "maxLength": 190},
                },
                "required": ["heading", "body"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["hook", "caption", "hashtags", "first_comment", "alt_text", "graphic", "slides"],
    "additionalProperties": False,
}

REEL_SCRIPT = {
    "type": "object",
    "properties": {
        "hook_text": {"type": "string", "maxLength": 70,
                      "description": "Text do prvních ~2,5 s videa."},
        "cover_title": {"type": "string", "maxLength": 70},
        "beats": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": 90},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                },
                "required": ["text", "start", "end"],
                "additionalProperties": False,
            },
        },
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "target_seconds": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["hook_text", "cover_title", "beats", "caption", "hashtags",
                 "target_seconds", "notes"],
    "additionalProperties": False,
}

PROFILE_ANALYSIS = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Stav profilu v 3–5 větách, česky."},
        "working": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "not_working": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "audience_read": {"type": "string", "description": "Co data říkají o publiku."},
        "experiments": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "change": {"type": "string"},
                    "measure": {"type": "string"},
                },
                "required": ["hypothesis", "change", "measure"],
                "additionalProperties": False,
            },
        },
        "next_actions": {"type": "array", "items": {"type": "string"}, "maxItems": 7},
        "confidence": {"type": "string", "enum": ["nízká", "střední", "vysoká"],
                       "description": "Jak moc datům věřit vzhledem k velikosti vzorku."},
    },
    "required": ["summary", "working", "not_working", "audience_read", "experiments",
                 "next_actions", "confidence"],
    "additionalProperties": False,
}

IMAGE_QA = {
    "type": "object",
    "properties": {
        "readable": {"type": "boolean", "description": "Je text čitelný na mobilu?"},
        "text_overflow": {"type": "boolean", "description": "Přetéká nebo se ořezává text?"},
        "on_brand": {"type": "boolean"},
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "problems": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "verdict": {"type": "string", "enum": ["publikovat", "opravit", "zahodit"]},
    },
    "required": ["readable", "text_overflow", "on_brand", "score", "problems", "verdict"],
    "additionalProperties": False,
}

COMMENT_REPLIES = {
    "type": "object",
    "properties": {
        "replies": {
            "type": "array",
            "maxItems": 25,
            "items": {
                "type": "object",
                "properties": {
                    "comment_id": {"type": "string"},
                    "reply": {"type": "string", "maxLength": 300},
                    "action": {"type": "string", "enum": ["odpovedet", "ignorovat", "eskalovat"]},
                    "reason": {"type": "string"},
                },
                "required": ["comment_id", "reply", "action", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["replies"],
    "additionalProperties": False,
}


REPURPOSE = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "source_media_id": {"type": "string"},
                    "title": {"type": "string"},
                    "series": {"type": "string"},
                    "variant": {
                        "type": "string",
                        "enum": ["jiny_uhel", "jiny_format", "hlubsi", "prakticky",
                                 "anglicky"],
                        "description": "Jak se s námětem naloží podruhé.",
                    },
                    "language": {"type": "string", "enum": ["cs", "en"]},
                    "format": {"type": "string", "enum": FORMATS},
                    "hook_style": {"type": "string", "enum": HOOK_STYLES},
                    "angle": {"type": "string",
                              "description": "Čím se nová verze liší od původní."},
                    "key_points": {"type": "array", "items": {"type": "string"},
                                   "maxItems": 6},
                    "why": {"type": "string",
                            "description": "Proč právě tenhle námět stojí za zopakování."},
                },
                "required": ["source_media_id", "title", "series", "variant", "language",
                             "format", "hook_style", "angle", "key_points", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["variants"],
    "additionalProperties": False,
}
