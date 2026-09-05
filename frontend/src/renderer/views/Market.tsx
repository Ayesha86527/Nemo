import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  generateRoadmap,
  getMarketStatus,
  getProfile,
  listMarketReports,
  runMarketIntel,
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
          placeholder={effectiveRole || "e.g. Backend Engineer"}
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
          <h2>
            Latest report — {latest.target_role}{" "}
            <span className="badge" style={{ marginLeft: 8 }}>
              {new Date(latest.created_at).toLocaleDateString()}
            </span>
          </h2>

          <div className="market-hero">
            {report.match_score !== null && (
              <div className="donut-wrap">
                <Donut value={report.match_score} caption="role match" />
              </div>
            )}
            <p className="market-summary">{report.summary || "No summary available."}</p>
          </div>

          {hasVisuals ? (
            <>
              {report.skill_gaps.length > 0 && (
                <div className="viz-block">
                  <h3>Skill gap analysis</h3>
                  <GapBars gaps={report.skill_gaps} />
                  <GapLegend />
                </div>
              )}

              {report.market_signals.length > 0 && (
                <div className="viz-block">
                  <h3>In demand right now</h3>
                  <div className="chips">
                    {report.market_signals.map((signal, i) => (
                      <span className="chip" key={i}>{signal}</span>
                    ))}
                  </div>
                </div>
              )}

              {report.recommendations.length > 0 && (
                <div className="viz-block">
                  <h3>Next steps</h3>
                  <ol className="rec-list">
                    {report.recommendations.map((rec, i) => (
                      <li key={i}>{rec}</li>
                    ))}
                  </ol>
                </div>
              )}

              <div className="viz-block">
                <h3>Turn gaps into a plan</h3>
                <p className="msg-info" style={{ marginTop: 0 }}>
                  The Nemo Agent can convert this analysis into a personalized, checkable roadmap.
                </p>
                <button className="primary" style={{ marginTop: 4 }} onClick={buildRoadmap} disabled={roadmapBusy}>
                  {roadmapBusy ? "Planning…" : "Generate my roadmap"}
                </button>
              </div>
            </>
          ) : (
            <div className="report">{report.summary}</div>
          )}
        </div>
      )}
    </>
  );
}
