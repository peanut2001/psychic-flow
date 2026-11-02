import type { Evaluation, EvaluationPage, Job, JobPasteResult, PoolAssignResult, PoolPasteResult, PoolResume, Resume, Run, RunPage, ScoringKind, ScoringPreview, TrashPage, TrashType } from "./types";

/** 页面填写的模型连接配置，只保存在本浏览器 localStorage。 */
export type ModelConfig = { apiKey: string; baseUrl: string; textModel: string; visionModel: string };

// 沿用旧项目名 hireflow，改名会让浏览器里已保存的配置失效。
const MODEL_CONFIG_KEY = "hireflow.model-config";

export function loadModelConfig(): ModelConfig | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(MODEL_CONFIG_KEY) || "null") as Partial<ModelConfig> | null;
    if (!value?.apiKey) return null;
    return { apiKey: value.apiKey, baseUrl: value.baseUrl || "", textModel: value.textModel || "", visionModel: value.visionModel || "" };
  } catch {
    return null;
  }
}

export function saveModelConfig(config: ModelConfig) {
  window.localStorage.setItem(MODEL_CONFIG_KEY, JSON.stringify(config));
}

export function clearModelConfig() {
  window.localStorage.removeItem(MODEL_CONFIG_KEY);
}

/** 有本地配置时，把它放进请求头随请求发给后端；后端只在本次请求及其触发的任务中使用。 */
function withModelConfig(init?: RequestInit): RequestInit | undefined {
  const config = loadModelConfig();
  if (!config) return init;
  const headers = new Headers(init?.headers);
  headers.set("X-Model-Api-Key", config.apiKey);
  if (config.baseUrl) headers.set("X-Model-Base-Url", config.baseUrl);
  if (config.textModel) headers.set("X-Model-Text", config.textModel);
  if (config.visionModel) headers.set("X-Model-Vision", config.visionModel);
  return { ...init, headers };
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, withModelConfig(init));
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // 非 JSON 错误保持通用提示。
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestText(url: string): Promise<string> {
  const response = await fetch(url, withModelConfig());
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // 非 JSON 错误保持通用提示。
    }
    throw new Error(message);
  }
  return response.text();
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<{ status: string; model_configured: boolean; model_source?: "browser" | "env"; text_model: string; vision_model: string; vision_enabled: boolean }>("/api/health"),
  listModels: (config: Pick<ModelConfig, "apiKey" | "baseUrl">) =>
    request<{ models: string[] }>("/api/model-config/models", json({ api_key: config.apiKey, base_url: config.baseUrl })),
  testModel: (config: ModelConfig) =>
    request<{ ok: boolean; checked_models: string[] }>("/api/model-config/test", json({ api_key: config.apiKey, base_url: config.baseUrl, text_model: config.textModel, vision_model: config.visionModel })),
  previewScoring: (prompt: string) => request<ScoringPreview>("/api/scoring-preview", json({ prompt })),
  jobs: () => request<Job[]>("/api/jobs"),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  createJob: (payload: Pick<Job, "name" | "jd_text" | "prompt"> & { scoring_profile?: { name: string; weight: number; kind: ScoringKind }[] }) =>
    request<Job>("/api/jobs", json(payload)),
  updateJob: (id: string, payload: Partial<Pick<Job, "name" | "jd_text" | "prompt">> & { scoring_profile?: { name: string; weight: number; kind: ScoringKind }[] }) =>
    request<Job>(`/api/jobs/${id}`, { ...json(payload), method: "PATCH" }),
  deleteJob: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),
  restoreJob: (id: string) => request<{ restored: boolean }>(`/api/trash/job/${id}/restore`, { method: "POST" }),
  resumes: (jobId: string) => request<Resume[]>(`/api/jobs/${jobId}/resumes`),
  poolResumes: (scope: "pool" | "all" = "pool") => request<PoolResume[]>(`/api/pool/resumes?scope=${scope}`),
  assignPool: (payload: { resume_ids: string[]; job_id: string; evaluate: boolean }) =>
    request<PoolAssignResult>("/api/pool/assign", json(payload)),
  pastePoolResume: (payload: { text: string; name?: string; confirm?: boolean }) =>
    request<PoolPasteResult>("/api/pool/resumes/paste", json(payload)),
  pasteJobResume: (jobId: string, payload: { text: string; name?: string; confirm?: boolean }) =>
    request<JobPasteResult>(`/api/jobs/${jobId}/resumes/paste`, json(payload)),
  resumeText: (resumeId: string) => requestText(`/api/resumes/${resumeId}/text`),
  updateResume: (resumeId: string, payload: { display_name?: string; parsed_text?: string }) =>
    request<Resume>(`/api/resumes/${resumeId}`, { ...json(payload), method: "PATCH" }),
  reparseResume: (resumeId: string) => request<Resume>(`/api/resumes/${resumeId}/reparse`, { method: "POST" }),
  deleteResume: (resumeId: string) => request<void>(`/api/resumes/${resumeId}`, { method: "DELETE" }),
  upload: async (jobId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    return request<{ uploaded: Resume[]; rejected: { filename: string; reason: string; recoverable_resume_id?: string | null }[] }>(
      `/api/jobs/${jobId}/resumes`,
      { method: "POST", body: form },
    );
  },
  runs: (jobId: string, params = new URLSearchParams()) => request<RunPage>(`/api/jobs/${jobId}/runs?${params.toString()}`),
  run: (id: string) => request<Run>(`/api/runs/${id}`),
  createRun: (jobId: string, resumeIds: string[]) => request<Run>(`/api/jobs/${jobId}/runs`, json({ resume_ids: resumeIds })),
  cancelRun: (id: string) => request<Run>(`/api/runs/${id}/cancel`, { method: "POST" }),
  retryRun: (id: string) => request<Run>(`/api/runs/${id}/retry-failed`, { method: "POST" }),
  deleteRun: (id: string) => request<void>(`/api/runs/${id}`, { method: "DELETE" }),
  evaluations: (runId: string) => request<Evaluation[]>(`/api/runs/${runId}/evaluations`),
  jobEvaluations: (jobId: string, params: URLSearchParams) =>
    request<EvaluationPage>(`/api/jobs/${jobId}/evaluations?${params.toString()}`),
  review: (id: string, status: Evaluation["review_status"], note: string) =>
    request<Evaluation>(`/api/evaluations/${id}/review`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note }),
    }),
  batchReview: (evaluationIds: string[], status: Evaluation["review_status"]) =>
    request<{ updated_count: number }>("/api/evaluations/review", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ evaluation_ids: evaluationIds, status }),
    }),
  rescore: (evaluationIds: string[], jobId?: string) => request<Run>("/api/evaluations/rescore", json({ evaluation_ids: evaluationIds, job_id: jobId })),
  deleteEvaluation: (id: string) => request<void>(`/api/evaluations/${id}`, { method: "DELETE" }),
  batchDeleteEvaluations: (evaluationIds: string[]) =>
    request<{ updated_count: number }>("/api/evaluations/delete", json({ evaluation_ids: evaluationIds })),
  trash: (params: URLSearchParams) => request<TrashPage>(`/api/trash?${params.toString()}`),
  restoreTrash: (itemType: TrashType, id: string) =>
    request<{ restored: boolean }>(`/api/trash/${itemType}/${id}/restore`, { method: "POST" }),
  purgeTrash: (itemType: TrashType, id: string) =>
    request<void>(`/api/trash/${itemType}/${id}`, { method: "DELETE" }),
  emptyTrash: () => request<{ updated_count: number }>("/api/trash", { method: "DELETE" }),
};
