"""Ověřuje tvar požadavků na Claude — bez volání sítě."""

import json
import types

import pytest

from igagent.brain import Brain
from igagent.brain import schemas
from igagent.errors import BrainError
from igagent.store import QueueItem


class StubMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload = payload
        self.stop_reason = stop_reason
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        text = json.dumps(self.payload, ensure_ascii=False)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text=text)],
            stop_reason=self.stop_reason,
            stop_details=None,
            usage=types.SimpleNamespace(input_tokens=10, output_tokens=5,
                                        cache_read_input_tokens=0))


class StubClient:
    def __init__(self, payload, stop_reason="end_turn"):
        self.messages = StubMessages(payload, stop_reason)


def _brain(settings, payload, stop_reason="end_turn"):
    client = StubClient(payload, stop_reason)
    return Brain(settings, client=client), client


def test_plan_request_shape(settings):
    payload = {"reasoning": "ok", "items": []}
    brain, client = _brain(settings, payload)
    brain.plan_content(3, strategy={"sample_size": 12})

    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["thinking"] == {"type": "adaptive"}
    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] is schemas.CONTENT_PLAN
    assert kwargs["output_config"]["effort"] == settings.effort
    # žádný `budget_tokens` — na Opus 5 by vrátil 400
    assert "budget_tokens" not in json.dumps(kwargs.get("thinking"))


def test_stable_context_is_marked_for_caching(settings):
    brain, client = _brain(settings, {"reasoning": "", "items": []})
    brain.plan_content(1, strategy={"sample_size": 5})

    system = client.messages.last_kwargs["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # brand kit i naučený profil patří do stabilní části, ne do zprávy
    assert "testbrand" in system[0]["text"]
    assert "sample_size" in system[0]["text"]


def test_image_is_sent_as_base64_block(settings, tmp_path):
    from PIL import Image

    path = tmp_path / "card.jpg"
    Image.new("RGB", (100, 100), "red").save(path)

    brain, client = _brain(settings, {"readable": True, "text_overflow": False,
                                      "on_brand": True, "score": 9, "problems": [],
                                      "verdict": "publikovat"})
    brain.qa_image(path, expected_text="Ahoj")

    content = client.messages.last_kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[0]["source"]["type"] == "base64"
    # na kontrolu obrázku stačí levnější model
    assert client.messages.last_kwargs["model"] == settings.model_fast


def test_write_post_passes_item_fields(settings):
    brain, client = _brain(settings, {
        "hook": "h", "caption": "c", "hashtags": [], "first_comment": "",
        "alt_text": "a", "graphic": {}, "slides": []})
    item = QueueItem(id=1, format="CAROUSEL", template="tip_list", topic="návyky",
                     pillar="Návody", hook_style="cislo", cta_type="uloz",
                     brief={"angle": "tři věci", "key_points": ["a", "b"]})
    brain.write_post(item)

    user_text = client.messages.last_kwargs["messages"][0]["content"][0]["text"]
    assert "CAROUSEL" in user_text and "tip_list" in user_text
    assert "tři věci" in user_text and "a; b" in user_text


def test_refusal_is_reported_clearly(settings):
    brain, _ = _brain(settings, {}, stop_reason="refusal")
    with pytest.raises(BrainError, match="odmítl"):
        brain.plan_content(1)


def test_invalid_json_is_reported_clearly(settings):
    brain, client = _brain(settings, {})

    def broken(**kwargs):
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="tohle není JSON")],
            stop_reason="end_turn", stop_details=None, usage=None)

    client.messages.create = broken
    with pytest.raises(BrainError, match="JSON"):
        brain.plan_content(1)


def test_schemas_are_strict_everywhere():
    """Každý objekt ve schématu musí zakazovat cizí klíče a mít `required`."""

    def check(node, path="root"):
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, f"{path}: chybí additionalProperties"
            assert set(node.get("required", [])) == set(node.get("properties", {})), \
                f"{path}: required neodpovídá properties"
        for key, value in (node.get("properties") or {}).items():
            check(value, f"{path}.{key}")
        if "items" in node:
            check(node["items"], f"{path}[]")

    for name in ("CONTENT_PLAN", "POST_CONTENT", "REEL_SCRIPT", "PROFILE_ANALYSIS",
                 "IMAGE_QA", "COMMENT_REPLIES"):
        check(getattr(schemas, name), name)
