import pytest

from igagent.errors import MediaError, PublishBlocked
from igagent.instagram import Publisher
from igagent.instagram.hosting import LocalHost
from tests.fakes import FakeGraphClient


@pytest.fixture
def publisher(settings, tmp_path):
    client = FakeGraphClient()
    host = LocalHost(tmp_path / "public", "https://example.test/media")
    return Publisher(client, settings, host=host), client


def _image(tmp_path, name="a.jpg"):
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (1080, 1350), "navy").save(path)
    return path


def test_caption_merges_hashtags_without_duplicates():
    out = Publisher.prepare_caption("Ahoj #test světe", ["#test", "novy", "#test"])
    assert out.count("#test") == 1
    assert "#novy" in out


def test_caption_drops_hashtags_before_truncating_text():
    text = "x" * 2180
    out = Publisher.prepare_caption(text, ["#aaaa", "#bbbb", "#cccc"])
    assert len(out) <= 2200
    assert out.startswith(text)


def test_caption_is_truncated_when_body_alone_is_too_long():
    out = Publisher.prepare_caption("slovo " * 800, [])
    assert len(out) <= 2200
    assert out.endswith("…")


def test_publish_image_uses_two_phase_flow(publisher, tmp_path):
    pub, client = publisher
    result = pub.publish_image(_image(tmp_path), caption="Ahoj", hashtags=["#x"],
                               alt_text="popis")
    assert result.media_id in client.media_store
    container = client.containers[result.container_id]
    assert container["image_url"].startswith("https://example.test/media/")
    assert container["alt_text"] == "popis"
    assert "#x" in container["caption"]


def test_publish_reel_waits_for_processing(publisher, tmp_path):
    pub, client = publisher
    video = tmp_path / "v.mp4"
    video.write_bytes(b"not-really-a-video")
    result = pub.publish_reel(video, caption="Reel", cover_path=_image(tmp_path, "c.jpg"))
    container = client.containers[result.container_id]
    assert container["media_type"] == "REELS"
    assert container["share_to_feed"] == "true"
    assert container["cover_url"].endswith(".jpg")


def test_carousel_requires_two_to_ten_items(publisher, tmp_path):
    pub, _ = publisher
    with pytest.raises(MediaError):
        pub.publish_carousel([_image(tmp_path)])
    with pytest.raises(MediaError):
        pub.publish_carousel([_image(tmp_path, f"{i}.jpg") for i in range(11)])


def test_carousel_builds_children_then_parent(publisher, tmp_path):
    pub, client = publisher
    paths = [_image(tmp_path, f"c{i}.jpg") for i in range(3)]
    result = pub.publish_carousel(paths, caption="Karusel")
    parent = client.containers[result.container_id]
    assert parent["media_type"] == "CAROUSEL"
    assert len(parent["children"].split(",")) == 3


def test_quota_exhaustion_blocks_publishing(publisher, tmp_path):
    pub, client = publisher
    client.publishing_limit = lambda: {"used": 50, "total": 50}
    with pytest.raises(PublishBlocked):
        pub.publish_image(_image(tmp_path))


def test_dry_run_never_touches_the_api(settings, tmp_path):
    client = FakeGraphClient()
    pub = Publisher(client, settings, dry_run=True)
    result = pub.publish_image(_image(tmp_path), caption="Ahoj")
    assert result.dry_run and result.media_id is None
    assert client.published == []
