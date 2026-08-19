import json
import time

import gateway


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "STATE_PATH", tmp_path / "jobs_state.json")
    with gateway.JOBS_LOCK:
        gateway.JOBS.clear()


def _job(**over):
    base = {
        "id": "j", "endpoint_id": "e", "status": "IN_PROGRESS", "input": {},
        "output": None, "error": None, "rp_job": None, "runpod_id": None,
        "submitted_at": 1.0, "started_at": 1.0, "completed_at": None,
    }
    base.update(over)
    return base


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    with gateway.JOBS_LOCK:
        gateway.JOBS["j1"] = _job(id="j1", status="COMPLETED", output={"x": 1})
        gateway._save_jobs()
        gateway.JOBS.clear()
    gateway._load_jobs()
    assert gateway.JOBS["j1"]["status"] == "COMPLETED"
    assert gateway.JOBS["j1"]["output"] == {"x": 1}


def test_load_marks_local_inflight_failed(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    (tmp_path / "jobs_state.json").write_text(
        json.dumps({"local1": _job(id="local1", endpoint_id="esmfold-local")})
    )
    gateway._load_jobs()
    assert gateway.JOBS["local1"]["status"] == "FAILED"
    assert "re-run" in gateway.JOBS["local1"]["error"]


def test_load_resumes_runpod_inflight(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(gateway, "_resume_runpod_job", lambda jid: calls.append(jid))
    (tmp_path / "jobs_state.json").write_text(
        json.dumps(
            {
                "rp1": _job(
                    id="rp1", endpoint_id="alphafold-local",
                    rp_job="abc-e1", runpod_id="n3tcpxdv3irr46",
                )
            }
        )
    )
    gateway._load_jobs()
    for _ in range(20):
        if calls:
            break
        time.sleep(0.02)
    assert calls == ["rp1"]
    # A resumable RunPod job is left in flight, not failed.
    assert gateway.JOBS["rp1"]["status"] == "IN_PROGRESS"


def test_finalize_completed_packages(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    monkeypatch.setattr(gateway, "package", lambda name, output: "BASE64ARCHIVE")
    with gateway.JOBS_LOCK:
        gateway.JOBS["c1"] = _job(id="c1")
    gateway._finalize_from_worker_data(
        "c1", {"status": "COMPLETED", "output": {"pdb": "X"}}, "generic"
    )
    assert gateway.JOBS["c1"]["status"] == "COMPLETED"
    assert gateway.JOBS["c1"]["output"]["archives"][0]["base64"] == "BASE64ARCHIVE"


def test_finalize_failed(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    with gateway.JOBS_LOCK:
        gateway.JOBS["f1"] = _job(id="f1")
    gateway._finalize_from_worker_data(
        "f1", {"status": "FAILED", "error": "boom"}, "generic"
    )
    assert gateway.JOBS["f1"]["status"] == "FAILED"
    assert gateway.JOBS["f1"]["error"] == "boom"
