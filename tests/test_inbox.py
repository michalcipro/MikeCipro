"""Párování natočených souborů s náměty."""

import pytest

from igagent.config import Brand
from igagent.pipeline import Agent
from igagent.pipeline.inbox import Inbox
from igagent.store import QueueItem
from igagent.util import to_iso, utcnow
from tests.fakes import FakeBrain, FakeGraphClient

import datetime as dt


@pytest.fixture
def agent(settings, store):
    settings.brand = Brand.load("config/brand.yaml")
    settings.qa_images = False
    return Agent(settings, store=store, client=FakeGraphClient(), brain=FakeBrain())


@pytest.fixture
def inbox(agent):
    box = agent.planner.inbox
    box.ensure()
    return box


def _waiting_item(store, item_id_hint, title, days=1):
    return store.enqueue(QueueItem(
        format="REEL", series="tvrda_pravda", title=title,
        scheduled_for=to_iso(utcnow() + dt.timedelta(days=days)),
        brief={"needs_user_media": True}))


def _drop(inbox, name, content=b"video"):
    path = inbox.path / name
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------- názvy

@pytest.mark.parametrize("name,expected", [
    ("1-sebevedomi.mp4", 1),
    ("4_reset.mov", 4),
    ("7 wisconsin.mp4", 7),
    ("12.klid.mp4", 12),
    ("bez-cisla.mp4", None),
    ("IMG_2031.jpg", None),
])
def test_item_id_is_read_from_the_filename(name, expected):
    assert Inbox.item_id_from_name(name) == expected


# ---------------------------------------------------------------- párování

def test_files_are_linked_by_number(agent, inbox):
    first = _waiting_item(agent.store, 1, "První")
    second = _waiting_item(agent.store, 2, "Druhý", days=3)
    _drop(inbox, f"{second.id}-druhy.mp4")

    linked, leftover, waiting = inbox.link()
    assert [item.id for item, _ in linked] == [second.id]
    assert not leftover
    assert [item.id for item in waiting] == [first.id]
    assert agent.store.get_queue_item(second.id).source_media
    assert not agent.store.get_queue_item(first.id).source_media


def test_one_file_never_lands_on_two_items(agent, inbox):
    """Dřív plánovač přilepil jedno video ke všem Reels naráz."""
    items = [_waiting_item(agent.store, i, f"Námět {i}", days=i) for i in range(1, 4)]
    _drop(inbox, "jedno-video.mp4")

    agent.planner.available_media()          # nesmí nic připnout
    linked, leftover, waiting = inbox.link()

    assert linked == []
    assert len(leftover) == 1
    assert len(waiting) == 3
    assert all(not agent.store.get_queue_item(i.id).source_media for i in items)


def test_several_photos_can_share_one_item(agent, inbox):
    item = agent.store.enqueue(QueueItem(
        format="CAROUSEL", title="Karusel", brief={"needs_user_media": True}))
    for letter in "abc":
        _drop(inbox, f"{item.id}-{letter}.jpg")

    inbox.link()
    assert len(agent.store.get_queue_item(item.id).source_media) == 3


def test_auto_mode_fills_by_schedule_order(agent, inbox):
    later = _waiting_item(agent.store, 1, "Pozdější", days=5)
    sooner = _waiting_item(agent.store, 2, "Dřívější", days=1)
    _drop(inbox, "prvni.mp4")
    _drop(inbox, "druhy.mp4")

    linked, leftover, _ = inbox.link(auto=True)
    assert not leftover
    # nejbližší termín bere první soubor
    assert linked[0][0].id == sooner.id or linked[1][0].id == sooner.id
    assert agent.store.get_queue_item(sooner.id).source_media
    assert agent.store.get_queue_item(later.id).source_media


def test_dry_run_changes_nothing(agent, inbox):
    item = _waiting_item(agent.store, 1, "Námět")
    _drop(inbox, f"{item.id}-video.mp4")

    linked, _, _ = inbox.link(dry_run=True)
    assert linked
    assert not agent.store.get_queue_item(item.id).source_media


def test_already_attached_file_is_not_offered_again(agent, inbox):
    item = _waiting_item(agent.store, 1, "Námět")
    _drop(inbox, f"{item.id}-video.mp4")
    inbox.link()

    matches, leftover, waiting = inbox.plan_matches()
    assert not matches and not leftover


def test_missing_media_unblocks_a_failed_item(agent, inbox):
    item = _waiting_item(agent.store, 1, "Námět")
    item.status = "failed"
    item.error = "nemá zdrojové video"
    agent.store.update_queue(item)

    _drop(inbox, f"{item.id}-video.mp4")
    inbox.link()

    refreshed = agent.store.get_queue_item(item.id)
    assert refreshed.status == "planned"
    assert refreshed.error is None


# ---------------------------------------------------------------- úklid

def test_used_file_is_archived(agent, inbox):
    item = _waiting_item(agent.store, 1, "Námět")
    path = _drop(inbox, f"{item.id}-video.mp4")
    inbox.link()

    inbox.archive([str(path)])
    assert not path.exists()
    assert (inbox.archive_path / path.name).exists()
    assert inbox.files() == []


def test_archive_keeps_both_when_names_collide(agent, inbox):
    first = _drop(inbox, "1-video.mp4", b"a")
    inbox.archive([str(first)])
    second = _drop(inbox, "1-video.mp4", b"b")
    inbox.archive([str(second)])

    archived = sorted(p.name for p in inbox.archive_path.iterdir())
    assert archived == ["1-video-1.mp4", "1-video.mp4"]


def test_files_outside_the_inbox_are_left_alone(agent, inbox, tmp_path):
    outside = tmp_path / "moje-video.mp4"
    outside.write_bytes(b"video")
    inbox.archive([str(outside)])
    assert outside.exists()          # cizí složku agent neuklízí


def test_status_summarizes_everything(agent, inbox):
    item = _waiting_item(agent.store, 1, "Námět")
    _waiting_item(agent.store, 2, "Bez videa", days=4)
    _drop(inbox, f"{item.id}-video.mp4")
    _drop(inbox, "bez-cisla.mp4")

    status = inbox.status()
    assert status["souboru"] == 2
    assert status["prirazeno"] == {item.id: [f"{item.id}-video.mp4"]}
    assert status["bez_cisla"] == ["bez-cisla.mp4"]
    assert [i for i, _ in status["cekaji_na_video"]]
