from __future__ import annotations

import base64
import gzip
import io
import json
import tarfile
import zipfile
from typing import Any, Callable


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _add_text(tar: tarfile.TarFile, name: str, text: str | None) -> None:
    if not text:
        return
    _add(tar, name, text.encode("utf-8"))


def _add_raw_json(tar: tarfile.TarFile, output: dict) -> None:
    raw = json.dumps(output, indent=2, default=str, ensure_ascii=False).encode("utf-8")
    _add(tar, "output.json", raw)


def _add_streams(tar: tarfile.TarFile, output: dict) -> None:
    _add_text(tar, "stdout.log", output.get("stdout_tail") or output.get("stdout"))
    _add_text(tar, "stderr.log", output.get("stderr_tail") or output.get("stderr"))


def _add_sample_pdbs(tar: tarfile.TarFile, output: dict) -> None:
    for idx, entry in enumerate(output.get("sample_pdbs") or []):
        pdb_text = entry.get("pdb") if isinstance(entry, dict) else None
        if not pdb_text:
            continue
        sample_id = (entry.get("id") if isinstance(entry, dict) else None) or f"sample_{idx:03d}"
        _add_text(tar, f"{sample_id}.pdb", pdb_text)
    _add_text(tar, "topology.pdb", output.get("topology_pdb"))


def _build(callback: Callable[[tarfile.TarFile], None]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        callback(tar)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def package_bioemu(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        _add_sample_pdbs(tar, output)
        _add_streams(tar, output)

    return _build(build)


def package_esmfold(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        pdb = output.get("pdb") or output.get("pdb_text")
        if pdb:
            _add_text(tar, "esmfold.pdb", pdb)
        _add_sample_pdbs(tar, output)
        _add_streams(tar, output)

    return _build(build)


def package_esmfold2(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        pdb = output.get("pdb") or output.get("pdb_text")
        if pdb:
            _add_text(tar, "esmfold2.pdb", pdb)
        for entry in output.get("structures") or []:
            if isinstance(entry, dict) and entry.get("pdb"):
                seq_id = str(entry.get("id") or "structure")
                _add_text(tar, f"{seq_id}.pdb", entry["pdb"])
        _add_streams(tar, output)

    return _build(build)


def package_colabfold(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        for key, fname in (
            ("ranked_0_pdb", "colabfold_ranked_0.pdb"),
            ("ranked_1_pdb", "colabfold_ranked_1.pdb"),
            ("ranked_2_pdb", "colabfold_ranked_2.pdb"),
            ("pdb", "colabfold.pdb"),
            ("pdb_text", "colabfold.pdb"),
        ):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                _add_text(tar, fname, value)
        for entry in output.get("files") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("filename")
            content = entry.get("content") or entry.get("text") or entry.get("pdb")
            if name and isinstance(content, str) and content.strip():
                _add_text(tar, str(name), content)
        _add_streams(tar, output)

    return _build(build)


def package_af3(output: dict) -> str:
    """AlphaFold3: the worker returns an mmCIF (not PDB) plus a confidences
    dict with ptm/iptm/chain_pair_iptm - materialize the structure and keep
    the confidences visible in output.json rather than buried in an archive."""
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        cif = output.get("ranked_0_cif")
        if isinstance(cif, str) and cif.strip():
            _add_text(tar, "af3_model.cif", cif)
        _add_streams(tar, output)

    return _build(build)


def package_boltz(output: dict) -> str:
    """Boltz-2: PDB plus confidence scores, and an affinity JSON when the
    request included a ligand with predict_affinity=true. The worker already
    lifts the interesting fields into `scores`/`affinity` at the top level, so
    output.json alone is useful; this just also materializes the structure."""
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        pdb = output.get("ranked_0_pdb")
        if isinstance(pdb, str) and pdb.strip():
            _add_text(tar, "boltz_model_0.pdb", pdb)
        _add_streams(tar, output)

    return _build(build)


def package_proteinmpnn(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        # ProteinMPNN designs SEQUENCES (not structures). The worker returns the
        # full FASTA as `raw_fasta` and per-design entries under `samples`
        # ({name,header,sequence}); an older shape used `sequences`. Materialize
        # the FASTA so the result isn't json-only.
        raw_fasta = output.get("raw_fasta")
        if isinstance(raw_fasta, str) and raw_fasta.strip():
            _add_text(tar, "proteinmpnn.fasta", raw_fasta)
        for idx, entry in enumerate(output.get("samples") or output.get("sequences") or []):
            if not isinstance(entry, dict):
                continue
            seq = entry.get("sequence") or entry.get("fasta")
            if not seq:
                continue
            name = str(entry.get("name") or f"design_{idx:03d}")
            text = str(seq) if str(seq).startswith(">") else f">{entry.get('header') or name}\n{seq}\n"
            _add_text(tar, f"{name}.fasta", text)
        _add_streams(tar, output)

    return _build(build)


_GENERIC_PDB_KEYS = (
    "pdb",
    "pdb_text",
    "pdb_content",
    "ranked_0_pdb",
    "best_pdb",
    "relaxed_pdb",
    "relaxed_pdb_content",
    "topology_pdb",
)


def package_generic(output: dict) -> str:
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        for key in _GENERIC_PDB_KEYS:
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                _add_text(tar, f"{key}.pdb", value)
        _add_sample_pdbs(tar, output)
        _add_streams(tar, output)

    return _build(build)


def package_rfd3(output: dict) -> str:
    """RFD3/RFdiffusion: extract designed backbone PDBs from `designs`/`selected`.

    The worker returns `designs` (list of {"id","pdb"}) and `selected` (the chosen
    design dict). The generic packager doesn't know these keys, so without this
    packager the result archive only contains output.json (no .pdb files).
    """
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        seen: set[str] = set()
        designs = output.get("designs")
        if isinstance(designs, list):
            for idx, entry in enumerate(designs):
                if not isinstance(entry, dict):
                    continue
                pdb_text = entry.get("pdb")
                if not (isinstance(pdb_text, str) and pdb_text.strip()):
                    continue
                design_id = str(entry.get("id") or f"design_{idx:03d}")
                name = f"{design_id}.pdb"
                if name in seen:
                    name = f"design_{idx:03d}.pdb"
                seen.add(name)
                _add_text(tar, name, pdb_text)
        selected = output.get("selected")
        if isinstance(selected, dict) and isinstance(selected.get("pdb"), str) and selected["pdb"].strip():
            _add_text(tar, "selected.pdb", selected["pdb"])
        _add_streams(tar, output)

    return _build(build)


def package_mmseqs(output: dict) -> str:
    """MMseqs2 search: materialize the hit table (`tsv`) and the MSA.

    The worker returns the search results as `tsv` (a string, the m8/tsv hit
    table) and the MSA as `a3m_gz_b64` (gzip-compressed, base64-encoded a3m).
    The generic packager doesn't know these keys, so without this packager the
    result archive only contains output.json (the actual .tsv/.a3m results were
    buried inside the json and never written as standalone files).
    """
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        tsv = output.get("tsv")
        if isinstance(tsv, str) and tsv.strip():
            _add_text(tar, "search.tsv", tsv)
        a3m_gz = output.get("a3m_gz_b64")
        if isinstance(a3m_gz, str) and a3m_gz.strip():
            try:
                a3m = gzip.decompress(base64.b64decode(a3m_gz)).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — keep json/streams even if MSA is malformed
                a3m = None
            if a3m and a3m.strip():
                _add_text(tar, "msa.a3m", a3m)
        _add_streams(tar, output)

    return _build(build)


def package_diffdock(output: dict) -> str:
    """DiffDock: extract the docked poses from the results zip.

    The worker returns the entire DiffDock out_dir as `out_dir_zip_b64`
    (base64-encoded zip) containing the ranked pose files (rank1.sdf, etc.).
    The generic packager doesn't know this key, so without this packager the
    result archive only contains output.json + logs (no .sdf/.pdb poses).
    """
    def build(tar: tarfile.TarFile) -> None:
        _add_raw_json(tar, output)
        zip_b64 = output.get("out_dir_zip_b64")
        if isinstance(zip_b64, str) and zip_b64.strip():
            try:
                zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(zip_b64)))
            except Exception:  # noqa: BLE001 — keep json/streams even if zip is malformed
                zf = None
            if zf is not None:
                with zf:
                    for member in zf.infolist():
                        if member.is_dir():
                            continue
                        data = zf.read(member)
                        # Flatten nested dirs but keep a stable, readable name.
                        name = member.filename.replace("/", "_").lstrip("_") or "pose"
                        _add(tar, name, data)
        _add_streams(tar, output)

    return _build(build)


PACKAGERS: dict[str, Callable[[dict], str]] = {
    "bioemu": package_bioemu,
    "esmfold": package_esmfold,
    "esmfold2": package_esmfold2,
    "colabfold": package_colabfold,
    "af3": package_af3,
    "boltz": package_boltz,
    "proteinmpnn": package_proteinmpnn,
    "rfd3": package_rfd3,
    "mmseqs": package_mmseqs,
    "diffdock": package_diffdock,
    "generic": package_generic,
}


def package(name: str, output: dict[str, Any]) -> str:
    fn = PACKAGERS.get(name) or PACKAGERS["generic"]
    return fn(output)
