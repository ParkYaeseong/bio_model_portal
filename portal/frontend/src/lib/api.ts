const browserApiBase =
  typeof window !== "undefined" ? process.env.NEXT_PUBLIC_BROWSER_API_BASE_URL ?? "" : null;

const serverApiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8400";

const API_BASE = browserApiBase ?? serverApiBase;

type FetchOptions = {
  method?: string;
  headers?: Record<string, string>;
  body?: BodyInit;
};

// Thrown when the SSO session is missing or expired. Callers surface this as a
// "로그인 하세요" prompt instead of a generic error.
export class AuthError extends Error {
  constructor(message = "로그인이 필요합니다. 다시 로그인 해주세요.") {
    super(message);
    this.name = "AuthError";
  }
}

// The portal sits behind a Caddy forward_auth SSO gateway. When the session is
// gone the backend answers 401, and same-origin /api calls get redirected to
// the login flow — both mean "re-authenticate".
function assertAuthenticated(response: Response): void {
  if (response.status === 401 || response.redirected) {
    throw new AuthError();
  }
}

async function apiFetch<T>(path: string, token?: string, options: FetchOptions = {}): Promise<T> {
  const headers: Record<string, string> = options.headers ? { ...options.headers } : {};
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = headers["Content-Type"] || "application/json";
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body,
  });
  assertAuthenticated(response);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "요청이 실패했습니다.");
  }
  if (response.status === 204) {
    return {} as T;
  }
  const text = await response.text();
  return text ? (JSON.parse(text) as T) : ({} as T);
}

export async function register(username: string, password: string) {
  return apiFetch("/api/auth/register", undefined, {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export async function login(username: string, password: string) {
  const body = new URLSearchParams();
  body.set("username", username);
  body.set("password", password);
  body.set("grant_type", "password");
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  if (!response.ok) {
    throw new Error("로그인에 실패했습니다.");
  }
  return response.json();
}

export const fetchPipelines = (token: string) => apiFetch<PipelineResponse>("/api/pipelines", token);
export const fetchJobs = (token: string) => apiFetch<JobResponse[]>("/api/jobs", token);
export const fetchJob = (jobId: string, token: string) => apiFetch<JobResponse>(`/api/jobs/${jobId}`, token);

export async function createJob(form: FormData, token: string) {
  return apiFetch<JobResponse>("/api/jobs", token, {
    method: "POST",
    body: form,
  });
}

export const deleteJob = (jobId: string, token: string) =>
  apiFetch(`/api/jobs/${jobId}`, token, { method: "DELETE" });

export async function downloadArtifact(jobId: string, artifactId: string, token: string) {
  const response = await fetch(`${API_BASE}/api/jobs/${jobId}/artifacts/${artifactId}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  assertAuthenticated(response);
  if (!response.ok) {
    throw new Error("파일 다운로드에 실패했습니다.");
  }
  const blob = await response.blob();
  return blob;
}

export async function downloadArchive(jobId: string, token: string) {
  const response = await fetch(`${API_BASE}/api/jobs/${jobId}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  assertAuthenticated(response);
  if (!response.ok) {
    throw new Error("결과를 다운로드할 수 없습니다.");
  }
  const blob = await response.blob();
  return blob;
}

export interface ContigOption {
  id: string;
  label: string;
  contig: string;
  recommended: boolean;
}

export interface ContigSuggestions {
  chains: string[];
  options: ContigOption[];
  processed_coords: boolean;
}

export async function fetchContigSuggestions(file: File, token: string): Promise<ContigSuggestions> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API_BASE}/api/rfdiffusion/contig-suggestions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: fd,
  });
  if (!res.ok) {
    return {
      chains: [],
      options: [{ id: "custom", label: "직접 입력", contig: "", recommended: true }],
      processed_coords: true,
    };
  }
  return res.json();
}

export interface PipelineResponse {
  retentionDays: number;
  pipelines: PipelineMeta[];
}

export interface PipelineMeta {
  key: string;
  label: string;
  description: string;
  instructions: string;
  supportsSequence: boolean;
  requiresArchive: boolean;
  previewKind: string;
  inputFields: Array<{
    name: string;
    label: string;
    field_type: string;
    required?: boolean;
    options?: Array<{ value: string; label: string }>;
    placeholder?: string;
    helper?: string;
    minimum?: number | null;
  }>;
}

export interface ArtifactMeta {
  id: string;
  file_name: string;
  kind: string;
  mime_type: string | null;
  size_bytes: number;
}

export interface JobResponse {
  id: string;
  title: string;
  pipeline: string;
  status: string;
  runpod_job_id: string | null;
  created_at: string;
  updated_at: string;
  expires_at: string;
  notes?: string | null;
  preferred_download_dir?: string | null;
  parameters: Record<string, unknown>;
  artifacts: ArtifactMeta[];
}

export type AssistantRequest = {
  job_id: string;
  message: string;
  artifact_id?: string | null;
};

export type AssistantResponse = {
  reply: string;
};

export const askAssistant = (payload: AssistantRequest, token: string) =>
  apiFetch<AssistantResponse>("/api/assistant", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export interface WorkflowSummary {
  id: string;
  name: string;
  description?: string | null;
  template_key: string;
  last_run_status: string | null;
  created_at: string;
}

export interface WorkflowRunStep {
  order: number;
  step_name: string;
  worker_name: string;
  status: string;
  job_id: string | null;
  metrics: Record<string, unknown> | null;
  error_message: string | null;
  logs: string | null;
}

export interface WorkflowRunDetail {
  id: string;
  status: string;
  error_message: string | null;
  input_summary: Record<string, unknown> | null;
  output_summary: Record<string, unknown> | null;
  steps: WorkflowRunStep[];
}

export const listWorkflows = (token: string) =>
  apiFetch<{ workflows: WorkflowSummary[] }>("/api/workflows", token);

export const instantiateWorkflow = (token: string, body: { template_key: string; name?: string }) =>
  apiFetch<WorkflowSummary>("/api/workflows", token, { method: "POST", body: JSON.stringify(body) });

export const uploadWorkflowInput = async (token: string, file: File): Promise<{ backbone_path: string; file_name: string }> => {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<{ backbone_path: string; file_name: string }>("/api/workflows/upload", token, { method: "POST", body: form });
};

export const startWorkflowRun = (
  token: string,
  workflowId: string,
  body: { sequence?: string; backbone_path?: string; step_params?: Record<string, unknown> },
) => apiFetch<{ id: string; status: string }>(`/api/workflows/${workflowId}/runs`, token, { method: "POST", body: JSON.stringify(body) });

export const getWorkflowRun = (token: string, runId: string) =>
  apiFetch<WorkflowRunDetail>(`/api/workflows/runs/${runId}`, token);

export const getWorkflowReport = (token: string, runId: string) =>
  apiFetch<{ run_id: string; status: string; candidates: Array<Record<string, unknown>> }>(
    `/api/workflows/runs/${runId}/report`, token,
  );

export const cancelWorkflowRun = (token: string, runId: string) =>
  apiFetch<{ id: string; status: string }>(`/api/workflows/runs/${runId}/cancel`, token, { method: "POST" });

export interface McpToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export const listMcpTokens = (token: string) =>
  apiFetch<{ tokens: McpToken[] }>("/api/mcp/tokens", token);

export const createMcpToken = (token: string, name: string) =>
  apiFetch<McpToken & { token: string }>("/api/mcp/tokens", token, {
    method: "POST",
    body: JSON.stringify({ name }),
  });

export const revokeMcpToken = (token: string, id: string) =>
  apiFetch<{ ok: boolean }>(`/api/mcp/tokens/${id}/revoke`, token, { method: "POST" });
