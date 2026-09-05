import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  CoverLetterData,
  JobApplication,
  JobStatus,
  deleteJob,
  downloadBlob,
  generateCoverLetter,
  listCoverLetters,
  listJobs,
  prepareJob,
  quickCoverLetter,
  quickTailorJob,
  tailorJob,
  updateJob,
} from "../api/client";

const STATUSES: JobStatus[] = ["wishlist", "applied", "interview", "offer", "rejected"];

const STATUS_COLORS: Record<JobStatus, string> = {
  wishlist: "var(--text-dim)",
  applied: "var(--accent)",
  interview: "var(--yellow)",
  offer: "var(--green)",
  rejected: "var(--red)",
};

function isFollowUpDue(job: JobApplication): "overdue" | "soon" | "" {
  if (!job.follow_up_at) return "";
  const date = new Date(job.follow_up_at);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = (date.getTime() - today.getTime()) / 86400000;
  if (diffDays < 0) return "overdue";
  if (diffDays <= 3) return "soon";
  return "";
}

interface ActionState {
  busy: string;
  error: string;
  hint: string;
  success: string;
}

const IDLE: ActionState = { busy: "", error: "", hint: "", success: "" };

function JobRow({ job }: { job: JobApplication }) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [ui, setUi] = useState<ActionState>(IDLE);
  const [letters, setLetters] = useState<CoverLetterData[] | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["jobs"] });
  const fail = (err: unknown) =>
    setUi({
      ...IDLE,
      error: err instanceof ApiRequestError ? err.message : String(err),
      hint: err instanceof ApiRequestError ? err.hint : "",
    });

  const followUp = isFollowUpDue(job);

  const onPrepare = async () => {
    setUi({ ...IDLE, busy: "Running role-match analysis + company research…" });
    try {
      await prepareJob(job.id);
      setUi({ ...IDLE, success: "Preparation complete — match analysis and research saved." });
      refresh();
    } catch (err) {
      fail(err);
    }
  };

  const onTailor = async () => {
    setUi({ ...IDLE, busy: "Tailoring CV (layout preserved)…" });
    try {
      const result = await tailorJob(job.id);
      downloadBlob(result.blob, result.filename);
      setUi({
        ...IDLE,
        success: `Tailored CV downloaded (${result.editsApplied} edits applied, ${result.editsSkipped} skipped). ${result.summary}`,
      });
    } catch (err) {
      fail(err);
    }
  };

  const onCoverLetter = async () => {
    setUi({ ...IDLE, busy: "Writing cover letter…" });
    try {
      await generateCoverLetter(job.id);
      const list = await listCoverLetters(job.id);
      setLetters(list);
      setUi({ ...IDLE, success: "Cover letter generated and saved." });
    } catch (err) {
      fail(err);
    }
  };

  const onToggle = async () => {
    const next = !expanded;
    setExpanded(next);
    setUi(IDLE);
    if (next && letters === null) {
      try {
        setLetters(await listCoverLetters(job.id));
      } catch {
        setLetters([]);
      }
    }
  };

  const onDelete = async () => {
    await deleteJob(job.id);
    refresh();
  };

  return (
    <>
      <tr className={followUp === "overdue" ? "row-overdue" : ""}>
        <td>
          <button className="linklike" onClick={onToggle}>
            {expanded ? "▾" : "▸"} <strong>{job.company}</strong>
          </button>
          <div className="job-role">{job.role}</div>
        </td>
        <td>
          <select
            className="status-select"
            value={job.status}
            style={{ color: STATUS_COLORS[job.status] }}
            onChange={async (e) => {
              await updateJob(job.id, { status: e.target.value as JobStatus });
              refresh();
            }}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </td>
        <td>
          {job.prepared ? (
            <span className="badge badge-green" title={job.match_summary || undefined}>
              {job.match_score != null ? `${job.match_score}% match` : "prepared"}
            </span>
          ) : (
            <span className="badge">not prepared</span>
          )}
        </td>
        <td>
          <input
            type="date"
            className="date-inline"
            value={job.follow_up_at?.slice(0, 10) || ""}
            onChange={async (e) => {
              await updateJob(job.id, { follow_up_at: e.target.value });
              refresh();
            }}
          />
          {followUp === "overdue" && <div className="followup-tag overdue">follow-up overdue</div>}
          {followUp === "soon" && <div className="followup-tag soon">follow-up soon</div>}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={4} className="job-detail">
            {ui.busy && <p className="msg-info">{ui.busy}</p>}
            {ui.success && <div className="msg-success">{ui.success}</div>}
            {ui.error && (
              <div className="msg-error">
                {ui.error}
                {ui.hint && <span className="hint">💡 {ui.hint}</span>}
              </div>
            )}

            <div className="job-actions">
              <button className="primary" style={{ marginTop: 0 }} onClick={onPrepare} disabled={Boolean(ui.busy) || !job.job_description}>
                {job.prepared ? "Re-run preparation" : "Prepare (match + research)"}
              </button>
              <button className="ghost" onClick={onTailor} disabled={Boolean(ui.busy) || !job.prepared}>
                Tailor CV
              </button>
              <button className="ghost" onClick={onCoverLetter} disabled={Boolean(ui.busy) || !job.prepared}>
                Generate cover letter
              </button>
              <button className="ghost" onClick={onDelete}>Delete job</button>
            </div>
            {!job.job_description && (
              <p className="msg-info">Add the job description below, then run Prepare.</p>
            )}
            {!job.prepared && job.job_description && (
              <p className="msg-info">
                Nemo requires role-match analysis + company research before tailoring or cover letters.
              </p>
            )}

            <label>Job description</label>
            <textarea
              rows={6}
              defaultValue={job.job_description}
              onBlur={async (e) => {
                if (e.target.value !== job.job_description) {
                  await updateJob(job.id, { job_description: e.target.value });
                  refresh();
                }
              }}
              placeholder="Paste the full job posting…"
            />

            {job.prepared && (
              <>
                <label>Match analysis</label>
                <div className="report" style={{ maxHeight: 140 }}>
                  {job.match_score != null ? `Match score: ${job.match_score}%\n` : ""}
                  {job.match_summary || "(no summary)"}
                </div>
                <label>Company / role research</label>
                <div className="report" style={{ maxHeight: 220 }}>{job.research}</div>
              </>
            )}

            {letters && letters.length > 0 && (
              <>
                <label>Cover letters</label>
                {letters.map((letter) => (
                  <div className="letter" key={letter.id}>
                    <div className="letter-head">
                      <span>{new Date(letter.created_at).toLocaleString()} · {letter.provider}</span>
                      <div className="cv-file-actions">
                        <button className="ghost" onClick={() => navigator.clipboard?.writeText(letter.content)}>
                          Copy
                        </button>
                        <button
                          className="ghost"
                          onClick={() =>
                            downloadBlob(new Blob([letter.content], { type: "text/plain" }), `CoverLetter_${job.company}.txt`)
                          }
                        >
                          Download
                        </button>
                      </div>
                    </div>
                    <div className="report" style={{ maxHeight: 260 }}>{letter.content}</div>
                  </div>
                ))}
              </>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function Dashboard() {
  const queryClient = useQueryClient();
  const jobsQ = useQuery({ queryKey: ["jobs"], queryFn: listJobs });

  const [qJd, setQJd] = useState("");
  const [qCompany, setQCompany] = useState("");
  const [qRole, setQRole] = useState("");
  const [qBusy, setQBusy] = useState<"" | "tailor" | "letter">("");
  const [qError, setQError] = useState("");
  const [qHint, setQHint] = useState("");
  const [qSuccess, setQSuccess] = useState("");

  const jobs = jobsQ.data ?? [];
  const active = jobs.filter((j) => j.status === "applied" || j.status === "interview").length;
  const interviews = jobs.filter((j) => j.status === "interview").length;
  const dueFollowUps = jobs.filter((j) => isFollowUpDue(j)).length;

  const quickRun = async (mode: "tailor" | "letter") => {
    setQError("");
    setQHint("");
    setQSuccess("");
    if (!qJd.trim()) {
      setQError("Paste the job description first.");
      return;
    }
    setQBusy(mode);
    const payload = { job_description: qJd.trim(), company: qCompany.trim(), role: qRole.trim() };
    try {
      if (mode === "tailor") {
        const out = await quickTailorJob(payload);
        downloadBlob(out.blob, out.filename);
        setQSuccess(
          `Job tracked automatically. Tailored CV downloaded (${out.editsApplied} edits applied, ${out.editsSkipped} skipped). ${out.summary}`
        );
      } else {
        const out = await quickCoverLetter(payload);
        setQSuccess(
          `Job tracked automatically — cover letter saved for ${out.job.company} (${out.job.role}). Expand the row below to view or copy it.`
        );
      }
      setQJd("");
      setQCompany("");
      setQRole("");
    } catch (err) {
      setQError(err instanceof ApiRequestError ? err.message : String(err));
      if (err instanceof ApiRequestError) setQHint(err.hint);
    } finally {
      setQBusy("");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    }
  };

  return (
    <>
      <h1>Job Tracker</h1>
      <p className="subtitle">
        No manual entries — tailor a CV or write a cover letter for any posting and the application
        is tracked here automatically (match analysis + research included).
      </p>

      <div className="stat-grid">
        <div className="stat">
          <div className="value">{jobs.length}</div>
          <div className="label">Tracked jobs</div>
        </div>
        <div className="stat">
          <div className="value">{active}</div>
          <div className="label">Active applications</div>
        </div>
        <div className="stat">
          <div className="value">{interviews}</div>
          <div className="label">Interviews</div>
        </div>
        <div className="stat">
          <div className="value" style={{ color: dueFollowUps ? "var(--yellow)" : undefined }}>
            {dueFollowUps}
          </div>
          <div className="label">Follow-ups due</div>
        </div>
      </div>

      <div className="card">
        <h2>Quick apply — zero entry</h2>
        <p className="msg-info" style={{ marginTop: 0 }}>
          Paste a job posting and go. Nemo runs the mandatory role-match analysis + company research,
          then tracks the application here automatically.
        </p>
        <div className="row">
          <input
            value={qCompany}
            onChange={(e) => setQCompany(e.target.value)}
            placeholder="Company (optional — Nemo extracts it from the posting)"
          />
          <input
            value={qRole}
            onChange={(e) => setQRole(e.target.value)}
            placeholder="Role (optional — Nemo extracts it from the posting)"
          />
        </div>
        <textarea
          rows={5}
          value={qJd}
          onChange={(e) => setQJd(e.target.value)}
          placeholder="Paste the full job description…"
        />
        <div className="job-actions">
          <button className="primary" style={{ marginTop: 0 }} onClick={() => quickRun("tailor")} disabled={Boolean(qBusy)}>
            {qBusy === "tailor" ? "Preparing + tailoring…" : "Tailor my CV for this job"}
          </button>
          <button className="ghost" onClick={() => quickRun("letter")} disabled={Boolean(qBusy)}>
            {qBusy === "letter" ? "Preparing + writing…" : "Write cover letter"}
          </button>
        </div>
        {qSuccess && <div className="msg-success">{qSuccess}</div>}
        {qError && (
          <div className="msg-error">
            {qError}
            {qHint && <span className="hint">💡 {qHint}</span>}
          </div>
        )}
      </div>

      <div className="card">
        <h2>Applications</h2>
        {jobs.length === 0 ? (
          <p className="msg-info">No applications yet — tailor a CV or write a cover letter above to track your first job.</p>
        ) : (
          <table className="jobs-table">
            <thead>
              <tr>
                <th>Company / Role</th>
                <th>Status</th>
                <th>Preparation</th>
                <th>Follow-up</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <JobRow key={job.id} job={job} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
