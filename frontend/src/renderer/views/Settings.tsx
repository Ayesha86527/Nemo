import React, { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  getProfile,
  getSettings,
  resetAllData,
  updateProfile,
  updateSettings,
} from "../api/client";

export default function SettingsView() {
  const queryClient = useQueryClient();
  const settingsQ = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const profileQ = useQuery({ queryKey: ["profile"], queryFn: getProfile });

  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [targetRoles, setTargetRoles] = useState("");
  const [education, setEducation] = useState("");
  const [shortTermGoal, setShortTermGoal] = useState("");
  const [longTermGoal, setLongTermGoal] = useState("");
  const [saved, setSaved] = useState(false);
  const [resetDone, setResetDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (settingsQ.data) {
      setBaseUrl(settingsQ.data.custom_base_url);
      setModel(settingsQ.data.cloud_model);
    }
  }, [settingsQ.data]);

  useEffect(() => {
    if (profileQ.data) {
      setName(profileQ.data.name);
      setEmail(profileQ.data.email);
      setTargetRoles(profileQ.data.target_roles);
      setEducation(profileQ.data.education);
      setShortTermGoal(profileQ.data.short_term_goal || "");
      setLongTermGoal(profileQ.data.long_term_goal || "");
    }
  }, [profileQ.data]);

  const keySet = settingsQ.data?.custom_api_key_set ?? false;

  const save = async () => {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await updateSettings({
        custom_base_url: baseUrl,
        cloud_model: model,
        ...(apiKey ? { custom_api_key: apiKey } : {}),
      });
      await updateProfile({
        name,
        email,
        target_roles: targetRoles,
        education,
        short_term_goal: shortTermGoal,
        long_term_goal: longTermGoal,
      });
      setApiKey("");
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ["settings"] });
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!window.confirm("Reset ALL data (profile, CV, jobs, roadmaps, chat history)? Your LLM settings are kept.")) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await resetAllData(false);
      queryClient.invalidateQueries();
      setSaved(false);
      setResetDone(true);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h1>Settings</h1>
      <p className="subtitle">All settings are stored locally in SQLite — nothing leaves your machine except LLM requests.</p>

      <div className="card">
        <h2>LLM endpoint</h2>
        <label>Base URL (any OpenAI-compatible endpoint)</label>
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.deepseek.com/v1"
        />
        <p className="msg-info">
          Works with DeepSeek, Groq, OpenRouter, Mistral, Together, OpenAI, or local servers like
          Ollama (http://localhost:11434/v1), vLLM and LM Studio.
        </p>

        <label>API key {keySet ? "(a key is stored — leave blank to keep it)" : ""}</label>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="Paste API key"
        />
        <p className="msg-info">Local servers that ignore auth accept any placeholder.</p>

        <label>Model</label>
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="Enter the exact model name used by your endpoint"
        />
        <p className="msg-info">Use the model identifier documented by your endpoint. Nemo saves and uses this value as entered.</p>
      </div>

      <div className="card">
        <h2>Profile</h2>
        <div className="row">
          <div>
            <label>Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <label>Email</label>
            <input value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
        </div>
        <label>Target roles (comma-separated)</label>
        <input value={targetRoles} onChange={(e) => setTargetRoles(e.target.value)} placeholder="Backend Engineer, SWE" />
        <div className="row">
          <div>
            <label>Education</label>
            <input value={education} onChange={(e) => setEducation(e.target.value)} placeholder="BSc Computer Science, …" />
          </div>
        </div>
        <label>Short-term goal</label>
        <input value={shortTermGoal} onChange={(e) => setShortTermGoal(e.target.value)} placeholder="Land a backend role in 3 months" />
        <label>Long-term goal</label>
        <input value={longTermGoal} onChange={(e) => setLongTermGoal(e.target.value)} placeholder="Grow into a staff engineer role" />
        <p className="msg-info">The Nemo Agent remembers these and keeps them updated from your conversations.</p>
      </div>

      <button className="primary" onClick={save} disabled={busy || settingsQ.isLoading || profileQ.isLoading}>
        {busy ? "Saving…" : "Save settings"}
      </button>
      {saved && <div className="msg-success">Saved.</div>}
      {resetDone && <div className="msg-success">All data reset — Nemo starts from a clean slate.</div>}
      {error && <div className="msg-error">{error}</div>}

      <div className="card danger-zone">
        <h2>Reset</h2>
        <p className="msg-info">
          Wipes your profile, CV, jobs, cover letters, roadmaps, and chat history — a full
          clean slate. Your LLM endpoint settings are kept.
        </p>
        <button className="ghost" onClick={reset} disabled={busy}>Reset all data…</button>
      </div>
    </>
  );
}
