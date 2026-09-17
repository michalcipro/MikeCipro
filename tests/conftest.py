import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igagent.config import Brand, Settings  # noqa: E402
from igagent.store import Store  # noqa: E402


@pytest.fixture
def brand():
    return Brand(name="TestBrand", handle="testbrand",
                 posting_windows=[9, 12, 18], weekly_post_target=5,
                 format_mix={"REEL": 0.5, "CAROUSEL": 0.3, "IMAGE": 0.2})


@pytest.fixture
def settings(tmp_path, brand):
    s = Settings(
        ig_user_id="17841400000000000",
        ig_access_token="TEST_TOKEN",
        anthropic_api_key="sk-test",
        media_public_base="https://example.test/media",
        data_dir=tmp_path / "data",
        work_dir=tmp_path / "work",
        out_dir=tmp_path / "out",
        reports_dir=tmp_path / "reports",
        db_path=tmp_path / "data" / "test.sqlite3",
        brand=brand,
        autopilot="review",
        qa_images=False,
    )
    return s.ensure_dirs()


@pytest.fixture
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()
