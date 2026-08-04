"""Tests for the antigen-validation scoring route.

The route proxies the gateway synchronously, so the interesting cases are the
gateway's shapes: the metrics live under output.raw because the gateway wraps
everything else in an archive, and an unreachable worker has to produce a
message that points at the ACG rule rather than a bare socket error.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app
from app.routers import binder_score


class _User:
    id = 1
    username = "tester"


@pytest.fixture
def client():
    # No context manager on purpose: app startup launches background monitor
    # threads that can only be started once per process, so entering the
    # TestClient a second time would raise. Same convention as the other
    # backend API tests.
    app.dependency_overrides[get_current_user] = lambda: _User()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _payload(**overrides):
    body = {
        "candidates": [{"id": "cand-1", "structure": "ATOM\n", "scores": {"pae": [[0.0]]}}],
        "primary_metric": "ipsae",
        "cutoff": 0.3,
    }
    body.update(overrides)
    return body


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient: one /run then a scripted /status."""

    def __init__(self, statuses, *, run_id="job-1", capture=None, raise_on_run=None):
        self._statuses = list(statuses)
        self._run_id = run_id
        self._capture = capture
        self._raise_on_run = raise_on_run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        if self._raise_on_run is not None:
            raise self._raise_on_run
        if self._capture is not None:
            self._capture.append({"url": url, "json": json})
        return _FakeResponse({"id": self._run_id, "status": "IN_QUEUE"})

    async def get(self, url, headers=None):
        return _FakeResponse(self._statuses.pop(0))


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _patch_client(monkeypatch, fake):
    monkeypatch.setattr(binder_score.httpx, "AsyncClient", lambda **kw: fake)
    monkeypatch.setattr(binder_score, "_POLL_INTERVAL_S", 0.0)


def test_metrics_lists_directions(client):
    body = client.get("/api/binder-score/metrics").json()
    assert body["default"] == "ipsae"
    by_name = {m["name"]: m["higher_is_better"] for m in body["metrics"]}
    assert by_name["ipsae"] is True
    # these two are lower-is-better, and the UI needs to know
    assert by_name["pae_interaction"] is False
    assert by_name["epitope_rmsd"] is False


def test_score_returns_the_workers_own_output(client, monkeypatch):
    worker_output = {
        "results": [{"id": "cand-1", "ok": True, "primary_value": 0.27, "passed": True}],
        "passed_ids": ["cand-1"],
    }
    _patch_client(
        monkeypatch,
        _FakeAsyncClient(
            [{"status": "IN_PROGRESS"}, {"status": "COMPLETED", "output": {
                "archives": [{"name": "job-1.tar.gz", "base64": "ignored"}],
                "raw": worker_output,
            }}]
        ),
    )
    body = client.post("/api/binder-score/score", json=_payload()).json()
    # the archive is not what the caller wants; the raw worker output is
    assert body == worker_output


def test_score_forwards_settings_to_the_worker(client, monkeypatch):
    captured: list[dict] = []
    _patch_client(
        monkeypatch,
        _FakeAsyncClient(
            [{"status": "COMPLETED", "output": {"raw": {"ok": True}}}], capture=captured
        ),
    )
    client.post(
        "/api/binder-score/score",
        json=_payload(
            epitope="A:1-6,A:10-12",
            reference_structure="ATOM\n",
            pae_cutoff=20.0,
        ),
    )
    sent = captured[0]["json"]["input"]
    assert sent["epitope"] == "A:1-6,A:10-12"
    assert sent["reference_structure"] == "ATOM\n"
    assert sent["pae_cutoff"] == 20.0
    assert sent["cutoff"] == 0.3
    assert sent["candidates"][0]["id"] == "cand-1"


def test_candidates_get_default_ids(client, monkeypatch):
    captured: list[dict] = []
    _patch_client(
        monkeypatch,
        _FakeAsyncClient(
            [{"status": "COMPLETED", "output": {"raw": {"ok": True}}}], capture=captured
        ),
    )
    client.post(
        "/api/binder-score/score",
        json={"candidates": [{"structure": "ATOM\n"}], "primary_metric": "ipsae"},
    )
    assert captured[0]["json"]["input"]["candidates"][0]["id"] == "candidate_1"


def test_unknown_metric_is_rejected_before_leaving_the_portal(client, monkeypatch):
    def explode(**kw):
        raise AssertionError("must not reach the gateway")

    monkeypatch.setattr(binder_score.httpx, "AsyncClient", explode)
    response = client.post("/api/binder-score/score", json=_payload(primary_metric="nope"))
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


def test_worker_failure_becomes_502(client, monkeypatch):
    _patch_client(
        monkeypatch,
        _FakeAsyncClient([{"status": "FAILED", "error": "numpy exploded"}]),
    )
    response = client.post("/api/binder-score/score", json=_payload())
    assert response.status_code == 502
    assert "numpy exploded" in response.json()["detail"]


def test_unreachable_gateway_mentions_the_acg_rule(client, monkeypatch):
    _patch_client(
        monkeypatch,
        _FakeAsyncClient([], raise_on_run=httpx.ConnectError("connection timed out")),
    )
    response = client.post("/api/binder-score/score", json=_payload())
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "18108" in detail
    assert "unreachable" in detail


def test_empty_candidate_list_is_a_422(client):
    response = client.post(
        "/api/binder-score/score", json={"candidates": [], "primary_metric": "ipsae"}
    )
    assert response.status_code == 422
