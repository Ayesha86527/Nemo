import React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RoadmapData, latestRoadmap, updateRoadmapStep } from "../api/client";
import { Donut } from "../components/charts";

export default function RoadmapView() {
  const queryClient = useQueryClient();
  const roadmapQ = useQuery({ queryKey: ["roadmap-latest"], queryFn: latestRoadmap });

  const roadmap: RoadmapData | null = roadmapQ.data ?? null;

  const toggleStep = async (m: number, s: number, done: boolean) => {
    const updated = await updateRoadmapStep(m, s, done);
    queryClient.setQueryData(["roadmap-latest"], updated);
  };

  return (
    <>
      <h1>Your Roadmap</h1>
      <p className="subtitle">
        A personalized plan built from your CV and the latest market gap analysis. Nemo sizes the horizon to your gaps; ask the agent in plain
        language to re-plan it (e.g. “compress my roadmap to 4 weeks”).
      </p>

      {!roadmap && !roadmapQ.isLoading && (
        <div className="card">
          <p className="msg-info">
            No roadmap yet — ask the Nemo Agent to create a personalized roadmap for your target role.
          </p>
        </div>
      )}

      {roadmap && (
        <>
          <div className="card">
            <section className="output-section output-summary" aria-labelledby="roadmap-outcome-heading">
              <div className="section-heading">
                <p className="section-kicker">Outcome & context</p>
                <h2 id="roadmap-outcome-heading">Your plan at a glance</h2>
              </div>
              <div className="roadmap-hero">
              <Donut value={roadmap.progress.percent} caption={`${roadmap.progress.done}/${roadmap.progress.total} steps`} />
              <div className="roadmap-meta">
                <p className="output-title">{roadmap.goal}</p>
                <p className="output-meta">
                  {roadmap.target_role}
                  {roadmap.focus ? ` · ${roadmap.focus} focus` : ""} · {roadmap.horizon_weeks}-week
                  horizon · generated {new Date(roadmap.created_at).toLocaleDateString()} via{" "}
                  {roadmap.provider}
                </p>
                {roadmap.preferences && (
                  <p className="output-note">
                    <strong>Preferences:</strong> {roadmap.preferences}
                  </p>
                )}
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${roadmap.progress.percent}%` }} />
                </div>
              </div>
            </div>
            </section>
          </div>

          <div className="roadmap-timeline">
            {roadmap.milestones.map((m, mi) => {
              const doneCount = m.steps.filter((s) => s.done).length;
              const complete = doneCount === m.steps.length;
              return (
                <div key={mi} className={`card milestone ${complete ? "milestone-done" : ""}`}>
                  <section className="output-section" aria-labelledby={`milestone-${mi}`}>
                  <div className="milestone-head">
                    <div className="milestone-index">{mi + 1}</div>
                    <div>
                      <h2 id={`milestone-${mi}`} style={{ margin: 0 }}>
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
                  </section>
                </div>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}
