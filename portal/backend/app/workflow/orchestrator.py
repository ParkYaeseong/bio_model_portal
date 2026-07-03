from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .. import models
from . import conservation, soluprot_mock, template_loader

TERMINAL_OK = {"completed", "succeeded"}
TERMINAL_FAIL = {"failed", "cancelled", "timed_out", "error"}


def _nodes(run: models.WorkflowRun) -> list[dict]:
    tpl = template_loader.load_template(run.workflow.template_key)
    return template_loader.ordered_steps(tpl)


def _is_worker_step(node: dict) -> bool:
    return str(node.get("worker", "")).startswith("gateway.")


def start_run(db: Session, run: models.WorkflowRun) -> None:
    """Mark the run as running and spawn the first step only.
    Does NOT chain further transitions -- callers drive progress via
    repeated advance() calls."""
    run.status = "running"
    run.started_at = datetime.utcnow()
    db.commit()
    _spawn_step(db, run, order=0)
    db.commit()


def _spawn_step(db: Session, run: models.WorkflowRun, order: int) -> models.WorkflowRunStep:
    node = _nodes(run)[order]
    step = models.WorkflowRunStep(
        run_id=run.id, order=order, step_name=node["step_name"],
        worker_name=node["worker"], status="running", parameters=node.get("params", {}),
    )
    db.add(step)
    db.commit()
    db.refresh(step)
    return step


def _current_step(db: Session, run: models.WorkflowRun) -> models.WorkflowRunStep | None:
    return (
        db.query(models.WorkflowRunStep)
        .filter_by(run_id=run.id, status="running")
        .order_by(models.WorkflowRunStep.order.desc())
        .first()
    )


def advance(db: Session, run: models.WorkflowRun) -> None:
    """Progress the run by at most one transition: complete the currently
    running step (if it is done) and spawn the next one, or finish the run
    if that was the last step. Does not recurse -- callers loop to drive
    the run to completion."""
    if run.status != "running":
        return
    nodes = _nodes(run)
    step = _current_step(db, run)
    if step is None:
        return
    node = nodes[step.order]

    if _is_worker_step(node):
        job = db.query(models.Job).filter_by(id=step.job_id).first() if step.job_id else None
        if job is None:
            _submit_worker_step(db, run, step, node)
            return
        jstatus = (job.status or "").lower()
        if jstatus in TERMINAL_FAIL:
            _fail(db, run, step, job.error_message or "worker step failed")
            return
        if jstatus not in TERMINAL_OK:
            return
        result, metrics = _collect_worker_output(job)
    else:
        try:
            result, metrics = _run_portal_step(db, run, step, node)
        except Exception as exc:  # noqa: BLE001
            _fail(db, run, step, str(exc))
            return

    _complete_step(db, run, step, result, metrics)
    if step.order + 1 < len(nodes):
        _spawn_step(db, run, order=step.order + 1)
        db.commit()
    else:
        run.status = "completed"
        run.finished_at = datetime.utcnow()
        run.output_summary = _build_output_summary(db, run)
        db.commit()


def _complete_step(db, run, step, result, metrics):
    step.status = "completed"
    step.metrics = metrics or {}
    step.finished_at = datetime.utcnow()
    run.input_summary = {**(run.input_summary or {}), **(result or {})}
    db.commit()


def _fail(db, run, step, message: str):
    step.status = "failed"
    step.error_message = message
    step.finished_at = datetime.utcnow()
    run.status = "failed"
    run.error_message = f"{step.step_name}: {message}"
    run.finished_at = datetime.utcnow()
    for later in db.query(models.WorkflowRunStep).filter(
        models.WorkflowRunStep.run_id == run.id, models.WorkflowRunStep.order > step.order
    ):
        later.status = "skipped"
    db.commit()


def _run_portal_step(db, run, step, node) -> tuple[dict, dict]:
    """Execute a portal-side step. Returns (result_dict_merged_into_summary, metrics)."""
    ctx = run.input_summary or {}
    worker = node["worker"]
    if worker == "portal.input":
        return {}, {}
    if worker == "portal.conservation":
        mask = conservation.fixed_positions(ctx.get("msa", []), node["params"]["tiers"])
        return {"mask": mask}, {"tiers": node["params"]["tiers"]}
    if worker == "portal.soluprot_mock":
        cands = ctx.get("candidates", [])
        top = soluprot_mock.filter_top_k(cands, node["params"]["top_k"])
        return {"top_candidates": top}, {"kept": len(top)}
    if worker == "portal.report":
        return {}, {"candidates": len(ctx.get("top_candidates", []))}
    raise ValueError(f"unknown portal worker: {worker}")


def _submit_worker_step(db, run, step, node) -> None:
    """Placeholder wired in Task 7; raises until implemented."""
    raise NotImplementedError("worker step submission wired in Task 7")


def _collect_worker_output(job) -> tuple[dict, dict]:
    """Placeholder wired in Task 7."""
    return {}, {}


def _build_output_summary(db, run) -> dict:
    ctx = run.input_summary or {}
    return {"candidates": ctx.get("top_candidates", [])}
