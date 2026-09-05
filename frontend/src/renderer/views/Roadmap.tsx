import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  RoadmapData,
  generateRoadmap,
  getProfile,
  latestRoadmap,
  updateRoadmapStep,
} from "../api/client";
import { Donut } from "../components/charts";

export default function RoadmapView() {
  const queryClient = useQueryClient();
  const roadmapQ = useQuery({ queryKey: ["roadmap-latest"], queryFn: latestRoadmap });
  const profileQ = useQuery({ queryKey: ["profile"], queryFn: getProfile });

  const [role, setRole] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");

  const roadmap: RoadmapData | null = roadmapQ.data ?? null;
  const effectiveRole = role || profileQ.data?.target_roles?.split(",")[0]?.trim() || "";

  const generate = async () => {
    setBusy(true);
    setError("");
    setHint("");
    try {
      await generateRoadmap(effectiveRole);
      queryClient.invalidateQueries({ queryKey: ["roadmap-latest"] });
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

  const toggleStep = async (m: number, s: number, done: boolean) => {
    const updated = await updateRoadmapStep(m, s, done);
    queryClient.setQueryData(["roadmap-latest"], updated);
  };

  return (
    <>
      <h1>Your Roadmap</h1>
      <p className="subtitle">
        A personalized plan built from your CV and the latest market gap analysis — with new project
        ideas for your target domain. Nemo sizes the horizon to your gaps; ask the agent in plain
        language to re-plan it (e.g. “compress my roadmap to 4 weeks”).
      </p>

      <div className="card">
        <h2>{roadmap ? "Regenerate" : "Generate your roadmap"}</h2>
        <div className="row">
          <input
            value={role}
            onChange={(e) => setRole(e.target.value)}
            placeholder={effectiveRole ? `Target role (default: ${effectiveRole})` : "Target role (e.g. Backend Engineer)"}
          />
          <button className="primary" style={{ marginTop: 0, flex: 0 }} onClick={generate} disabled={busy || !effectiveRole}>
            {busy ? "Planning…" : roadmap ? "Regenerate roadmap" : "Generate roadmap"}
          </button>
        </div>
        {busy && <p className="msg-info">Analysing your gaps and building milestones…</p>}
        {error && (
          <div className="msg-error">
            {error}
            {hint && <span className="hint">💡 {hint}</span>}
          </div>
        )}
      </div>

      {!roadmap && !roadmapQ.isLoading && (
        <div className="card">
          <p className="msg-info">
            No roadmap yet. Generate one here, or ask the Nemo Agent: “Create a roadmap for Backend Engineer”.
          </p>
        </div>
      )}

      {roadmap && (
        <>
          <div className="card">
            <div className="roadmap-hero">
              <Donut value={roadmap.progress.percent} caption={`${roadmap.progress.done}/${roadmap.progress.total} steps`} />
              <div className="roadmap-meta">
                <h2 style={{ margin: 0 }}>{roadmap.goal}</h2>
                <p className="msg-info" style={{ marginTop: 6 }}>
                  {roadmap.target_role}
                  {roadmap.focus ? ` · ${roadmap.focus} focus` : ""} · {roadmap.horizon_weeks}-week
                  horizon · generated {new Date(roadmap.created_at).toLocaleDateString()} via{" "}
                  {roadmap.provider}
                </p>
                {roadmap.preferences && (
                  <p className="msg-info" style={{ marginTop: 8 }}>
                    <strong>Tailored to your preferences:</strong> {roadmap.preferences}
                  </p>
                )}
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${roadmap.progress.percent}%` }} />
                </div>
              </div>
            </div>
          </div>

          <div className="roadmap-timeline">
            {roadmap.milestones.map((m, mi) => {
              const doneCount = m.steps.filter((s) => s.done).length;
              const complete = doneCount === m.steps.length;
              return (
                <div key={mi} className={`card milestone ${complete ? "milestone-done" : ""}`}>
                  <div className="milestone-head">
                    <div className="milestone-index">{mi + 1}</div>
                    <div>
                      <h2 style={{ margin: 0 }}>
                        {m.title} {complete && <span className="badge badge-green">done</span>}
                      </h2>
                      {m.focus && <div className="milestone-focus">{m.focus}</div>}
                    </div>
                    <span className="badge">{doneCount}/{m.steps.length}</span>
                  </div>
                  <ul className="step-list">
                    {m.steps.map((step, si) => (
                      <li key={si}>
                        <label className={`step-row ${step.done ? "step-done" : ""}`}>
                          <input
                            type="checkbox"
                            checked={step.done}
                            onChange={(e) => toggleStep(mi, si, e.target.checked)}
                            style={{ width: "auto" }}
                          />
                          <span>{step.task}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}
