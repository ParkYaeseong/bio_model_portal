import gateway


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise gateway.httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stands in for httpx.Client(...) as a context manager."""

    def __init__(self, post_fn=None, get_fn=None):
        self._post_fn = post_fn
        self._get_fn = get_fn
        self.calls: list[tuple[str, str, dict | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, **kwargs):
        self.calls.append(("POST", url, json))
        return self._post_fn(url, json)

    def get(self, url, params=None, **kwargs):
        self.calls.append(("GET", url, params))
        return self._get_fn(url, params)


def test_local_submit_returns_the_run_response_body(monkeypatch):
    client = _FakeClient(post_fn=lambda url, json: _Response({"id": "j1", "status": "PENDING"}))
    monkeypatch.setattr(gateway.httpx, "Client", lambda timeout=None: client)

    result = gateway._local_submit("http://worker.example:1", {"sequence": "AC"}, "j1")

    assert result == {"id": "j1", "status": "PENDING"}
    assert client.calls[0][0] == "POST"
    assert client.calls[0][1] == "http://worker.example:1/run"


def test_local_submit_raises_with_worker_body_on_http_error(monkeypatch):
    client = _FakeClient(post_fn=lambda url, json: _Response({"ok": False, "error": "bad input"}, status_code=400))
    monkeypatch.setattr(gateway.httpx, "Client", lambda timeout=None: client)

    try:
        gateway._local_submit("http://worker.example:1", {}, "j1")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "400" in str(exc)
        assert "bad input" in str(exc)


def test_local_poll_returns_immediately_on_completed(monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_POLL_INTERVAL_S", 0.0)
    client = _FakeClient(
        get_fn=lambda url, params: _Response({"id": "j1", "status": "COMPLETED", "output": {"x": 1}})
    )
    monkeypatch.setattr(gateway.httpx, "Client", lambda timeout=None: client)

    result = gateway._local_poll("http://worker.example:1", "j1")

    assert result["status"] == "COMPLETED"
    assert result["output"]["x"] == 1
    assert client.calls[0] == ("GET", "http://worker.example:1/status", {"id": "j1"})


def test_local_poll_keeps_polling_until_terminal(monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_POLL_INTERVAL_S", 0.0)
    responses = iter(
        [
            _Response({"id": "j1", "status": "RUNNING"}),
            _Response({"id": "j1", "status": "RUNNING"}),
            _Response({"id": "j1", "status": "COMPLETED", "output": {"ok": True}}),
        ]
    )
    client = _FakeClient(get_fn=lambda url, params: next(responses))
    monkeypatch.setattr(gateway.httpx, "Client", lambda timeout=None: client)

    result = gateway._local_poll("http://worker.example:1", "j1")

    assert result["status"] == "COMPLETED"
    assert len(client.calls) == 3


def test_local_poll_returns_failed_status_for_finalize_to_handle(monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_POLL_INTERVAL_S", 0.0)
    client = _FakeClient(
        get_fn=lambda url, params: _Response({"id": "j1", "status": "FAILED", "error": "boom exit=1"})
    )
    monkeypatch.setattr(gateway.httpx, "Client", lambda timeout=None: client)

    result = gateway._local_poll("http://worker.example:1", "j1")

    assert result["status"] == "FAILED"
    assert result["error"] == "boom exit=1"


def test_local_poll_raises_when_job_not_found(monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_POLL_INTERVAL_S", 0.0)
    client = _FakeClient(get_fn=lambda url, params: _Response({"id": "j1", "status": "NOT_FOUND"}))
    monkeypatch.setattr(gateway.httpx, "Client", lambda timeout=None: client)

    try:
        gateway._local_poll("http://worker.example:1", "j1")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "not found" in str(exc)


def test_local_poll_survives_transient_network_errors(monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_POLL_INTERVAL_S", 0.0)
    attempts = {"n": 0}

    def flaky_get(url, params):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise gateway.httpx.ConnectError("connection reset")
        return _Response({"id": "j1", "status": "COMPLETED", "output": {"ok": True}})

    client = _FakeClient(get_fn=flaky_get)
    monkeypatch.setattr(gateway.httpx, "Client", lambda timeout=None: client)

    result = gateway._local_poll("http://worker.example:1", "j1")

    assert result["output"]["ok"] is True
    assert attempts["n"] == 3


def test_local_poll_gives_up_after_too_many_network_errors(monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_POLL_INTERVAL_S", 0.0)

    def always_fails(url, params):
        raise gateway.httpx.ConnectError("connection reset")

    client = _FakeClient(get_fn=always_fails)
    monkeypatch.setattr(gateway.httpx, "Client", lambda timeout=None: client)

    try:
        gateway._local_poll("http://worker.example:1", "j1")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "repeatedly" in str(exc)


def test_local_poll_times_out_using_the_overall_budget(monkeypatch):
    monkeypatch.setattr(gateway, "LOCAL_POLL_TIMEOUT_S", 0)
    monkeypatch.setattr(gateway, "LOCAL_POLL_INTERVAL_S", 0.0)
    client = _FakeClient(get_fn=lambda url, params: _Response({"id": "j1", "status": "RUNNING"}))
    monkeypatch.setattr(gateway.httpx, "Client", lambda timeout=None: client)

    try:
        gateway._local_poll("http://worker.example:1", "j1")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "timed out" in str(exc)


def _reset_job(job_id, endpoint_id="ep1", worker_url="http://worker.example:1"):
    gateway.ENDPOINTS[endpoint_id] = {
        "worker_url": worker_url,
        "runpod_endpoint_id": "",
        "packager": "generic",
        "adapter": "",
    }
    with gateway.JOBS_LOCK:
        gateway.JOBS[job_id] = {
            "id": job_id, "endpoint_id": endpoint_id, "status": "IN_QUEUE",
            "input": {"sequence": "AC"}, "output": None, "error": None,
            "rp_job": None, "runpod_id": None,
            "submitted_at": 0.0, "started_at": None, "completed_at": None,
        }


def test_run_job_polls_when_the_worker_answers_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway, "STATE_PATH", tmp_path / "jobs_state.json")
    monkeypatch.setattr(gateway, "LOCAL_POLL_INTERVAL_S", 0.0)
    _reset_job("pending-job")

    monkeypatch.setattr(gateway, "_local_submit", lambda worker_url, adapted, job_id: {"id": job_id, "status": "PENDING"})
    monkeypatch.setattr(
        gateway, "_local_poll",
        lambda worker_url, job_id: {"id": job_id, "status": "COMPLETED", "output": {"ranked_0_pdb": "ATOM"}},
    )

    gateway._run_job("pending-job")

    job = gateway.JOBS["pending-job"]
    assert job["status"] == "COMPLETED"
    assert job["output"]["raw"]["ranked_0_pdb"] == "ATOM"


def test_run_job_skips_polling_when_the_worker_answers_synchronously(monkeypatch, tmp_path):
    # Backward compatibility: a worker not yet migrated to the async pattern
    # still answers /run with the final result directly.
    monkeypatch.setattr(gateway, "STATE_PATH", tmp_path / "jobs_state.json")
    _reset_job("sync-job")

    monkeypatch.setattr(
        gateway, "_local_submit",
        lambda worker_url, adapted, job_id: {"id": job_id, "status": "COMPLETED", "output": {"ranked_0_pdb": "ATOM"}},
    )

    def _poll_must_not_be_called(worker_url, job_id):
        raise AssertionError("polling must not happen for a synchronously-completed run")

    monkeypatch.setattr(gateway, "_local_poll", _poll_must_not_be_called)

    gateway._run_job("sync-job")

    job = gateway.JOBS["sync-job"]
    assert job["status"] == "COMPLETED"
    assert job["output"]["raw"]["ranked_0_pdb"] == "ATOM"


def test_run_job_marks_failed_when_polling_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway, "STATE_PATH", tmp_path / "jobs_state.json")
    _reset_job("fail-job")

    monkeypatch.setattr(gateway, "_local_submit", lambda worker_url, adapted, job_id: {"id": job_id, "status": "PENDING"})

    def _boom(worker_url, job_id):
        raise RuntimeError("worker job fail-job timed out after 21600s")

    monkeypatch.setattr(gateway, "_local_poll", _boom)

    gateway._run_job("fail-job")

    job = gateway.JOBS["fail-job"]
    assert job["status"] == "FAILED"
    assert "timed out" in job["error"]
