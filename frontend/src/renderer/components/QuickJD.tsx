import React, { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  downloadBlob,
  generateCoverLetter,
  generateLinkedInDM,
  JobApplication,
  quickAnalyzeJob,
  stripMarkdown,
  tailorJob,
} from "../api/client";

const SAMPLE = "Paste a job description here — Nemo tracks, researches, and scores it automatically.";

export default function QuickJD() {
  const queryClient = useQueryClient();
  const [jd, setJd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");
  const [job, setJob] = useState<JobApplication | null>(null);
  const [dmBusy, setDmBusy] = useState(false);

  const analyze = async () => {
    if (!jd.trim() || busy) return;
    setBusy(true);
    setError("");
    setHint("");
    setJob(null);
    try {
      const result = await quickAnalyzeJob({ job_description: jd });
      setJob(result);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError(err.message);
        setHint(err.hint);
      } else {
        setError(String(err));
      }
    } finally {
      setBusy(false);
    }
  };

  const tailoredCV = async () => {
    if (!job) return;
    setError("");
    try {
      const outcome = await tailorJob(job.id);
      downloadBlob(outcome.blob, outcome.filename);
    } catch (err) {
      setError(err instanceof ApiRequestError ? `${err.message} ${err.hint}`.trim() : String(err));
    }
  };

  const coverLetter = async () => {
    if (!job) return;
    setError("");
    try {
      await generateCoverLetter(job.id);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setJob({ ...job });
      setError("");
      setHint("Cover letter generated — open the job in the tracker below to view it.");
    } catch (err) {
      setError(err instanceof ApiRequestError ? `${err.message} ${err.hint}`.trim() : String(err));
    }
  };

  const linkedinDM = async () => {
    if (!job) return;
    setDmBusy(true);
    setError("");
    try {
      const updated = await generateLinkedInDM(job.id);
      setJob(updated);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (err) {
      setError(err instanceof ApiRequestError ? `${err.message} ${err.hint}`.trim() : String(err));
    } finally {
      setDmBusy(false);
    }
  };

  return (
    <div className="card quick-jd">
      <h2>Analyze a job description</h2>
      <textarea
        value={jd}
        onChange={(e) => setJd(e.target.value)}
        placeholder={SAMPLE}
        rows={5}
      />
      <button className="primary" onClick={analyze} disabled={busy || !jd.trim()}>
        {busy ? "Analyzing — parsing, researching, scoring…" : "Analyze"}
      </button>
      <p className="msg-info">
        Company details and role are parsed automatically, market research runs against your CV,
        and a fit verdict is produced. No forms.
      </p>

      {job && (
        <div className="quick-jd-result">
          <div className="quick-jd-verdict">
            <div className="quick-jd-score">
              <strong>{job.match_score !== null ? `${job.match_score}%` : "—"}</strong>
              <span>match</span>
            </div>
            <div>
              <strong>{job.company} — {job.role}</strong>
              <p>{stripMarkdown(job.match_summary)}</p>
            </div>
            <span className={`badge ${job.aligned ? "badge-green" : "badge-warn"}`}>
              {job.aligned ? "CV aligned — original kept" : "CV misaligned — tailor it"}
            </span>
          </div>
          <div className="cv-file-actions">
            {!job.aligned && (
              <button className="primary" style={{ marginTop: 0 }} onClick={tailoredCV}>
                Download tailored CV
              </button>
            )}
            <button className="ghost" onClick={coverLetter}>Cover letter</button>
            <button className="ghost" onClick={linkedinDM} disabled={dmBusy}>
              {dmBusy ? "Drafting…" : "LinkedIn DM"}
            </button>
          </div>
          {job.linkedin_dm && (
            <div className="quick-jd-dm">
              <label>LinkedIn outreach</label>
              <p>{stripMarkdown(job.linkedin_dm)}</p>
              <button className="ghost" onClick={() => navigator.clipboard.writeText(job.linkedin_dm)}>
                Copy
              </button>
            </div>
          )}
          {job.research && (
            <details className="quick-jd-research">
              <summary>Company & role research</summary>
              <div className="report">{stripMarkdown(job.research)}</div>
            </details>
          )}
        </div>
      )}
      {error && (
        <div className="msg-error">
          {error}
          {hint && <span className="hint">💡 {hint}</span>}
        </div>
      )}
    </div>
  );
}
