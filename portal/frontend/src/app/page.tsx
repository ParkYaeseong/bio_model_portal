"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import JSZip from "jszip";
import ReactMarkdown from "react-markdown";

import {
  PipelineMeta,
  JobResponse,
  ContigOption,
  AuthError,
  fetchPipelines,
  fetchJobs,
  fetchContigSuggestions,
  createJob,
  deleteJob,
  downloadArchive,
  downloadArtifact,
} from "@/lib/api";
import { triggerDownload } from "@/lib/download";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { NglViewer } from "@/components/NglViewer";
import { PhastestViewer, CgviewData } from "@/components/PhastestViewer";
import { AssistantWidget } from "@/components/AssistantWidget";
import Link from "next/link";

type UploadEntry = {
  file: File;
  relativePath: string;
};

type DiffdockJobInput = {
  complex_name: string;
  protein_path: string;
  ligand_description: string;
  ligand_type: "sdf" | "smiles";
  protein_sequence: string;
  protein_file: UploadEntry | null;
  ligand_file: UploadEntry | null;
};

const createBlankDiffdockJob = (): DiffdockJobInput => ({
  complex_name: "",
  protein_path: "",
  ligand_description: "",
  ligand_type: "sdf",
  protein_sequence: "",
  protein_file: null,
  ligand_file: null,
});

type PhastestConfig = {
  input_type: "fasta" | "contig" | "genbank";
  mode: "lite" | "deep";
  sample_name: string;
  accession: string;
};

const defaultPhastestConfig: PhastestConfig = {
  input_type: "fasta",
  mode: "lite",
  sample_name: "",
  accession: "",
};

const sanitizeFileSlug = (value: string) => value.replace(/[^A-Za-z0-9._-]+/g, "_");

const ensureInputsPrefix = (path: string) => {
  const trimmed = path.replace(/^\/+/, "");
  return trimmed.startsWith("inputs/") ? trimmed : `inputs/${trimmed}`;
};

const inferInputsPath = (complexName: string, fileName: string, kind: "protein" | "ligand") => {
  const slug = sanitizeFileSlug(complexName || "complex");
  const ext = fileName.includes(".") ? fileName.slice(fileName.lastIndexOf(".")) : (kind === "protein" ? ".pdb" : ".sdf");
  return ensureInputsPrefix(`${slug}_${kind}${ext}`);
};

const createUploadEntry = (file: File, desiredPath?: string, enforceInputsPrefix = false): UploadEntry => {
  let relativePath = (desiredPath || file.name || "upload.bin").replace(/^\/+/, "");
  if (enforceInputsPrefix) {
    relativePath = ensureInputsPrefix(relativePath);
  }
  return { file, relativePath };
};

// Accepted upload formats + an unobtrusive hint, per pipeline. `accept` only
// filters the file dialog (users can still pick "all files"); the hint tells
// them what the pipeline can use.
function acceptedFiles(pipeline: PipelineMeta): { accept: string; hint: string } {
  if (pipeline.key === "phastest") {
    return { accept: ".fasta,.fa,.fna,.csv,.gb,.gbk,.zip", hint: "FASTA · CSV · GenBank" };
  }
  if (pipeline.supportsSequence) {
    return {
      accept: ".fasta,.fa,.faa,.fna,.txt,.pdb,.cif,.mmcif,.zip",
      hint: "FASTA, 또는 PDB·CIF (구조 업로드 시 서열 자동 추출)",
    };
  }
  return { accept: ".pdb,.cif,.mmcif,.zip", hint: "PDB · CIF" };
}

export default function HomePage() {
  const [sessionExpired, setSessionExpired] = useState(false);

  // Access is gated upstream by the Keycloak SSO forward_auth gateway, and the
  // backend identifies each user from the gateway-injected X-KBF-User header —
  // so the browser holds no token. Clear any stale token from the old
  // shared-account design so it can never be sent.
  useEffect(() => {
    window.localStorage.removeItem("portal-token");
  }, []);

  // Real logout = end the SSO session at the gateway (Keycloak end-session),
  // not just drop local state.
  const handleLogout = () => {
    window.location.href = "/logout";
  };

  return (
    <>
      <Dashboard onLogout={handleLogout} onAuthExpired={() => setSessionExpired(true)} />
      {sessionExpired && <SessionExpiredModal />}
    </>
  );
}

function SessionExpiredModal() {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 px-6">
      <div className="w-full max-w-md rounded-3xl bg-white p-8 text-center shadow-2xl">
        <h2 className="text-xl font-semibold text-slate-900">세션이 만료되었습니다</h2>
        <p className="mt-2 text-slate-600">로그인 하세요.</p>
        <a
          href="/login"
          className="mt-6 inline-block rounded-2xl bg-brand-600 px-6 py-3 text-white shadow-lg shadow-brand-200 transition hover:bg-brand-500"
        >
          로그인
        </a>
      </div>
    </div>
  );
}

type DashboardProps = {
  onLogout: () => void;
  onAuthExpired: () => void;
};

function Dashboard({ onLogout, onAuthExpired }: DashboardProps) {
  // Identity travels in the gateway-injected header, so API calls carry no
  // bearer token; the empty string disables the Authorization header.
  const token = "";

  const handleAuthError = useCallback(
    (error: unknown) => {
      if (error instanceof AuthError) onAuthExpired();
    },
    [onAuthExpired]
  );

  const { data: pipelineData } = useSWR(["pipelines"], () => fetchPipelines(token), { onError: handleAuthError });
  const { data: jobs, mutate: refreshJobs, isLoading: jobsLoading } = useSWR(
    ["jobs"],
    () => fetchJobs(token),
    { refreshInterval: 15000, onError: handleAuthError }
  );

  const [selectedPipelineKey, setSelectedPipelineKey] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [sequence, setSequence] = useState("");
  const [title, setTitle] = useState("새로운 작업");
  const [paramState, setParamState] = useState<Record<string, string>>({ model_preset: "monomer", db_preset: "full_dbs" });
  // Multimer/complex chain editor: one sequence per chain, assembled on submit
  // (AF2 -> multi-record FASTA, ColabFold -> ':'-joined single record).
  const [multimerChains, setMultimerChains] = useState<string[]>(["", ""]);
  const [colabfoldMultimer, setColabfoldMultimer] = useState(false);
  const [uploads, setUploads] = useState<UploadEntry[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [diffdockJobs, setDiffdockJobs] = useState<DiffdockJobInput[]>([createBlankDiffdockJob()]);
  const [phastestConfig, setPhastestConfig] = useState<PhastestConfig>(defaultPhastestConfig);
  const [contigOptions, setContigOptions] = useState<ContigOption[]>([]);
  const [contigChoice, setContigChoice] = useState<string>("custom");
  const [customContig, setCustomContig] = useState<string>("");
  const [contigLoading, setContigLoading] = useState(false);

  const retentionDays = pipelineData?.retentionDays ?? 7;
  const pipelines = useMemo(() => pipelineData?.pipelines ?? [], [pipelineData]);
  const selectedPipeline = pipelines.find((p) => p.key === selectedPipelineKey) ?? pipelines[0];
  const selectedJob = useMemo(() => jobs?.find((job) => job.id === selectedJobId) ?? jobs?.[0], [jobs, selectedJobId]);

  useEffect(() => {
    if (pipelines.length && !selectedPipelineKey) {
      setSelectedPipelineKey(pipelines[0].key);
    }
  }, [pipelines, selectedPipelineKey]);

  // Restore password fields (e.g. esmfold2 api_key) from localStorage when the pipeline changes.
  useEffect(() => {
    if (typeof window === "undefined" || !selectedPipeline) return;
    const restored: Record<string, string> = {};
    selectedPipeline.inputFields.forEach((field) => {
      if (field.field_type !== "password") return;
      const stored = window.localStorage.getItem(`portal:${selectedPipeline.key}:${field.name}`);
      if (stored) restored[field.name] = stored;
    });
    if (Object.keys(restored).length) {
      setParamState((prev) => ({ ...prev, ...restored }));
    }
  }, [selectedPipeline]);

  // Reset contig suggestions whenever the selected pipeline changes.
  useEffect(() => {
    setContigOptions([]);
    setContigChoice("custom");
    setCustomContig("");
    setMultimerChains(["", ""]);
    setColabfoldMultimer(false);
  }, [selectedPipeline?.key]);

  const multimerActive =
    (selectedPipeline?.key === "alphafold" && paramState.model_preset === "multimer") ||
    (selectedPipeline?.key === "colabfold" && colabfoldMultimer);

  const handlePasswordPersist = (pipelineKey: string, name: string, value: string) => {
    if (typeof window === "undefined") return;
    const storageKey = `portal:${pipelineKey}:${name}`;
    if (value) window.localStorage.setItem(storageKey, value);
    else window.localStorage.removeItem(storageKey);
  };

  useEffect(() => {
    if (jobs?.length && !selectedJobId) {
      setSelectedJobId(jobs[0].id);
    }
  }, [jobs, selectedJobId]);

  const handleParamChange = (name: string, value: string) => {
    setParamState((prev) => ({ ...prev, [name]: value }));
  };

  const handleFiles = (files: FileList | null) => {
    if (!files) return;
    const fileArray = Array.from(files);
    const entries = fileArray.map((file) => createUploadEntry(file));
    setUploads((prev) => [...prev, ...entries]);
    if (selectedPipeline?.key === "rfdiffusion") {
      const pdb = fileArray.find((f) => /\.(pdb|cif)$/i.test(f.name));
      if (pdb) {
        void loadContigSuggestions(pdb);
      }
    }
  };

  const loadContigSuggestions = async (file: File) => {
    setContigLoading(true);
    try {
      const result = await fetchContigSuggestions(file, token);
      setContigOptions(result.options);
      const recommended = result.options.find((o) => o.recommended);
      setContigChoice(recommended?.id ?? "custom");
    } finally {
      setContigLoading(false);
    }
  };

  const handleFolder = async (files: FileList | null) => {
    if (!files || !files.length) return;
    const zip = new JSZip();
    Array.from(files).forEach((file) => {
      const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      zip.file(relative, file);
    });
    const first = files[0] as File & { webkitRelativePath?: string };
    const folderName = (first.webkitRelativePath && first.webkitRelativePath.split("/")[0]) || first.name || "folder";
    const blob = await zip.generateAsync({ type: "blob" });
    const zippedFile = new File([blob], `${folderName}.zip`, { type: "application/zip" });
    setUploads((prev) => [...prev, createUploadEntry(zippedFile)]);
  };

  const removeUpload = (index: number) => {
    setUploads((prev) => prev.filter((_, idx) => idx !== index));
  };

  const handleDiffdockFileChange = (index: number, kind: "protein" | "ligand", file: File | null) => {
    setDiffdockJobs((prev) =>
      prev.map((job, idx) => {
        if (idx !== index) return job;
        const next = { ...job };
        if (kind === "protein") {
          next.protein_file = null;
          if (file) {
            const relativePath = inferInputsPath(job.complex_name || `complex_${index + 1}`, file.name, "protein");
            const entry = createUploadEntry(file, relativePath);
            next.protein_file = entry;
            next.protein_path = relativePath;
          } else {
            next.protein_path = "";
          }
        } else {
          next.ligand_file = null;
          if (file) {
            const relativePath = inferInputsPath(job.complex_name || `complex_${index + 1}`, file.name, "ligand");
            const entry = createUploadEntry(file, relativePath);
            next.ligand_file = entry;
            next.ligand_description = relativePath;
            next.ligand_type = "sdf";
          } else if (next.ligand_type === "sdf") {
            next.ligand_description = "";
          }
        }
        return next;
      })
    );
  };

  const handleDiffdockJobRemove = (index: number) => {
    setDiffdockJobs((prev) => prev.filter((_, idx) => idx !== index));
  };

  const handleSubmit = async () => {
    if (!selectedPipeline) return;
    const normalizedParams: Record<string, unknown> = { ...paramState };

    // Reject number inputs below their declared minimum (e.g. 0 / negative counts).
    for (const field of selectedPipeline.inputFields) {
      if (field.field_type !== "number" || field.minimum == null) continue;
      const raw = (paramState[field.name] ?? "").trim();
      if (raw === "") continue;
      const num = Number(raw);
      if (!Number.isFinite(num) || num < field.minimum) {
        setUploadError(`${translate(field.label)}은(는) ${field.minimum} 이상이어야 합니다.`);
        return;
      }
    }

    const jobSpecificUploads: UploadEntry[] = [];

    if (selectedPipeline.key === "diffdock") {
      const sanitized: Array<{
        complex_name: string;
        protein_path: string;
        ligand_description: string;
        ligand_type: "sdf" | "smiles";
        protein_sequence: string;
      }> = [];
      for (let index = 0; index < diffdockJobs.length; index += 1) {
        const job = diffdockJobs[index];
        const complexName = job.complex_name.trim();
        const proteinPath = job.protein_path.trim();
        if (!complexName) {
          setUploadError(`복합체 #${index + 1}의 이름을 입력하세요.`);
          return;
        }
        if (!proteinPath) {
          setUploadError(`복합체 #${index + 1}의 Protein 파일을 업로드하거나 경로를 입력하세요.`);
          return;
        }
        if (job.protein_file) {
          jobSpecificUploads.push(job.protein_file);
        }
        let ligandDescription = job.ligand_description.trim();
        if (job.ligand_type === "sdf") {
          if (!ligandDescription) {
            setUploadError(`복합체 #${index + 1}의 Ligand SDF 파일을 업로드하세요.`);
            return;
          }
          if (job.ligand_file) {
            jobSpecificUploads.push(job.ligand_file);
          }
        } else if (!ligandDescription) {
          setUploadError(`복합체 #${index + 1}의 SMILES 문자열을 입력하세요.`);
          return;
        }
        sanitized.push({
          complex_name: complexName,
          protein_path: proteinPath,
          ligand_description: ligandDescription,
          ligand_type: job.ligand_type,
          protein_sequence: job.protein_sequence.trim(),
        });
      }
      if (!sanitized.length) {
        setUploadError("DiffDock 복합체 정보를 최소 1개 이상 입력하세요.");
        return;
      }
      normalizedParams.jobs = sanitized;
    }

    if (selectedPipeline.key === "phastest") {
      if (!phastestConfig.sample_name.trim()) {
        setUploadError("샘플 이름을 입력하세요.");
        return;
      }
      normalizedParams.input_type = phastestConfig.input_type;
      normalizedParams.mode = phastestConfig.mode;
      normalizedParams.sample_name = phastestConfig.sample_name.trim();
      if (phastestConfig.input_type === "genbank") {
        if (!phastestConfig.accession.trim()) {
          setUploadError("GenBank 모드에서는 Accession 번호가 필요합니다.");
          return;
        }
        normalizedParams.accession = phastestConfig.accession.trim();
      } else if (phastestConfig.accession.trim()) {
        normalizedParams.accession = phastestConfig.accession.trim();
      }
    }

    if (selectedPipeline.key === "rfdiffusion") {
      const chosen = contigOptions.find((o) => o.id === contigChoice);
      const contigValue = contigChoice === "custom" ? customContig.trim() : (chosen?.contig ?? "");
      normalizedParams.contigs = contigValue;
      normalizedParams.contig_processed_coords = contigChoice !== "custom";
    }

    const needsArchive =
      selectedPipeline.requiresArchive &&
      !(selectedPipeline.key === "phastest" && phastestConfig.input_type === "genbank");
    const totalUploads = uploads.length + jobSpecificUploads.length;
    if (needsArchive && totalUploads === 0) {
      setUploadError("이 파이프라인은 최소 한 개의 파일이 필요합니다.");
      return;
    }

    // Assemble the sequence from the per-chain multimer editor when active.
    let effectiveSequence = sequence.trim();
    if (multimerActive) {
      const chains = multimerChains.map((c) => c.replace(/\s+/g, "").toUpperCase()).filter(Boolean);
      if (chains.length < 2) {
        setUploadError("멀티머(복합체)는 서열이 있는 체인을 2개 이상 입력하세요.");
        return;
      }
      effectiveSequence =
        selectedPipeline.key === "colabfold"
          ? chains.join(":")
          : chains.map((seq, i) => `>chain_${i + 1}\n${seq}`).join("\n") + "\n";
    }

    setUploadError(null);
    setIsSubmitting(true);
    try {
      const form = new FormData();
      form.append("title", title);
      form.append("pipeline", selectedPipeline.key);
      form.append("parameters", JSON.stringify(normalizedParams));
      if (selectedPipeline.supportsSequence && effectiveSequence) {
        form.append("sequence", effectiveSequence);
      }
      [...uploads, ...jobSpecificUploads].forEach((entry) => {
        form.append("files", entry.file, entry.relativePath);
      });
      await createJob(form, token);
      setUploads([]);
      setSequence("");
      setMultimerChains(["", ""]);
      setColabfoldMultimer(false);
      setDiffdockJobs([createBlankDiffdockJob()]);
      setPhastestConfig(defaultPhastestConfig);
      setContigOptions([]);
      setContigChoice("custom");
      setCustomContig("");
      await refreshJobs();
    } catch (error: any) {
      handleAuthError(error);
      setUploadError(error.message || "작업 생성 중 오류가 발생했습니다.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDownload = async (jobId: string) => {
    try {
      const blob = await downloadArchive(jobId, token);
      triggerDownload(blob, `${jobId}.tar.gz`);
    } catch (error) {
      handleAuthError(error);
      throw error;
    }
  };

  const handleArtifactDownload = async (jobId: string, artifactId: string, filename: string) => {
    try {
      const blob = await downloadArtifact(jobId, artifactId, token);
      triggerDownload(blob, filename);
    } catch (error) {
      handleAuthError(error);
      throw error;
    }
  };

  const handleDelete = async (jobId: string) => {
    if (!confirm("정말 삭제하시겠습니까?")) return;
    try {
      await deleteJob(jobId, token);
    } catch (error) {
      handleAuthError(error);
      throw error;
    }
    if (jobId === selectedJobId) {
      setSelectedJobId(null);
    }
    await refreshJobs();
  };

  return (
    <>
      <main className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
          <div>
            <p className="text-sm text-slate-500">AI Structure Pipeline Hub</p>
            <h1 className="text-2xl font-semibold text-slate-900">K-BioFoundry Orchestrator</h1>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/workflows"
              className="rounded-full border border-slate-200 px-5 py-2 text-sm text-slate-600 hover:bg-slate-100"
            >
              워크플로우
            </Link>
            <button className="rounded-full border border-slate-200 px-5 py-2 text-sm text-slate-600 hover:bg-slate-100" onClick={onLogout}>
              로그아웃
            </button>
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-7xl px-6 py-6">
        <div className="rounded-2xl border border-brand-100 bg-white p-6 text-sm text-slate-600 shadow-sm">
          <p>
            알림: 업로드한 데이터와 결과물은 서버 용량을 보호하기 위해 <strong>{retentionDays}일</strong> 뒤 자동으로 삭제됩니다. 중요한 결과는 즉시 다운로드 경로나 외부 스토리지에 백업하세요.
          </p>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-6 pb-24">
        <div className="grid gap-8 lg:grid-cols-[1.2fr,0.8fr]">
          <div className="space-y-8">
            <PipelineSelector selected={selectedPipeline?.key} pipelines={pipelines} onSelect={setSelectedPipelineKey} />
            {selectedPipeline && (
              <SubmissionPanel
                pipeline={selectedPipeline}
                title={title}
                onTitleChange={setTitle}
                sequence={sequence}
                onSequenceChange={setSequence}
                multimerActive={multimerActive}
                multimerChains={multimerChains}
                onMultimerChainsChange={setMultimerChains}
                colabfoldMultimer={colabfoldMultimer}
                onColabfoldMultimerChange={setColabfoldMultimer}
                paramState={paramState}
                onParamChange={handleParamChange}
                onPasswordPersist={handlePasswordPersist}
                diffdockJobs={diffdockJobs}
                onDiffdockJobsChange={setDiffdockJobs}
                onDiffdockFileChange={handleDiffdockFileChange}
                onDiffdockRemove={handleDiffdockJobRemove}
                phastestConfig={phastestConfig}
                onPhastestConfigChange={setPhastestConfig}
                contigOptions={contigOptions}
                contigChoice={contigChoice}
                onContigChoiceChange={setContigChoice}
                customContig={customContig}
                onCustomContigChange={setCustomContig}
                contigLoading={contigLoading}
                uploads={uploads}
                onFiles={handleFiles}
                onFolder={handleFolder}
                onRemove={removeUpload}
                onSubmit={handleSubmit}
                submitting={isSubmitting}
                uploadError={uploadError}
              />
            )}
          </div>
          <div className="space-y-6">
            <JobTable
              jobs={jobs}
              loading={jobsLoading}
              onSelect={setSelectedJobId}
              selectedJobId={selectedJob?.id}
              onDownload={handleDownload}
              onDelete={handleDelete}
            />
            {selectedJob && (
              <ResultPanel job={selectedJob} token={token} onArtifactDownload={handleArtifactDownload} />)
            }
          </div>
        </div>
      </section>
    </main>
    <AssistantWidget token={token} jobs={jobs} initialJobId={selectedJob?.id ?? null} />
    </>
  );
}

type PipelineSelectorProps = {
  pipelines: PipelineMeta[];
  selected?: string;
  onSelect: (key: string) => void;
};

function PipelineSelector({ pipelines, selected, onSelect }: PipelineSelectorProps) {
  const copyMap: Record<string, { description: string; instructions: string }> = {
    alphafold: {
      description: "단백질 구조 예측",
      instructions: "FASTA 서열을 붙여 넣거나 ZIP으로 묶어서 업로드하고 모델/DB 옵션을 선택하세요.",
    },
    diffdock: {
      description: "리간드 도킹/포즈 예측",
      instructions: "수용체 PDB와 리간드 SDF/SMILES를 업로드하면 여러 복합체를 한 번에 도킹합니다.",
    },
    phastest: {
      description: "PHASTEST 바이러스 분석",
      instructions: "유전체 FASTA·Contig·GenBank 데이터를 넣어 기능 리포트와 주석을 생성합니다.",
    },
  };
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-slate-900">파이프라인 선택</h2>
      <div className="grid gap-4 md:grid-cols-3">
        {pipelines.map((pipeline) => (
          <button
            key={pipeline.key}
            onClick={() => onSelect(pipeline.key)}
            className={`rounded-2xl border p-4 text-left transition ${
              selected === pipeline.key ? "border-brand-500 bg-brand-50" : "border-slate-200 bg-white hover:border-brand-200"
            }`}
          >
            <p className="text-sm font-semibold text-brand-600">{pipeline.label}</p>
            <p className="mt-1 text-base font-semibold text-slate-900">{copyMap[pipeline.key]?.description || pipeline.description}</p>
            <p className="mt-2 text-sm text-slate-500">{copyMap[pipeline.key]?.instructions || pipeline.instructions}</p>
          </button>
        ))}
      </div>
    </div>
  );
}

type SubmissionPanelProps = {
  pipeline: PipelineMeta;
  title: string;
  onTitleChange: (value: string) => void;
  sequence: string;
  onSequenceChange: (value: string) => void;
  multimerActive: boolean;
  multimerChains: string[];
  onMultimerChainsChange: (chains: string[]) => void;
  colabfoldMultimer: boolean;
  onColabfoldMultimerChange: (value: boolean) => void;
  paramState: Record<string, string>;
  onParamChange: (name: string, value: string) => void;
  onPasswordPersist?: (pipelineKey: string, name: string, value: string) => void;
  diffdockJobs: DiffdockJobInput[];
  onDiffdockJobsChange: (jobs: DiffdockJobInput[]) => void;
  onDiffdockFileChange: (index: number, kind: "protein" | "ligand", file: File | null) => void;
  onDiffdockRemove: (index: number) => void;
  phastestConfig: PhastestConfig;
  onPhastestConfigChange: (config: PhastestConfig) => void;
  contigOptions: ContigOption[];
  contigChoice: string;
  onContigChoiceChange: (id: string) => void;
  customContig: string;
  onCustomContigChange: (value: string) => void;
  contigLoading: boolean;
  uploads: UploadEntry[];
  onFiles: (files: FileList | null) => void;
  onFolder: (files: FileList | null) => void;
  onRemove: (index: number) => void;
  onSubmit: () => Promise<void>;
  submitting: boolean;
  uploadError: string | null;
};

function SubmissionPanel(props: SubmissionPanelProps) {
  const {
    pipeline,
    title,
    onTitleChange,
    sequence,
    onSequenceChange,
    multimerActive,
    multimerChains,
    onMultimerChainsChange,
    colabfoldMultimer,
    onColabfoldMultimerChange,
    diffdockJobs,
    onDiffdockJobsChange,
    onDiffdockFileChange,
    onDiffdockRemove,
    phastestConfig,
    onPhastestConfigChange,
    contigOptions,
    contigChoice,
    onContigChoiceChange,
    customContig,
    onCustomContigChange,
    contigLoading,
    paramState,
    onParamChange,
    onPasswordPersist,
    uploads,
    onFiles,
    onFolder,
    onRemove,
    onSubmit,
    submitting,
    uploadError,
  } = props;

  const folderInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (folderInputRef.current) {
      folderInputRef.current.setAttribute("webkitdirectory", "true");
    }
  }, []);

  return (
    <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-xl font-semibold text-slate-900">{pipeline.label} 작업 세팅</h3>
          <p className="text-sm text-slate-500">모든 입력을 확인한 뒤 &quot;작업 실행&quot; 버튼을 누르세요.</p>
        </div>
        <span className="rounded-full bg-brand-100 px-3 py-1 text-xs font-semibold text-brand-700">{pipeline.previewKind.toUpperCase()}</span>
      </div>
      <div className="mt-6">
        <label className="text-sm font-semibold text-slate-600">작업 제목</label>
        <input className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3" value={title} onChange={(e) => onTitleChange(e.target.value)} />
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        {pipeline.inputFields
          .filter((field) => !(pipeline.key === "rfdiffusion" && field.name === "contigs"))
          // AF2 model_preset is rendered as the prominent 예측 유형 toggle below.
          .filter((field) => !(pipeline.key === "alphafold" && field.name === "model_preset"))
          .map((field) => (
          <div key={field.name}>
            <label className="text-sm font-semibold text-slate-600">{translate(field.label)}</label>
            {field.field_type === "select" ? (
              <select
                className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3"
                value={paramState[field.name] || ""}
                onChange={(e) => onParamChange(field.name, e.target.value)}
              >
                <option value="" disabled>
                  옵션 선택
                </option>
                {field.options?.map((option) => (
                  <option key={option.value} value={option.value}>
                    {translate(option.label)}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type={
                  field.field_type === "number"
                    ? "number"
                    : field.field_type === "date"
                    ? "date"
                    : field.field_type === "password"
                    ? "password"
                    : "text"
                }
                className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3"
                placeholder={field.placeholder || ""}
                value={paramState[field.name] || ""}
                min={field.field_type === "number" && field.minimum != null ? field.minimum : undefined}
                step={field.field_type === "number" ? 1 : undefined}
                onChange={(e) => {
                  onParamChange(field.name, e.target.value);
                  if (field.field_type === "password") {
                    onPasswordPersist?.(pipeline.key, field.name, e.target.value);
                  }
                }}
                required={field.required}
                autoComplete={field.field_type === "password" ? "off" : undefined}
              />
            )}
            {field.helper && <p className="mt-1 text-xs text-slate-500">{translate(field.helper)}</p>}
            {field.field_type === "password" && (
              <button
                type="button"
                onClick={() => {
                  onParamChange(field.name, "");
                  onPasswordPersist?.(pipeline.key, field.name, "");
                }}
                disabled={!paramState[field.name]}
                className="mt-1 text-xs text-brand-600 underline hover:text-brand-700 disabled:cursor-not-allowed disabled:text-slate-300 disabled:no-underline"
              >
                저장된 키 지우기
              </button>
            )}
          </div>
        ))}
      </div>

      {pipeline.key === "rfdiffusion" && (
        <div className="mt-4">
          <label className="text-sm font-semibold text-slate-600">Contig 선택 (모티프 스캐폴딩)</label>
          {contigOptions.length > 0 ? (
            <select
              className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3"
              value={contigChoice}
              onChange={(e) => onContigChoiceChange(e.target.value)}
            >
              {contigOptions.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                  {o.recommended ? " (추천)" : ""}
                  {o.contig ? ` — ${o.contig}` : ""}
                </option>
              ))}
            </select>
          ) : (
            <p className="mt-1 rounded-2xl border border-dashed border-slate-200 px-4 py-3 text-sm text-slate-400">
              {contigLoading
                ? "PDB를 분석하여 Contig 후보를 불러오는 중..."
                : "PDB/CIF 파일을 업로드하면 Contig 후보가 자동으로 채워집니다. 신규 디자인이면 비워두세요."}
            </p>
          )}
          {(contigChoice === "custom" || contigOptions.length === 0) && (
            <input
              type="text"
              className="mt-2 w-full rounded-2xl border border-slate-200 px-4 py-3"
              placeholder="A1-10,B5-12"
              value={customContig}
              onChange={(e) => onCustomContigChange(e.target.value)}
            />
          )}
          <p className="mt-1 text-xs text-slate-500">
            업로드한 PDB의 잔기 선택. 신규 디자인이면 비워두세요.
          </p>
        </div>
      )}

      {pipeline.key === "diffdock" && (
        <div className="mt-6">
          <DiffdockJobsEditor
            jobs={diffdockJobs}
            onChange={onDiffdockJobsChange}
            onFileChange={onDiffdockFileChange}
            onRemoveJob={onDiffdockRemove}
          />
        </div>
      )}

      {pipeline.key === "phastest" && (
        <div className="mt-6">
          <PhastestConfigForm config={phastestConfig} onChange={onPhastestConfigChange} />
        </div>
      )}

      {(pipeline.key === "colabfold" || pipeline.key === "alphafold") && (
        <div className="mt-4">
          <label className="text-sm font-semibold text-slate-600">예측 유형</label>
          <div className="mt-1 inline-flex rounded-2xl border border-slate-200 p-1">
            {[
              { value: false, label: "Monomer (단일 체인)" },
              { value: true, label: "Multimer (복합체)" },
            ].map((opt) => {
              // AF2 drives the real model_preset param; ColabFold drives a UI-only flag.
              const active =
                pipeline.key === "alphafold" ? paramState.model_preset === "multimer" : colabfoldMultimer;
              const setMultimer = (v: boolean) =>
                pipeline.key === "alphafold"
                  ? onParamChange("model_preset", v ? "multimer" : "monomer")
                  : onColabfoldMultimerChange(v);
              return (
                <button
                  key={String(opt.value)}
                  type="button"
                  onClick={() => setMultimer(opt.value)}
                  className={`rounded-xl px-4 py-2 text-sm font-semibold transition ${
                    active === opt.value ? "bg-brand-500 text-white" : "text-slate-600 hover:text-brand-600"
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
          {pipeline.key === "alphafold" && (
            <p className="mt-1 text-xs text-slate-500">Batch submissions must use a single preset.</p>
          )}
        </div>
      )}
      {pipeline.supportsSequence && !multimerActive && (
        <div className="mt-4">
          <label className="text-sm font-semibold text-slate-600">서열 입력 (선택)</label>
          <textarea
            className="mt-1 w-full rounded-2xl border border-slate-200 px-4 py-3 font-mono"
            rows={4}
            value={sequence}
            onChange={(e) => onSequenceChange(e.target.value)}
            placeholder={">sp|P12345 예시\nMVTES..."}
          />
        </div>
      )}
      {pipeline.supportsSequence && multimerActive && (
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <label className="text-sm font-semibold text-slate-600">체인별 서열 입력 (복합체)</label>
            <span className="text-xs text-slate-400">
              {pipeline.key === "colabfold" ? "체인을 콜론(:)으로 이어 예측합니다" : "체인마다 FASTA 레코드로 전송됩니다"}
            </span>
          </div>
          <div className="mt-2 space-y-3">
            {multimerChains.map((chain, index) => (
              <div key={index} className="rounded-2xl border border-slate-200 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-brand-600">
                    체인 {String.fromCharCode(65 + index)}
                  </span>
                  {multimerChains.length > 2 && (
                    <button
                      type="button"
                      onClick={() => onMultimerChainsChange(multimerChains.filter((_, i) => i !== index))}
                      className="text-xs text-slate-400 underline hover:text-red-500"
                    >
                      삭제
                    </button>
                  )}
                </div>
                <textarea
                  className="mt-2 w-full rounded-xl border border-slate-200 px-4 py-2 font-mono"
                  rows={3}
                  value={chain}
                  onChange={(e) =>
                    onMultimerChainsChange(multimerChains.map((c, i) => (i === index ? e.target.value : c)))
                  }
                  placeholder={`체인 ${String.fromCharCode(65 + index)} 아미노산 서열 (예: MVTES...)`}
                />
              </div>
            ))}
          </div>
          <button
            type="button"
            onClick={() => onMultimerChainsChange([...multimerChains, ""])}
            className="mt-3 rounded-full border border-brand-200 px-4 py-2 text-sm font-semibold text-brand-700 hover:border-brand-400"
          >
            + 체인 추가
          </button>
        </div>
      )}
      {pipeline.key !== "diffdock" && (
      <div className="mt-6 rounded-2xl border border-dashed border-brand-200 bg-brand-50/50 p-4">
        <p className="text-sm font-semibold text-slate-700">입력 데이터</p>
        <p className="text-xs text-slate-500">
          폴더 업로드 버튼은 브라우저에서 즉시 ZIP으로 압축하여 올려줍니다. 대용량 데이터는 미리 ZIP으로 만들어 두면 더 빠르게 전송됩니다.
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <label className="flex cursor-pointer items-center gap-2 rounded-full bg-white px-4 py-2 text-sm font-semibold text-brand-700 shadow-sm">
            파일 선택
            <input
              type="file"
              multiple
              accept={acceptedFiles(pipeline).accept}
              className="hidden"
              onChange={(e) => {
                onFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
          <label className="flex cursor-pointer items-center gap-2 rounded-full bg-white px-4 py-2 text-sm font-semibold text-brand-700 shadow-sm">
            폴더 업로드
            <input
              type="file"
              className="hidden"
              multiple
              ref={folderInputRef}
              onChange={(e) => {
                onFolder(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        </div>
        <p className="mt-2 text-[11px] text-slate-400">허용 형식: {acceptedFiles(pipeline).hint}</p>
        {uploads.length > 0 ? (
          <ul className="mt-4 space-y-2 text-sm text-slate-600">
            {uploads.map((entry, index) => (
              <li key={`${entry.relativePath}-${index}`} className="flex items-center justify-between rounded-xl bg-white px-3 py-2">
                <span className="truncate">{entry.relativePath}</span>
                <button className="text-xs text-rose-500" onClick={() => onRemove(index)}>
                  제거
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-slate-400">업로드된 파일이 없습니다.</p>
        )}
        {uploadError && <p className="mt-2 text-sm text-rose-500">{uploadError}</p>}
      </div>
      )}
      <div className="mt-6 flex justify-end">
        <button
          onClick={onSubmit}
          disabled={submitting}
          className="rounded-2xl bg-slate-900 px-6 py-3 text-white shadow-lg shadow-slate-300 transition hover:bg-brand-700 disabled:opacity-40"
        >
          {submitting ? "실행 중..." : "작업 실행"}
        </button>
      </div>
    </div>
  );
}

type DiffdockJobsEditorProps = {
  jobs: DiffdockJobInput[];
  onChange: (jobs: DiffdockJobInput[]) => void;
  onFileChange: (index: number, kind: "protein" | "ligand", file: File | null) => void;
  onRemoveJob: (index: number) => void;
};

function DiffdockJobsEditor({ jobs, onChange, onFileChange, onRemoveJob }: DiffdockJobsEditorProps) {
  const updateJob = (index: number, field: keyof DiffdockJobInput, value: string | DiffdockJobInput["ligand_type"]) => {
    onChange(
      jobs.map((job, idx) => {
        if (idx !== index) return job;
        const next = { ...job, [field]: value };
        if (field === "ligand_type" && value === "smiles") {
          onFileChange(index, "ligand", null);
          next.ligand_description = "";
        }
        return next;
      })
    );
  };

  const addJob = () => onChange([...jobs, createBlankDiffdockJob()]);
  const removeJob = (index: number) => {
    if (jobs.length === 1) return;
    onRemoveJob(index);
  };

  return (
    <div className="space-y-4 rounded-2xl border border-slate-100 bg-slate-50 p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-700">DiffDock 복합체 목록</p>
          <p className="text-xs text-slate-500">Protein/Ligand 파일을 업로드하면 경로가 자동으로 채워집니다.</p>
        </div>
        <button type="button" className="text-sm font-semibold text-brand-600" onClick={addJob}>
          + 복합체 추가
        </button>
      </div>
      {jobs.map((job, index) => (
        <div key={`diffdock-${index}`} className="space-y-3 rounded-2xl bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-700">복합체 #{index + 1}</p>
            {jobs.length > 1 && (
              <button type="button" className="text-xs text-rose-500" onClick={() => removeJob(index)}>
                제거
              </button>
            )}
          </div>
          <div>
            <label className="text-xs font-semibold text-slate-600">Complex 이름</label>
            <input
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
              value={job.complex_name}
              onChange={(e) => updateJob(index, "complex_name", e.target.value)}
              placeholder="fold_1_01067"
            />
          </div>
          <div>
            <label className="text-xs font-semibold text-slate-600">Protein PDB 경로</label>
            <input
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
              value={job.protein_path}
              onChange={(e) => updateJob(index, "protein_path", e.target.value)}
              placeholder="inputs/fold_1_01067_model_0.pdb"
            />
            <div className="mt-2 text-xs text-slate-500">
              <label className="font-semibold">파일 업로드</label>
              <input
                type="file"
                accept=".pdb,.cif"
                className="mt-1 w-full text-xs"
                onChange={(e) => {
                  onFileChange(index, "protein", e.target.files?.[0] ?? null);
                  e.target.value = "";
                }}
              />
              <p className="mt-1 text-[11px] text-slate-400">파일을 선택하면 경로가 자동으로 설정됩니다.</p>
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-[0.4fr,0.6fr]">
            <div>
              <label className="text-xs font-semibold text-slate-600">Ligand 형식</label>
              <select
                className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
                value={job.ligand_type}
                onChange={(e) => updateJob(index, "ligand_type", e.target.value as DiffdockJobInput["ligand_type"])}
              >
                <option value="sdf">SDF 파일 경로</option>
                <option value="smiles">SMILES 문자열</option>
              </select>
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-600">
                {job.ligand_type === "sdf" ? "Ligand SDF 경로" : "Ligand SMILES"}
              </label>
              {job.ligand_type === "sdf" ? (
                <>
                  <input
                    className="mt-1 w-full rounded-xl border border-slate-200 bg-slate-100 px-3 py-2 text-sm"
                    value={job.ligand_description}
                    readOnly
                    placeholder="파일 업로드 시 자동으로 채워집니다."
                  />
                  <input
                    type="file"
                    accept=".sdf"
                    className="mt-2 w-full text-xs"
                    onChange={(e) => {
                      onFileChange(index, "ligand", e.target.files?.[0] ?? null);
                      e.target.value = "";
                    }}
                  />
                </>
              ) : (
                <input
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
                  value={job.ligand_description}
                  onChange={(e) => updateJob(index, "ligand_description", e.target.value)}
                  placeholder="CC1=CC=C(C=C1)O"
                />
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

type PhastestConfigFormProps = {
  config: PhastestConfig;
  onChange: (config: PhastestConfig) => void;
};

function PhastestConfigForm({ config, onChange }: PhastestConfigFormProps) {
  const handleChange = <K extends keyof PhastestConfig>(field: K, value: PhastestConfig[K]) => {
    onChange({
      ...config,
      [field]: value,
    });
  };

  return (
    <div className="space-y-4 rounded-2xl border border-slate-100 bg-slate-50 p-4">
      <p className="text-sm font-semibold text-slate-700">PHASTEST 입력 설정</p>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <label className="text-xs font-semibold text-slate-600">Input Type</label>
          <select
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={config.input_type}
            onChange={(e) => handleChange("input_type", e.target.value as PhastestConfig["input_type"])}
          >
            <option value="fasta">FASTA</option>
            <option value="contig">Contig</option>
            <option value="genbank">GenBank Accession</option>
          </select>
        </div>
        <div>
          <label className="text-xs font-semibold text-slate-600">Mode</label>
          <select
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={config.mode}
            onChange={(e) => handleChange("mode", e.target.value as PhastestConfig["mode"])}
          >
            <option value="lite">Lite</option>
            <option value="deep">Deep</option>
          </select>
        </div>
      </div>
      <div>
        <label className="text-xs font-semibold text-slate-600">샘플 이름</label>
        <input
          className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
          value={config.sample_name}
          onChange={(e) => handleChange("sample_name", e.target.value)}
          placeholder="CD_7908"
        />
      </div>
      {config.input_type === "genbank" ? (
        <div>
          <label className="text-xs font-semibold text-slate-600">NCBI Accession</label>
          <input
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={config.accession}
            onChange={(e) => handleChange("accession", e.target.value)}
            placeholder="KF030445.1"
          />
          <p className="mt-1 text-xs text-slate-500">GenBank 모드에서는 파일 업로드 없이 Accession만 전송합니다.</p>
        </div>
      ) : (
        <div>
          <label className="text-xs font-semibold text-slate-600">참고 메모 (선택)</label>
          <input
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={config.accession}
            onChange={(e) => handleChange("accession", e.target.value)}
            placeholder="필요 시 참고 Accession"
          />
          <p className="mt-1 text-xs text-slate-500">FASTA/Contig 모드는 반드시 파일을 업로드해야 합니다.</p>
        </div>
      )}
    </div>
  );
}

type JobTableProps = {
  jobs?: JobResponse[];
  loading: boolean;
  selectedJobId?: string;
  onSelect: (id: string) => void;
  onDownload: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
};

function JobTable({ jobs, loading, selectedJobId, onSelect, onDownload, onDelete }: JobTableProps) {
  return (
    <div className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-slate-900">내 작업</h3>
        <span className="text-sm text-slate-500">{jobs?.length || 0}건</span>
      </div>
      <div className="mt-4 space-y-2">
        {loading && <p className="text-sm text-slate-500">작업 목록을 불러오는 중...</p>}
        {!loading && (!jobs || jobs.length === 0) && <p className="text-sm text-slate-400">아직 실행한 작업이 없습니다.</p>}
        {jobs?.map((job) => (
          <div
            key={job.id}
            className={`rounded-2xl border p-3 ${selectedJobId === job.id ? "border-brand-400 bg-brand-50" : "border-slate-100 bg-white"}`}
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-semibold text-slate-900">{job.title}</p>
                <p className="text-xs text-slate-500">{job.pipeline.toUpperCase()} · {new Date(job.created_at).toLocaleString("ko-KR")}</p>
              </div>
              <JobStatusBadge status={job.status} />
            </div>
            <div className="mt-3 flex gap-2 text-xs text-slate-500">
              <button className="rounded-full border border-slate-200 px-3 py-1" onClick={() => onSelect(job.id)}>
                상세보기
              </button>
              <button className="rounded-full border border-slate-200 px-3 py-1" onClick={() => void onDownload(job.id)}>
                결과 다운로드
              </button>
              <button className="rounded-full border border-rose-200 px-3 py-1 text-rose-500" onClick={() => void onDelete(job.id)}>
                삭제
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

type ResultPanelProps = {
  job: JobResponse;
  token: string;
  onArtifactDownload: (jobId: string, artifactId: string, filename: string) => Promise<void>;
};

function ResultPanel({ job, token, onArtifactDownload }: ResultPanelProps) {
  const [viewerUrl, setViewerUrl] = useState<string | null>(null);
  const [htmlPreviewUrl, setHtmlPreviewUrl] = useState<string | null>(null);
  const [phastestViz, setPhastestViz] = useState<CgviewData | null>(null);
  const [phastestReports, setPhastestReports] = useState<Array<{ name: string; content: string }>>([]);
  const structureArtifacts = useMemo(
    () => job.artifacts.filter((artifact) => /\.(pdb|cif|sdf)$/i.test(artifact.file_name)),
    [job.artifacts]
  );
  const [selectedStructureId, setSelectedStructureId] = useState<string | null>(structureArtifacts[0]?.id ?? null);
  const selectedStructure = useMemo(
    () => structureArtifacts.find((artifact) => artifact.id === selectedStructureId) ?? structureArtifacts[0] ?? null,
    [structureArtifacts, selectedStructureId]
  );

  useEffect(() => {
    setSelectedStructureId(structureArtifacts[0]?.id ?? null);
  }, [job.id, structureArtifacts]);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    setViewerUrl(null);
    if (!selectedStructure) {
      return () => {
        if (revoked) URL.revokeObjectURL(revoked);
      };
    }
    (async () => {
      const blob = await downloadArtifact(job.id, selectedStructure.id, token);
      if (cancelled) return;
      const url = URL.createObjectURL(blob);
      revoked = url;
      setViewerUrl(url);
    })();
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [job.id, selectedStructure, token]);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    setHtmlPreviewUrl(null);
    const html = job.artifacts.find((artifact) => artifact.file_name.endsWith(".html"));
    if (!html) {
      return () => {
        if (revoked) URL.revokeObjectURL(revoked);
      };
    }
    (async () => {
      const blob = await downloadArtifact(job.id, html.id, token);
      if (cancelled) return;
      const url = URL.createObjectURL(blob);
      revoked = url;
      setHtmlPreviewUrl(url);
    })();
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [job.id, job.artifacts, token]);

  useEffect(() => {
    setPhastestViz(null);
    setPhastestReports([]);
    if (job.pipeline !== "phastest") return;
    let cancelled = false;
    const vizArtifact = job.artifacts.find((artifact) => artifact.file_name.endsWith("_output_for_viz.json"));
    if (vizArtifact) {
      (async () => {
        try {
          const blob = await downloadArtifact(job.id, vizArtifact.id, token);
          if (cancelled) return;
          setPhastestViz(JSON.parse(await blob.text()));
        } catch {
          if (!cancelled) setPhastestViz(null);
        }
      })();
    }
    const markdownArtifacts = job.artifacts.filter((artifact) => /summary\\.txt$|detail\\.txt$/i.test(artifact.file_name));
    if (markdownArtifacts.length) {
      (async () => {
        try {
          const entries = await Promise.all(
            markdownArtifacts.map(async (artifact) => {
              const blob = await downloadArtifact(job.id, artifact.id, token);
              return { name: artifact.file_name, content: await blob.text() };
            })
          );
          if (!cancelled) setPhastestReports(entries);
        } catch {
          if (!cancelled) setPhastestReports([]);
        }
      })();
    }
    return () => {
      cancelled = true;
    };
  }, [job.id, job.artifacts, job.pipeline, token]);

  return (
    <div className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-lg font-semibold text-slate-900">결과 패널</h3>
      <p className="text-sm text-slate-500">선택한 작업: {job.title}</p>
      {structureArtifacts.length > 0 && (
        <div className="mt-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-600">3D Structure Viewer</p>
            <div className="flex flex-wrap gap-2">
              {structureArtifacts.map((artifact) => (
                <button
                  key={artifact.id}
                  className={`rounded-full border px-3 py-1 text-xs ${
                    artifact.id === selectedStructureId
                      ? "border-brand-500 bg-brand-50 text-brand-600"
                      : "border-slate-200 text-slate-500"
                  }`}
                  onClick={() => setSelectedStructureId(artifact.id)}
                >
                  {artifact.file_name}
                </button>
              ))}
            </div>
          </div>
      {viewerUrl ? <NglViewer url={viewerUrl} fileName={selectedStructure?.file_name ?? null} /> : <p className="text-xs text-slate-400">구조 파일을 준비하는 중입니다.</p>}
        </div>
      )}
      {job.pipeline === "phastest" && phastestViz && (
        <div className="mt-4 space-y-3">
          <p className="text-sm text-slate-600">Circular Genome Viewer</p>
          <PhastestViewer data={phastestViz} />
        </div>
      )}
      {job.pipeline === "phastest" && htmlPreviewUrl && (
        <div className="mt-4 space-y-3">
          <p className="text-sm text-slate-600">PHASTEST 리포트</p>
          <iframe src={htmlPreviewUrl} className="h-80 w-full rounded-xl border border-slate-200" title="PHASTEST" />
        </div>
      )}
      {job.pipeline === "phastest" && phastestReports.length > 0 && (
        <div className="mt-4 space-y-3">
          <p className="text-sm text-slate-600">텍스트 리포트</p>
          {phastestReports.map((report) => (
            <div key={report.name} className="rounded-2xl border border-slate-100 bg-slate-50 p-4">
              <p className="text-xs font-semibold text-slate-500">{report.name}</p>
              <div className="prose prose-slate prose-sm mt-2 max-w-none">
                <ReactMarkdown>{report.content}</ReactMarkdown>
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="mt-4">
        <p className="text-sm font-semibold text-slate-700">아티팩트</p>
        <div className="mt-2 space-y-2">
          {job.artifacts.map((artifact) => (
            <div key={artifact.id} className="flex items-center justify-between rounded-xl border border-slate-100 px-3 py-2 text-sm text-slate-600">
              <div>
                <p className="font-semibold text-slate-800">{artifact.file_name}</p>
                <p className="text-xs text-slate-400">{(artifact.size_bytes / 1024).toFixed(1)} KB</p>
              </div>
              <button className="text-xs text-brand-600" onClick={() => onArtifactDownload(job.id, artifact.id, artifact.file_name)}>
                다운로드
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function translate(text?: string) {
  const dictionary: Record<string, string> = {
    "Model preset": "모델 프리셋",
    "DB preset": "DB 프리셋",
    "Max template date": "최대 템플릿 날짜",
    "Sample count": "샘플 개수",
    "Random seed": "랜덤 시드",
    "Report language": "리포트 언어",
    Full: "Full",
    Reduced: "Reduced",
    Monomer: "Monomer",
    Multimer: "Multimer",
    English: "English",
    Korean: "한국어",
  };
  return text ? dictionary[text] || text : "";
}
