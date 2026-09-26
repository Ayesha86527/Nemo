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
  tailorJob,
  updateJob,
  stripMarkdown,
} from "../api/client";
import QuickJD from "../components/QuickJD";

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

            <section className="output-section output-actions" aria-labelledby={`job-actions-${job.id}`}>
              <div className="section-heading">
                <p className="section-kicker">Next actions</p>
                <h3 id={`job-actions-${job.id}`}>Prepare your application</h3>
              </div>
              <div className="job-actions">
              <button className="primary" onClick={onPrepare} disabled={Boolean(ui.busy) || !job.job_description}>
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
            </section>
            {!job.job_description && (
              <p className="msg-info">Add the job description below, then run Prepare.</p>
            )}
            {!job.prepared && job.job_description && (
              <p className="msg-info">
                Nemo requires role-match analysis + company research before tailoring or cover letters.
              </p>
            )}

            <section className="output-section" aria-labelledby={`job-context-${job.id}`}>
              <div className="section-heading">
                <p className="section-kicker">Context</p>
                <h3 id={`job-context-${job.id}`}>Job description</h3>
              </div>
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
            </section>

            {job.prepared && (
              <section className="output-section output-summary" aria-labelledby={`job-summary-${job.id}`}>
                <div className="section-heading">
                  <p className="section-kicker">Summary</p>
                  <h3 id={`job-summary-${job.id}`}>Candidate match</h3>
                </div>
                <div className="report report-compact">
                  {job.match_score != null ? `Match score: ${job.match_score}%\n` : ""}
                  {stripMarkdown(job.match_summary) || "(no summary)"}
                </div>
              </section>
            )}

            {job.prepared && (
              <section className="output-section output-evidence" aria-labelledby={`job-evidence-${job.id}`}>
                <div className="section-heading">
                  <p className="section-kicker">Evidence</p>
                  <h3 id={`job-evidence-${job.id}`}>Company and role research</h3>
                </div>
                <div className="report report-research">{stripMarkdown(job.research)}</div>
              </section>
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
                    <div className="report" style={{ maxHeight: 260 }}>{stripMarkdown(letter.content)}</div>
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

  const jobs = jobsQ.data ?? [];
  const active = jobs.filter((j) => j.status === "applied" || j.status === "interview").length;
  const interviews = jobs.filter((j) => j.status === "interview").length;
  const dueFollowUps = jobs.filter((j) => isFollowUpDue(j)).length;

  return (
    <>
      <h1>Job Tracker</h1>
      <p className="subtitle">
        Paste a posting below — Nemo parses it, researches the company, and scores your fit.
        Everything it generates is tracked here automatically.
      </p>

      <QuickJD />

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
        <h2>Applications</h2>
        {jobs.length === 0 ? (
          <p className="msg-info">No applications yet — analyze a posting above to track your first job.</p>
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
