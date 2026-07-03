from __future__ import annotations

import json
from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from .. import models
from . import outcomes

MIN_SUPPORT = 2
FAIL_RATE = 0.5


def _param_key(args: dict) -> str:
    clean = {k: v for k, v in (args or {}).items() if k not in ("pipeline", "files")}
    return json.dumps(clean, sort_keys=True, ensure_ascii=False)


def compute_artifact(db: Session) -> models.ImprovementArtifact:
    interactions = db.query(models.Interaction).order_by(models.Interaction.created_at).all()

    # pipeline -> Counter(param_key) among successful runs; and success/fail tallies
    success_params: dict[str, Counter] = defaultdict(Counter)
    fail_counts: dict[str, int] = defaultdict(int)
    total_counts: dict[str, int] = defaultdict(int)
    fail_errors: dict[str, Counter] = defaultdict(Counter)
    # per-user ordered successful pipelines for recipe pairs
    user_seq: dict[int, list[str]] = defaultdict(list)
    n_jobs = 0

    for it in interactions:
        for call in it.tool_calls or []:
            if call.get("name") != "run_model":
                continue
            args = call.get("arguments_sanitized") or {}
            pipeline = args.get("pipeline")
            if not pipeline:
                continue
            jids = it.job_ids or []
            if not jids:
                continue
            out = outcomes.job_outcome(db, jids[0])
            if out["status"] is None:
                continue
            n_jobs += 1
            total_counts[pipeline] += 1
            if out["success"]:
                success_params[pipeline][_param_key(args)] += 1
                user_seq[it.user_id].append(pipeline)
            else:
                fail_counts[pipeline] += 1
                job = db.query(models.Job).filter_by(id=jids[0]).first()
                if job and job.error_message:
                    fail_errors[pipeline][job.error_message] += 1

    recommended: dict[str, dict] = {}
    for pipeline, counter in success_params.items():
        key, support = counter.most_common(1)[0]
        if support >= MIN_SUPPORT:
            recommended[pipeline] = json.loads(key)

    warnings: list[dict] = []
    for pipeline, fails in fail_counts.items():
        total = total_counts[pipeline]
        if fails >= MIN_SUPPORT and total and (fails / total) >= FAIL_RATE:
            msg = fail_errors[pipeline].most_common(1)[0][0] if fail_errors[pipeline] else "high failure rate"
            warnings.append({
                "pipeline": pipeline,
                "condition": f"{fails}/{total} runs failed",
                "message": msg,
            })

    pair_counts: Counter = Counter()
    for seq in user_seq.values():
        for a, b in zip(seq, seq[1:]):
            if a != b:
                pair_counts[(a, b)] += 1
    recipes = [
        {"goal": f"{a} → {b}", "steps": [a, b]}
        for (a, b), c in pair_counts.most_common(5) if c >= MIN_SUPPORT
    ]

    payload = {"recommended_defaults": recommended, "warnings": warnings, "recipes": recipes}
    stats = {"n_interactions": len(interactions), "n_jobs": n_jobs}
    parts = []
    if recommended:
        parts.append(f"{len(recommended)} default(s)")
    if warnings:
        parts.append(f"{len(warnings)} warning(s)")
    if recipes:
        parts.append(f"{len(recipes)} recipe(s)")
    summary = ", ".join(parts) or "no signal yet"

    art = models.ImprovementArtifact(status="proposed", summary=summary, payload=payload, stats=stats)
    db.add(art)
    db.commit()
    db.refresh(art)
    return art
