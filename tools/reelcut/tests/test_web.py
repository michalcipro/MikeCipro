"""Web UI API tests (Flask test client, real analysis + render on the synthetic video)."""

from __future__ import annotations

import io
import time

import pytest

from reelcut.ffmpeg import find_binary
from reelcut.web import create_app, parse_params

from sample_video import build_sample

pytestmark = pytest.mark.skipif(find_binary("ffmpeg") is None or find_binary("ffprobe") is None, reason="ffmpeg missing")

FAST = {"preset": "ultrafast", "crf": 30}


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    return build_sample(tmp_path_factory.mktemp("video") / "sample.mp4")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(tmp_path_factory.mktemp("work"))
    app.testing = True
    return app.test_client()


def _wait(client, jid, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{jid}").get_json()
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.5)
    raise AssertionError("job did not finish in time")


def test_parse_params_defaults_and_validation():
    p = parse_params({})
    assert p["target"] == 30.0 and p["style"] == "auto" and p["hook"] is True
    assert parse_params({"target": "500"})["target"] == 180.0
    assert parse_params({"hook": "false", "snap": 0})["hook"] is False
    with pytest.raises(ValueError):
        parse_params({"style": "nope"})


def test_index_and_capabilities(client):
    assert client.get("/").status_code == 200
    assert b"reelcut" in client.get("/").data
    caps = client.get("/api/capabilities").get_json()
    assert {"auto", "talk", "action", "vibe"} <= {s["name"] for s in caps["styles"]}


def test_bad_inputs(client):
    assert client.post("/api/sources/upload", data={}).status_code == 400
    assert client.post("/api/sources/path", json={"path": "/nope/missing.mp4"}).status_code == 404
    assert client.post("/api/jobs", json={"source_id": "missing"}).status_code == 404
    assert client.get("/api/jobs/missing").status_code == 404


def test_upload_job_and_rerender(client, sample):
    data = {"file": (io.BytesIO(sample.read_bytes()), "sample.mp4")}
    r = client.post("/api/sources/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    src = r.get_json()
    assert abs(src["duration"] - 24.0) < 0.3 and src["media_url"]
    assert client.get(src["media_url"], headers={"Range": "bytes=0-99"}).status_code == 206

    r = client.post("/api/jobs", json={"source_id": src["id"], "target": 12, "style": "auto", **FAST})
    assert r.status_code == 202
    job = _wait(client, r.get_json()["id"])
    assert job["state"] == "done", job
    assert job["output_url"] and job["timeline_url"] and job["plan_url"]
    assert any(c["kind"] == "speech" for c in job["plan"]["clips"])
    assert client.get(job["output_url"]).status_code == 200
    assert client.get(job["timeline_url"]).status_code == 200
    dl = client.get(job["output_url"] + "?download=1")
    assert "attachment" in dl.headers.get("Content-Disposition", "")

    # drop the first clip and re-render
    clips = job["plan"]["clips"][1:]
    r = client.post(f"/api/jobs/{job['id']}/rerender", json={"clips": clips, "params": {"transition": "fade", **FAST}})
    assert r.status_code == 202
    job2 = _wait(client, r.get_json()["id"])
    assert job2["state"] == "done", job2
    assert len(job2["plan"]["clips"]) == len(clips)
    assert job2["params"]["transition"] == "fade"
    assert client.post(f"/api/jobs/{job['id']}/rerender", json={"clips": []}).status_code == 400

    jobs = client.get("/api/jobs").get_json()
    assert len(jobs) >= 2 and jobs[0]["state"] == "done"


def test_path_source(client, sample):
    r = client.post("/api/sources/path", json={"path": str(sample)})
    assert r.status_code == 201
    body = r.get_json()
    assert body["uploaded"] is False and body["proxy"] == "none" and body["codec"] == "h264"
    assert client.get(f"/api/sources/{body['id']}").status_code == 200
    assert any(s["id"] == body["id"] for s in client.get("/api/sources").get_json())


def test_thumbnails_and_persistence(sample, tmp_path):
    import shutil
    from reelcut.web import Manager

    work = tmp_path / "work"
    app = create_app(work)
    app.testing = True
    c = app.test_client()
    src = c.post("/api/sources/path", json={"path": str(sample)}).get_json()
    r = c.post("/api/jobs", json={"source_id": src["id"], "target": 8, "hook": False, **FAST})
    job = _wait(c, r.get_json()["id"])
    assert job["state"] == "done", job
    assert job["thumbs"] and all(job["thumbs"]), job["thumbs"]
    assert c.get(job["thumbs"][0]).status_code == 200
    assert (work / "sources.json").is_file() and (work / "jobs" / job["id"] / "job.json").is_file()

    # a new manager (server restart) sees the same sources and finished jobs
    m2 = Manager(work)
    assert src["id"] in m2.sources
    restored = m2.jobs[job["id"]]
    assert restored.state == "done" and restored.plan and len(restored.plan.clips) == len(job["plan"]["clips"])
    app2 = create_app(work)
    app2.testing = True
    c2 = app2.test_client()
    assert c2.get(f"/api/jobs/{job['id']}").get_json()["output_url"]
    assert c2.get("/api/jobs").get_json()[0]["id"] == job["id"]


def test_proxy_for_non_h264_source(tmp_path):
    import subprocess
    from reelcut.ffmpeg import ffmpeg_bin

    src = tmp_path / "old.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-t", "3", "-i", "testsrc2=size=640x360:rate=25",
                    "-c:v", "mpeg4", "-q:v", "5", str(src)], check=True)
    app = create_app(tmp_path / "work")
    app.testing = True
    c = app.test_client()
    body = c.post("/api/sources/path", json={"path": str(src)}).get_json()
    assert body["codec"] == "mpeg4" and body["proxy"] == "pending"
    deadline = time.time() + 60
    while time.time() < deadline:
        body = c.get(f"/api/sources/{body['id']}").get_json()
        if body["proxy"] != "pending":
            break
        time.sleep(0.5)
    assert body["proxy"] == "ready", body
    media = c.get(body["media_url"])
    assert media.status_code == 200
    proxy = next(iter(app.extensions["reelcut_manager"].sources.values()))
    from reelcut.ffmpeg import probe as _probe
    assert _probe(proxy.proxy_path).video_codec == "h264"


def test_multi_source_job(client, sample, tmp_path):
    a = client.post("/api/sources/path", json={"path": str(sample)}).get_json()
    b = client.post("/api/sources/path", json={"path": str(sample)}).get_json()
    r = client.post("/api/jobs", json={"source_ids": [a["id"], b["id"]], "target": 14, "min_quality": 0, **FAST})
    assert r.status_code == 202
    job = _wait(client, r.get_json()["id"])
    assert job["state"] == "done", job
    assert len(job["sources"]) == 2 and job["download_name"].endswith("_+1_reel.mp4")
    assert all(c.get("source") for c in job["plan"]["clips"])
    assert client.get(job["output_url"]).status_code == 200
    assert client.post("/api/jobs", json={"source_ids": []}).status_code == 400
    listed = client.get("/api/jobs").get_json()[0]
    assert " + " in listed["source_name"]
