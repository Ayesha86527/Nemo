import axios, { AxiosError, AxiosInstance } from "axios";

declare global {
  interface Window {
    nemoAPI: {
      getBackendUrl: () => string;
      getBackendError?: () => string;
      getAuthToken?: () => string;
    };
  }
}

export interface Settings {
  custom_base_url: string;
  custom_api_key_set: boolean;
  cloud_model: string;
}

export interface Profile {
  name: string;
  email: string;
  target_roles: string;
  education: string;
  short_term_goal?: string;
  long_term_goal?: string;
}

export interface CVStatus {
  has_file: boolean;
  filename: string | null;
  uploaded_at: string | null;
  has_content: boolean;
  content_updated_at: string | null;
}

export interface CVUploadResult extends CVStatus {
  ingested: boolean;
  ingest_error: string | null;
}

export interface ExperienceItem {
  role: string;
  company: string;
  start: string;
  end: string;
  description: string;
}

export interface ProjectItem {
  name: string;
  tech: string;
  description: string;
}

export interface CVContentData {
  skills: string[];
  experience: ExperienceItem[];
  projects: ProjectItem[];
  achievements: string[];
  updated_at: string | null;
}

export interface SkillGap {
  skill: string;
  status: "have" | "partial" | "missing";
  note: string;
}

export interface MarketSourceRef {
  claim: string;
  source: string;
}

export interface MarketReportData {
  match_score: number | null;
  summary: string;
  skill_gaps: SkillGap[];
  market_signals: string[];
  recommendations: string[];
  sources?: MarketSourceRef[];
}

export interface MarketStatus {
  last_run_at: string | null;
  due: boolean;
}

export interface MarketReport {
  id: number;
  created_at: string;
  target_role: string;
  report: MarketReportData;
  provider: string;
}

export type JobStatus = "wishlist" | "applied" | "interview" | "offer" | "rejected";

export interface JobApplication {
  id: number;
  company: string;
  role: string;
  job_description: string;
  status: JobStatus;
  applied_at: string;
  follow_up_at: string;
  notes: string;
  match_score: number | null;
  match_summary: string;
  research: string;
  linkedin_dm: string;
  aligned: boolean;
  prepared: boolean;
  prepared_at: string;
  created_at: string;
}

export interface CoverLetterData {
  id: number;
  job_id: number;
  content: string;
  provider: string;
  created_at: string;
}

export interface RoadmapStep {
  task: string;
  done: boolean;
}

export interface RoadmapMilestone {
  title: string;
  focus: string;
  steps: RoadmapStep[];
}

export interface RoadmapData {
  id: number;
  target_role: string;
  goal: string;
  horizon_weeks: number;
  focus: string;
  preferences: string;
  milestones: RoadmapMilestone[];
  progress: { done: number; total: number; percent: number };
  provider: string;
  created_at: string;
}

export interface AgentHistoryItem {
  role: "user" | "agent";
  content: string;
  created_at: string;
}

export interface AgentChatResponse {
  reply: string;
  changes: string[];
  cv_updated: boolean;
}

export interface TailorOutcome {
  blob: Blob;
  filename: string;
  editsApplied: number;
  editsSkipped: number;
  summary: string;
}

export class ApiRequestError extends Error {
  hint: string;
  status: number;

  constructor(message: string, hint: string, status: number) {
    super(message);
    this.hint = hint;
    this.status = status;
  }
}

let _client: AxiosInstance | null = null;
let _clientUrl = "";

export function getClient(): AxiosInstance {
  // The backend port is delivered after page load; rebuild the client when it lands.
  const baseURL = window.nemoAPI.getBackendUrl();
  if (!_client || _clientUrl !== baseURL) {
    _client = axios.create({
      baseURL,
      // Above the backend's worst case with automatic retries (3x120s + backoff).
      timeout: 420000,
      headers: {
        "Content-Type": "application/json",
        "X-Nemo-Token": window.nemoAPI.getAuthToken?.() || "",
      },
    });
    _clientUrl = baseURL;
  }
  return _client;
}

function extractError(err: unknown): ApiRequestError {
  if (err instanceof AxiosError) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "object" && detail?.error) {
      return new ApiRequestError(detail.error, detail.hint || "", err.response?.status || 500);
    }
    if (typeof detail === "string") {
      return new ApiRequestError(detail, "", err.response?.status || 500);
    }
    if (err.response?.data?.message) {
      return new ApiRequestError(String(err.response.data.message), "", err.response.status);
    }
    return new ApiRequestError(err.message, "Check that the backend is running.", 0);
  }
  return new ApiRequestError(String(err), "", 0);
}

async function extractBlobError(err: unknown): Promise<ApiRequestError> {
  if (err instanceof AxiosError && err.response?.data instanceof Blob) {
    const text = await err.response.data.text();
    try {
      const parsed = JSON.parse(text);
      const detail = parsed.detail;
      if (typeof detail === "object" && detail?.error) {
        return new ApiRequestError(detail.error, detail.hint || "", err.response.status);
      }
      return new ApiRequestError(text, "", err.response.status);
    } catch {
      return new ApiRequestError(text, "", err.response.status);
    }
  }
  return extractError(err);
}

function filenameFromDisposition(disposition: string, fallback: string): string {
  const match = String(disposition || "").match(/filename="?([^";]+)"?/);
  return match?.[1] || fallback;
}

export async function healthCheck(): Promise<boolean> {
  if (!window.nemoAPI.getBackendUrl()) return false;
  try {
    const resp = await getClient().get("/api/health", { timeout: 5000 });
    return resp.data?.status === "ok";
  } catch {
    return false;
  }
}

export async function getSettings(): Promise<Settings> {
  return (await getClient().get("/api/settings")).data;
}

export async function updateSettings(
  patch: Partial<Omit<Settings, "custom_api_key_set">> & { custom_api_key?: string }
): Promise<Settings> {
  return (await getClient().put("/api/settings", patch)).data;
}

export async function getProfile(): Promise<Profile> {
  return (await getClient().get("/api/profile")).data;
}

export async function updateProfile(patch: Partial<Profile>): Promise<Profile> {
  return (await getClient().put("/api/profile", patch)).data;
}

export async function getCVStatus(): Promise<CVStatus> {
  return (await getClient().get("/api/cv/status")).data;
}

export async function uploadCVFile(file: File): Promise<CVUploadResult> {
  const form = new FormData();
  form.append("file", file);
  try {
    return (await getClient().post("/api/cv/file", form, { headers: { "Content-Type": "multipart/form-data" } }))
      .data;
  } catch (err) {
    throw await extractBlobError(err);
  }
}

export async function downloadCVFile(): Promise<{ blob: Blob; filename: string }> {
  const resp = await getClient().get("/api/cv/file", { responseType: "blob" });
  return {
    blob: resp.data,
    filename: filenameFromDisposition(String(resp.headers["content-disposition"] || ""), "cv.docx"),
  };
}

export async function deleteCVFile(): Promise<CVStatus> {
  return (await getClient().delete("/api/cv/file")).data;
}

export async function getCVContent(): Promise<CVContentData> {
  return (await getClient().get("/api/cv/content")).data;
}

export async function replaceCVFile(): Promise<CVStatus> {
  try {
    return (await getClient().post("/api/cv/replace")).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function renderCV(format: "docx" | "pdf" = "docx"): Promise<{ blob: Blob; filename: string }> {
  try {
    const resp = await getClient().post(
      `/api/cv/render?format=${format}`,
      {},
      { responseType: "blob" }
    );
    return {
      blob: resp.data,
      filename: filenameFromDisposition(
        String(resp.headers["content-disposition"] || ""),
        format === "pdf" ? "cv.pdf" : "cv.docx"
      ),
    };
  } catch (err) {
    throw await extractBlobError(err);
  }
}

export async function getMarketStatus(): Promise<MarketStatus> {
  return (await getClient().get("/api/market/status")).data;
}

export async function runMarketIntel(targetRole: string): Promise<MarketReport> {
  try {
    return (await getClient().post("/api/market/run", { target_role: targetRole })).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function listMarketReports(limit = 10): Promise<MarketReport[]> {
  return (await getClient().get("/api/market/reports", { params: { limit } })).data;
}

// --- Job tracker + gated preparation workflow -------------------------------

export async function listJobs(): Promise<JobApplication[]> {
  return (await getClient().get("/api/jobs")).data;
}

export async function createJob(
  payload: Pick<JobApplication, "company" | "role"> &
    Partial<Pick<JobApplication, "job_description" | "status" | "applied_at" | "follow_up_at" | "notes">>
): Promise<JobApplication> {
  try {
    return (await getClient().post("/api/jobs", payload)).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function updateJob(jobId: number, patch: Partial<Omit<JobApplication, "id">>): Promise<JobApplication> {
  try {
    return (await getClient().put(`/api/jobs/${jobId}`, patch)).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function deleteJob(jobId: number) {
  return (await getClient().delete(`/api/jobs/${jobId}`)).data;
}

export async function prepareJob(jobId: number): Promise<JobApplication> {
  try {
    return (await getClient().post(`/api/jobs/${jobId}/prepare`)).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function tailorJob(jobId: number): Promise<TailorOutcome> {
  try {
    const resp = await getClient().post(`/api/jobs/${jobId}/tailor`, {}, { responseType: "blob" });
    return {
      blob: resp.data,
      filename: filenameFromDisposition(String(resp.headers["content-disposition"] || ""), "cv_tailored.docx"),
      editsApplied: Number(resp.headers["x-edits-applied"] || 0),
      editsSkipped: Number(resp.headers["x-edits-skipped"] || 0),
      summary: decodeURIComponent(escape(String(resp.headers["x-tailor-summary"] || ""))),
    };
  } catch (err) {
    throw await extractBlobError(err);
  }
}

export async function generateCoverLetter(jobId: number): Promise<CoverLetterData> {
  try {
    return (await getClient().post(`/api/jobs/${jobId}/cover-letter`)).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function listCoverLetters(jobId: number): Promise<CoverLetterData[]> {
  return (await getClient().get(`/api/jobs/${jobId}/cover-letters`)).data;
}

export interface QuickRequest {
  job_description: string;
  company?: string;
  role?: string;
}

export async function quickAnalyzeJob(payload: QuickRequest): Promise<JobApplication> {
  try {
    return (await getClient().post("/api/jobs/quick-analyze", payload)).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function generateLinkedInDM(jobId: number): Promise<JobApplication> {
  try {
    return (await getClient().post(`/api/jobs/${jobId}/linkedin-dm`)).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function resetAllData(includeSettings = false): Promise<{ wiped: string[] }> {
  return (
    await getClient().post(`/api/system/reset?include_settings=${includeSettings}`)
  ).data;
}

export async function findOrCreateJob(payload: QuickRequest): Promise<JobApplication> {
  try {
    return (await getClient().post("/api/jobs/find-or-create", payload)).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function quickTailorJob(payload: QuickRequest): Promise<TailorOutcome & { jobId: number }> {
  try {
    const resp = await getClient().post("/api/jobs/quick-tailor", payload, { responseType: "blob" });
    return {
      blob: resp.data,
      filename: filenameFromDisposition(String(resp.headers["content-disposition"] || ""), "cv_tailored.docx"),
      editsApplied: Number(resp.headers["x-edits-applied"] || 0),
      editsSkipped: Number(resp.headers["x-edits-skipped"] || 0),
      summary: decodeURIComponent(escape(String(resp.headers["x-tailor-summary"] || ""))),
      jobId: Number(resp.headers["x-job-id"] || 0),
    };
  } catch (err) {
    throw await extractBlobError(err);
  }
}

export async function quickCoverLetter(
  payload: QuickRequest
): Promise<{ job: JobApplication; letter: CoverLetterData }> {
  try {
    return (await getClient().post("/api/jobs/quick-cover-letter", payload)).data;
  } catch (err) {
    throw extractError(err);
  }
}

// --- Nemo agent chat ---------------------------------------------------------

export async function agentChat(message: string, context = ""): Promise<AgentChatResponse> {
  try {
    return (await getClient().post("/api/agent/chat", { message, context })).data;
  } catch (err) {
    throw extractError(err);
  }
}

export type AgentStreamEvent =
  | { type: "chunk"; text: string }
  | { type: "done"; reply: string; changes: string[]; cv_updated: boolean }
  | { type: "error"; error: string; hint: string };

/**
 * Streaming agent chat via Server-Sent Events.
 * Calls the callback for each parsed event until "done" or "error" arrives.
 * Returns the final AgentChatResponse on success, throws ApiRequestError on failure.
 */
export async function agentStream(
  message: string,
  context: string,
  onChunk: (text: string) => void
): Promise<AgentChatResponse> {
  const baseURL = window.nemoAPI.getBackendUrl();
  const resp = await fetch(`${baseURL}/api/agent/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Nemo-Token": window.nemoAPI.getAuthToken?.() || "",
    },
    body: JSON.stringify({ message, context }),
    signal: AbortSignal.timeout(420_000),
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = body?.detail?.error || body?.detail || detail;
    } catch { /* ignore */ }
    throw new ApiRequestError(String(detail), "Check that the backend is running.", resp.status);
  }

  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n\n");
    buffer = lines.pop() ?? "";
    for (const block of lines) {
      const dataLine = block.split("\n").find((l) => l.startsWith("data: "));
      if (!dataLine) continue;
      try {
        const event: AgentStreamEvent = JSON.parse(dataLine.slice(6));
        if (event.type === "chunk") {
          onChunk(event.text);
        } else if (event.type === "error") {
          throw new ApiRequestError(event.error, event.hint, 502);
        } else if (event.type === "done") {
          return { reply: event.reply, changes: event.changes, cv_updated: event.cv_updated };
        }
      } catch (e) {
        if (e instanceof ApiRequestError) throw e;
        // malformed SSE line — skip
      }
    }
  }
  throw new ApiRequestError("Stream ended without a done event.", "Try again.", 502);
}


export async function agentHistory(limit = 100): Promise<AgentHistoryItem[]> {
  return (await getClient().get("/api/agent/history", { params: { limit } })).data;
}

export async function getAgentGreeting(): Promise<{
  greeting: string;
  onboarded: boolean;
  missing: string[];
}> {
  return (await getClient().get("/api/agent/greeting")).data;
}

export async function clearAgentHistory() {
  return (await getClient().delete("/api/agent/history")).data;
}

export async function transcribeAudio(blob: Blob): Promise<string> {
  const form = new FormData();
  form.append("file", blob, "recording.webm");
  try {
    const resp = await getClient().post("/api/speech/transcribe", form, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300000,
    });
    return String(resp.data.text || "");
  } catch (err) {
    throw await extractBlobError(err);
  }
}

// --- Roadmap ------------------------------------------------------------------

export interface RoadmapRequestOptions {
  horizonWeeks?: number;
  focus?: string;
  preferences?: string;
}

export async function generateRoadmap(
  targetRole = "",
  { horizonWeeks, focus, preferences }: RoadmapRequestOptions = {}
): Promise<RoadmapData> {
  try {
    const payload: { target_role: string; horizon_weeks?: number; focus?: string; preferences?: string } = {
      target_role: targetRole,
    };
    if (horizonWeeks) payload.horizon_weeks = horizonWeeks;
    if (focus?.trim()) payload.focus = focus.trim();
    if (preferences?.trim()) payload.preferences = preferences.trim();
    return (await getClient().post("/api/roadmap/generate", payload)).data;
  } catch (err) {
    throw extractError(err);
  }
}

export async function latestRoadmap(): Promise<RoadmapData | null> {
  try {
    return (await getClient().get("/api/roadmap/latest")).data;
  } catch (err) {
    const e = extractError(err);
    if (e.status !== 404) throw e;
    return null;
  }
}

export async function updateRoadmapStep(milestone: number, step: number, done: boolean): Promise<RoadmapData> {
  try {
    return (await getClient().put("/api/roadmap/step", { milestone, step, done })).data;
  } catch (err) {
    throw extractError(err);
  }
}

/**
 * Strip markdown emphasis/heading artifacts from LLM plain-text output
 * (**bold**, __it__, # headings, `code ticks`) so report panels never show
 * raw asterisks. Paragraph structure (line breaks, - bullets) is preserved.
 */
export function stripMarkdown(text: string): string {
  return String(text || "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|\s)\*([^*\n]+)\*(?=\s|$|[.,!?])/g, "$1$2")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^---+$/gm, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
