from __future__ import annotations


def build_input(payload: dict) -> dict:
    from adapters import _extract_archive

    out = dict(payload)
    if out.get("query_fasta"):
        out.pop("input_archive", None)
        return out
    sequence = str(out.pop("sequence", "") or "").strip()
    if sequence:
        if not sequence.startswith(">"):
            sequence = f">query\n{sequence}\n"
        out["query_fasta"] = sequence
    else:
        files = _extract_archive(out)
        for name, data in files.items():
            if name.lower().endswith((".fasta", ".fa", ".faa", ".fna")):
                out["query_fasta"] = data.decode("utf-8", errors="replace")
                break
    out.setdefault("task", "search")
    out.setdefault("target_db", "uniref90")
    out.pop("input_archive", None)
    return out
