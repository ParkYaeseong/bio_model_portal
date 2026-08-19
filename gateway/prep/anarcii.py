"""Portal input -> ANARCII worker input.

The portal sends one `sequence` (plus optional uploaded files); the worker takes
`sequences` as either {id: seq} or [{"id", "sequence"}]. Requests that already
carry `sequences` (the pipeline's own client, or a direct API caller) are passed
straight through, so this only fills the gap for portal submissions.
"""

from __future__ import annotations


def _parse_fasta(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    name: str | None = None
    chunks: list[str] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if name is not None and chunks:
                out[name] = "".join(chunks)
            name = line[1:].split()[0] if line[1:].split() else f"seq{len(out) + 1}"
            chunks = []
        elif name is not None:
            chunks.append(line)
    if name is not None and chunks:
        out[name] = "".join(chunks)
    return out


def build_input(payload: dict) -> dict:
    from adapters import _extract_archive

    out = dict(payload)
    if out.get("sequences"):
        out.pop("input_archive", None)
        return out

    sequence = str(out.pop("sequence", "") or "").strip()
    if sequence:
        # A pasted FASTA numbers every record in it; a bare sequence is one query.
        out["sequences"] = _parse_fasta(sequence) if sequence.startswith(">") else {
            "query": "".join(sequence.split())
        }
    else:
        for name, data in _extract_archive(out).items():
            if name.lower().endswith((".fasta", ".fa", ".faa", ".fna", ".txt")):
                parsed = _parse_fasta(data.decode("utf-8", errors="replace"))
                if parsed:
                    out["sequences"] = parsed
                    break

    out.pop("input_archive", None)
    return out
