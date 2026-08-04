"""The endpoint registry is pure config, so a typo is only caught by a test.

Checks that every declared endpoint resolves to a real packager and adapter, and
pins the binder-score entry used by antigen validation.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from adapters import ADAPTERS
from packagers import PACKAGERS

_CONFIG = Path(__file__).resolve().parents[1] / "endpoints.yaml"


def _endpoints() -> dict:
    loaded = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    endpoints = loaded.get("endpoints")
    assert isinstance(endpoints, dict) and endpoints
    return endpoints


def test_every_endpoint_names_a_real_packager_and_adapter():
    for name, entry in _endpoints().items():
        assert entry.get("packager") in PACKAGERS, f"{name}: unknown packager"
        # adapter is optional: the gateway defaults it to "" meaning no
        # adaptation, which is how phastest-local is declared.
        adapter = entry.get("adapter")
        if adapter:
            assert adapter in ADAPTERS, f"{name}: unknown adapter {adapter!r}"


def test_every_endpoint_has_a_worker_url_or_a_runpod_id():
    for name, entry in _endpoints().items():
        assert entry.get("worker_url") or entry.get("runpod_endpoint_id"), (
            f"{name}: needs worker_url or runpod_endpoint_id"
        )


def test_binder_score_endpoint_is_registered():
    entry = _endpoints().get("binder-score-local")
    assert entry is not None, "antigen validation needs the binder-score endpoint"
    # CPU-only worker on the GPU host; the port needs an ACG rule to be reachable
    assert entry["worker_url"] == "http://211.188.35.221:18108"
    # passthrough because the worker already speaks the scorer's own schema
    assert entry["adapter"] == "passthrough"


def test_no_endpoint_points_at_paid_runpod_by_accident():
    """alphafold/phastest are deliberate RunPod passthroughs; nothing else."""
    allowed = {"alphafold-local", "phastest-local"}
    for name, entry in _endpoints().items():
        if entry.get("runpod_endpoint_id"):
            assert name in allowed, f"{name} unexpectedly routes to paid RunPod"
