import React from "react";
import NemoIcon from "./NemoIcon";

export default function BootScreen({ error, reconnecting }: { error: string; reconnecting: boolean }) {
  return (
    <div className="welcome boot">
      <div className="welcome-glow boot-glow">
        <NemoIcon size={96} />
      </div>
      <h1 className="welcome-title">Nemo</h1>
      {error ? (
        <div className="boot-card">
          <p className="boot-card-title">Nemo couldn't start its local engine</p>
          <p className="boot-card-msg">{error}</p>
          <p className="boot-card-hint">
            Close and reopen Nemo, and check that Python is installed — Nemo keeps retrying in the
            background.
          </p>
        </div>
      ) : (
        <>
          <p className="welcome-tagline boot-status">
            {reconnecting
              ? "Reconnecting to your local engine…"
              : "Setting up your private workspace…"}
          </p>
          <div className="boot-dots" role="status" aria-label="Loading">
            <span />
            <span />
            <span />
          </div>
        </>
      )}
      <div className="welcome-foot">Local-first · Private · Powered by your own LLM</div>
    </div>
  );
}
