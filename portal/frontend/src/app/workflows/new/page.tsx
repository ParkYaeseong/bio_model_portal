"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { instantiateWorkflow, startWorkflowRun, uploadWorkflowInput } from "@/lib/api";

type ProteinMpnnParams = {
  num_seq_per_target: number;
  sampling_temp: number;
  seed: number;
  batch_size: number;
};

type ValidationParams = {
  plddt_cutoff: number;
  rmsd_cutoff: number;
  top_k: number;
};

const defaultProteinMpnn: ProteinMpnnParams = {
  num_seq_per_target: 16,
  sampling_temp: 0.1,
  seed: 0,
  batch_size: 1,
};

const defaultValidation: ValidationParams = {
  plddt_cutoff: 85,
  rmsd_cutoff: 2.0,
  top_k: 20,
};

const conservationTiers = [30, 50, 70];

const STEP_TEXT = "FASTA/PDB Input → MSA → Conservation → ProteinMPNN → SoluProt → Structure Validation → Report";

export default function NewWorkflowPage() {
  // Same auth pattern as the rest of the app: no bearer token, SSO cookie only.
  const token = "";
  const router = useRouter();

  const [name, setName] = useState("RAPID 설계 실행");
  const [sequence, setSequence] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [proteinMpnn, setProteinMpnn] = useState<ProteinMpnnParams>(defaultProteinMpnn);
  const [validation, setValidation] = useState<ValidationParams>(defaultValidation);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleProteinMpnnChange = (key: keyof ProteinMpnnParams, value: string) => {
    const parsed = Number(value);
    setProteinMpnn((prev) => ({ ...prev, [key]: Number.isFinite(parsed) ? parsed : prev[key] }));
  };

  const handleValidationChange = (key: keyof ValidationParams, value: string) => {
    const parsed = Number(value);
    setValidation((prev) => ({ ...prev, [key]: Number.isFinite(parsed) ? parsed : prev[key] }));
  };

  const handleSubmit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      let backbonePath: string | undefined;
      if (file) {
        const uploaded = await uploadWorkflowInput(token, file);
        backbonePath = uploaded.backbone_path;
      }

      const workflow = await instantiateWorkflow(token, { template_key: "rapid_v1", name });

      const step_params = {
        proteinmpnn: proteinMpnn,
        validation,
        conservation: { tiers: conservationTiers },
      };

      const run = await startWorkflowRun(token, workflow.id, {
        sequence: sequence.trim() || undefined,
        backbone_path: backbonePath,
        step_params,
      });

      router.push(`/workflows/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "워크플로우 실행 중 오류가 발생했습니다.");
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-3xl px-6 py-10">
        <div className="rounded-2xl border border-slate-200 bg-white p-6">
          <h1 className="text-2xl font-semibold text-slate-900">RAPID 단백질 설계 워크플로우</h1>
          <p className="mt-2 text-sm text-slate-500">{STEP_TEXT}</p>
        </div>

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <label className="text-sm font-semibold text-slate-600">워크플로우 이름</label>
          <input
            className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">ProteinMPNN</h2>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <label className="text-sm font-semibold text-slate-600">num_seq_per_target</label>
              <input
                type="number"
                className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm"
                value={proteinMpnn.num_seq_per_target}
                onChange={(e) => handleProteinMpnnChange("num_seq_per_target", e.target.value)}
              />
            </div>
            <div>
              <label className="text-sm font-semibold text-slate-600">sampling_temp</label>
              <input
                type="number"
                step="0.01"
                className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm"
                value={proteinMpnn.sampling_temp}
                onChange={(e) => handleProteinMpnnChange("sampling_temp", e.target.value)}
              />
            </div>
            <div>
              <label className="text-sm font-semibold text-slate-600">seed</label>
              <input
                type="number"
                className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm"
                value={proteinMpnn.seed}
                onChange={(e) => handleProteinMpnnChange("seed", e.target.value)}
              />
            </div>
            <div>
              <label className="text-sm font-semibold text-slate-600">batch_size</label>
              <input
                type="number"
                className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm"
                value={proteinMpnn.batch_size}
                onChange={(e) => handleProteinMpnnChange("batch_size", e.target.value)}
              />
            </div>
          </div>
        </div>

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">Validation</h2>
          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <div>
              <label className="text-sm font-semibold text-slate-600">plddt_cutoff</label>
              <input
                type="number"
                className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm"
                value={validation.plddt_cutoff}
                onChange={(e) => handleValidationChange("plddt_cutoff", e.target.value)}
              />
            </div>
            <div>
              <label className="text-sm font-semibold text-slate-600">rmsd_cutoff</label>
              <input
                type="number"
                step="0.1"
                className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm"
                value={validation.rmsd_cutoff}
                onChange={(e) => handleValidationChange("rmsd_cutoff", e.target.value)}
              />
            </div>
            <div>
              <label className="text-sm font-semibold text-slate-600">top_k</label>
              <input
                type="number"
                className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm"
                value={validation.top_k}
                onChange={(e) => handleValidationChange("top_k", e.target.value)}
              />
            </div>
          </div>
        </div>

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">Conservation tiers</h2>
          <p className="mt-2 text-sm text-slate-500">{conservationTiers.join(", ")}</p>
        </div>

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <label className="text-sm font-semibold text-slate-600">서열 (선택)</label>
          <textarea
            className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 font-mono text-sm"
            rows={4}
            value={sequence}
            onChange={(e) => setSequence(e.target.value)}
            placeholder={">sp|P12345 예시\nMVTES..."}
          />

          <label className="mt-4 block text-sm font-semibold text-slate-600">PDB backbone 업로드 (선택)</label>
          <input
            type="file"
            accept=".pdb,.cif"
            className="mt-1 w-full text-sm"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>

        {error && (
          <div className="mt-6 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-600">
            {error}
          </div>
        )}

        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            disabled
            className="rounded-full border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-400"
          >
            Langflow에서 편집 (Phase 2)
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="rounded-full bg-brand-500 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
          >
            {submitting ? "실행 중..." : "워크플로우 실행"}
          </button>
        </div>
      </section>
    </main>
  );
}
