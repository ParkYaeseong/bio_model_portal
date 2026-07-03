from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from . import conservation, job_bridge, soluprot_mock, template_loader

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


# gateway.<pipeline> -> pipeline key
_WORKER_PIPELINE = {
    "gateway.mmseqs": "mmseqs",
    "gateway.proteinmpnn": "proteinmpnn",
    "gateway.esmfold": "esmfold",
}


def _submit_worker_step(db, run, step, node) -> None:
    pipeline = _WORKER_PIPELINE[node["worker"]]
    ctx = run.input_summary or {}
    sequence = None
    input_files: list[Path] = []
    if pipeline == "mmseqs":
        sequence = ctx.get("sequence")
    elif pipeline == "proteinmpnn":
        pdb_path = ctx.get("backbone_path")
        if not pdb_path:
            _fail(db, run, step, "ProteinMPNN needs a PDB backbone (upload a structure).")
            return
        input_files = [Path(pdb_path)]
    elif pipeline == "esmfold":
        top = ctx.get("top_candidates", [])
        sequence = top[0]["sequence"] if top else ctx.get("sequence")
    job = job_bridge.create_step_job(
        db, user_id=run.owner_id, title=f"{run.id[:8]} {step.step_name}",
        pipeline=pipeline, params=step.parameters or {},
        input_files=input_files, sequence=sequence,
    )
    step.job_id = job.id
    db.commit()


def _collect_worker_output(job) -> tuple[dict, dict]:
    """Read a completed step-Job's artifacts into the run context + metrics."""
    result: dict = {}
    metrics: dict = {}
    rdir = Path(job.result_dir) if job.result_dir else None
    if job.pipeline == "proteinmpnn" and rdir:
        fasta = next(iter(rdir.rglob("*.fa")), None) or next(iter(rdir.rglob("*.fasta")), None)
        if fasta:
            result["candidates"] = _parse_fasta_candidates(fasta.read_text())
            metrics["designs"] = len(result["candidates"])
    elif job.pipeline == "mmseqs" and rdir:
        a3m = next(iter(rdir.rglob("*.a3m")), None)
        if a3m:
            result["msa"] = _parse_a3m(a3m.read_text())
            metrics["n_seqs"] = len(result["msa"])
    elif job.pipeline == "esmfold" and rdir:
        pdb = next(iter(rdir.rglob("*ranked_0*.pdb")), None) or next(iter(rdir.rglob("*.pdb")), None)
        if pdb:
            result["structures"] = [str(pdb)]
            metrics["plddt"] = _mean_plddt(pdb.read_text())
    return result, metrics


def _parse_fasta_candidates(text: str) -> list[dict]:
    out, cur_id, cur = [], None, []
    for line in text.splitlines():
        if line.startswith(">"):
            if cur_id is not None:
                out.append({"id": cur_id, "sequence": "".join(cur)})
            cur_id, cur = line[1:].strip() or f"seq_{len(out)+1}", []
        elif line.strip():
            cur.append(line.strip())
    if cur_id is not None:
        out.append({"id": cur_id, "sequence": "".join(cur)})
    return out


def _parse_a3m(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln and not ln.startswith(">")]


def _mean_plddt(pdb_text: str) -> float | None:
    vals = []
    for ln in pdb_text.splitlines():
        if ln.startswith("ATOM") and len(ln) >= 66 and ln[12:16].strip() == "CA":
            try:
                vals.append(float(ln[60:66]))
            except ValueError:
                pass
    return round(sum(vals) / len(vals), 2) if vals else None


def _build_output_summary(db, run) -> dict:
    ctx = run.input_summary or {}
    return {"candidates": ctx.get("top_candidates", [])}
