import React, { useEffect, useState } from "react";
import { healthCheck } from "./api/client";
import AgentPanel from "./components/AgentPanel";
import BootScreen from "./components/BootScreen";
import NemoIcon from "./components/NemoIcon";
import Welcome from "./components/Welcome";
import Dashboard from "./views/Dashboard";
import CVStudio from "./views/CVStudio";
import Market from "./views/Market";
import RoadmapView from "./views/Roadmap";
import SettingsView from "./views/Settings";

export type View = "dashboard" | "cv" | "market" | "roadmap" | "settings";

const NAV: { id: View; label: string }[] = [
  { id: "dashboard", label: "Job Tracker" },
  { id: "cv", label: "CV Studio" },
  { id: "market", label: "Market Intel" },
  { id: "roadmap", label: "Roadmap" },
  { id: "settings", label: "Settings" },
];

const VIEW_CONTEXT: Record<View, string> = {
  dashboard:
    "Job Tracker — the applications table with statuses, preparation results, and follow-up reminders. Tailoring and cover letters auto-track jobs here.",
  cv: "CV Studio — the uploaded CV file plus structured content (skills, experience, projects, achievements).",
  market: "Market Intelligence — gap analysis comparing the user's CV against demand for the target role.",
  roadmap: "Roadmap — the personalized learning plan with milestones and checkable steps.",
  settings: "Settings — LLM provider, cloud provider, API keys, and models.",
};

export default function App() {
  const [view, setView] = useState<View>("dashboard");
  const [backendOk, setBackendOk] = useState(false);
  const [welcomed, setWelcomed] = useState(false);
  const [backendUrl, setBackendUrl] = useState(window.nemoAPI.getBackendUrl());
  const [backendError, setBackendError] = useState(window.nemoAPI.getBackendError?.() || "");

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      const ok = await healthCheck();
      if (!cancelled) {
        setBackendOk(ok);
        // The port/error can land over IPC after first paint.
        setBackendUrl(window.nemoAPI.getBackendUrl());
        setBackendError(window.nemoAPI.getBackendError?.() || "");
      }
    };
    check();
    const interval = setInterval(check, backendOk ? 15000 : 1500);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [backendOk]);

  if (!backendOk) {
    return <BootScreen error={backendError} reconnecting={!!backendUrl} />;
  }

  if (!welcomed) {
    return <Welcome onEnter={() => setWelcomed(true)} />;
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <NemoIcon size={32} />
          <div>
            Nemo
            <small>AI Career Co-Pilot</small>
          </div>
        </div>
        {NAV.map((item) => (
          <button
            key={item.id}
            className={`nav-item ${view === item.id ? "active" : ""}`}
            onClick={() => setView(item.id)}
          >
            {item.label}
          </button>
        ))}
        <div className="health">
          <span className="dot green" />
          Backend connected
        </div>
      </aside>
      <main className="main">
        {view === "dashboard" && <Dashboard />}
        {view === "cv" && <CVStudio />}
        {view === "market" && <Market onNavigate={setView} />}
        {view === "roadmap" && <RoadmapView />}
        {view === "settings" && <SettingsView />}
      </main>
      <AgentPanel context={VIEW_CONTEXT[view]} />
    </div>
  );
}
