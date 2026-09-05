import React, { useState } from "react";
import NemoIcon from "./NemoIcon";

const FEATURES = [
  "Hands-free CV Studio",
  "Job tracking & tailoring",
  "Market intelligence",
  "Personal roadmap",
];

export default function Welcome({ onEnter }: { onEnter: () => void }) {
  const [exiting, setExiting] = useState(false);

  const enter = () => {
    setExiting(true);
    window.setTimeout(onEnter, 380);
  };

  return (
    <div className={`welcome ${exiting ? "welcome-exit" : ""}`}>
      <div className="welcome-glow">
        <NemoIcon size={96} />
      </div>
      <h1 className="welcome-title">Nemo</h1>
      <p className="welcome-tagline">
        Your AI career co-pilot. Nemo keeps your CV sharp, tracks every application,
        and charts the course to your next role — all private, all on your machine.
      </p>
      <div className="welcome-features">
        {FEATURES.map((feature) => (
          <span className="welcome-feature" key={feature}>{feature}</span>
        ))}
      </div>
      <button className="primary welcome-enter" onClick={enter} disabled={exiting}>
        Get started
      </button>
      <div className="welcome-foot">Local-first · Private · Powered by your own LLM</div>
    </div>
  );
}
