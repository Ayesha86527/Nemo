import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  generateRoadmap,
  getMarketStatus,
  getProfile,
  listMarketReports,
  runMarketIntel,
  stripMarkdown,
} from "../api/client";
import { Donut, GapBars, GapLegend } from "../components/charts";
import type { View } from "../App";

interface Props {
  onNavigate: (view: View) => void;
}

export default function Market({ onNavigate }: Props) {
  const queryClient = useQueryClient();
  const [role, setRole] = useState("");
  const [busy, setBusy] = useState(false);
  const [roadmapBusy, setRoadmapBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");

  const profile = useQuery({ queryKey: ["profile"], queryFn: getProfile });
  const status = useQuery({ queryKey: ["market-status"], queryFn: getMarketStatus });
  const reports = useQuery({ queryKey: ["market-reports"], queryFn: () => listMarketReports(1) });

  const effectiveRole = role || profile.data?.target_roles?.split(",")[0]?.trim() || "";
  const latest = reports.data?.[0];
  const report = latest?.report;
  const hasVisuals = report && (report.match_score !== null || report.skill_gaps.length > 0);

  const run = async () => {
    setBusy(true);
    setError("");
    setHint("");
    try {
      await runMarketIntel(effectiveRole);
      queryClient.invalidateQueries({ queryKey: ["market-reports"] });
      queryClient.invalidateQueries({ queryKey: ["market-status"] });
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

  const buildRoadmap = async () => {
    setRoadmapBusy(true);
    setError("");
    setHint("");
    try {
      await generateRoadmap(latest?.target_role || effectiveRole);
      queryClient.invalidateQueries({ queryKey: ["roadmap-latest"] });
      onNavigate("roadmap");
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError(err.message);
        setHint(err.hint);
      } else {
        setError(String(err));
      }
    } finally {
      setRoadmapBusy(false);
    }
  };

  return (
    <>
      <h1>Market Intelligence</h1>
      <p className="subtitle">
        Compares your actual resume (uploaded file + CV Studio content) against market demand for
        your target role. Recommended cadence: weekly.
      </p>

      <div className="card">
        <h2>Run gap analysis</h2>
        <label>Target role {profile.data?.target_roles ? `(default: ${profile.data.target_roles.split(",")[0].trim()})` : ""}</label>
        <input
          value={role}
          onChange={(e) => setRole(e.target.value)}
          placeholder={effectiveRole || "Target role or career goal"}
        />
        <button className="primary" onClick={run} disabled={busy || !effectiveRole}>
          {busy ? "Analysing…" : "Run analysis"}
        </button>
        <p className="msg-info">
          {status.data?.last_run_at
            ? `Last run: ${new Date(status.data.last_run_at).toLocaleString()} — ${status.data.due ? "a fresh run is due." : "up to date."}`
            : "No runs yet."}
        </p>
        {busy && <p className="msg-info">Gathering market context and comparing against your profile…</p>}
        {error && (
          <div className="msg-error">
            {error}
            {hint && <span className="hint">💡 {hint}</span>}
          </div>
        )}
      </div>

      {latest && report && (
        <div className="card">
          <section className="output-section output-summary" aria-labelledby="market-overview-heading">
            <div className="section-heading">
              <p className="section-kicker">Context & summary</p>
              <h2 id="market-overview-heading">Latest report — {latest.target_role}</h2>
              <p className="output-meta">Generated {new Date(latest.created_at).toLocaleDateString()} via {latest.provider}</p>
            </div>
            <div className="market-hero">
              {report.match_score !== null && (
                <div className="donut-wrap">
                  <Donut value={report.match_score} caption="role match" />
                </div>
              )}
              <p className="market-summary">{stripMarkdown(report.summary) || "No summary available."}</p>
            </div>
          </section>

          {hasVisuals ? (
            <>
              {(report.skill_gaps.length > 0 || report.market_signals.length > 0) && (
                <section className="output-section output-evidence" aria-labelledby="market-evidence-heading">
                  <div className="section-heading">
                    <p className="section-kicker">Evidence</p>
                    <h3 id="market-evidence-heading">Market signals and demonstrated gaps</h3>
                  </div>
                  {report.skill_gaps.length > 0 && (
                    <div className="viz-block">
                      <h4>Gap analysis</h4>
                      <GapBars gaps={report.skill_gaps} />
                      <GapLegend />
                    </div>
                  )}
                  {report.market_signals.length > 0 && (
                    <div className="viz-block">
                      <h4>Market signals</h4>
                      <div className="chips">
                        {report.market_signals.map((signal, i) => (
                          <span className="chip" key={i}>{signal}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </section>
              )}

              <section className="output-section output-actions" aria-labelledby="market-actions-heading">
                <div className="section-heading">
                  <p className="section-kicker">Next actions</p>
                  <h3 id="market-actions-heading">Turn evidence into progress</h3>
                </div>
              {report.recommendations.length > 0 && (
                <div className="viz-block">
                  <ol className="rec-list">
                    {report.recommendations.map((rec, i) => (
                      <li key={i}>{rec}</li>
                    ))}
                  </ol>
                </div>
              )}

              {report.sources && report.sources.length > 0 && (
                <div className="viz-block">
                  <h4>Sources & transparency</h4>
                  <ul className="source-list">
                    {report.sources.map((src, i) => (
                      <li key={i}>
                        {src.claim && <span className="source-claim">{src.claim}</span>}
                        <span className={`source-name ${src.source.startsWith("reasoned") ? "source-inferred" : ""}`}>
                          {src.source.startsWith("reasoned") ? "🧠 " : "🔗 "}
                          {src.source}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="output-note">
                    Market claims are labeled with their origin; claims derived only from your own
                    CV are marked as inferred rather than sourced.
                  </p>
                </div>
              )}

              <div className="viz-block">
                <p className="output-note">
                  The Nemo Agent can convert this analysis into a personalized, checkable roadmap.
                </p>
                <button className="primary" onClick={buildRoadmap} disabled={roadmapBusy}>
                  {roadmapBusy ? "Planning…" : "Generate my roadmap"}
                </button>
              </div>
              </section>
            </>
          ) : (
            <div className="report">{report.summary}</div>
          )}
        </div>
      )}
    </>
  );
}
