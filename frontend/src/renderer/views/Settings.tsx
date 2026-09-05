import React, { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getProfile, getSettings, listModels, updateProfile, updateSettings } from "../api/client";

export default function SettingsView() {
  const queryClient = useQueryClient();
  const settingsQ = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const profileQ = useQuery({ queryKey: ["profile"], queryFn: getProfile });

  const [baseUrl, setBaseUrl] = useState("");
  const [debouncedUrl, setDebouncedUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [targetRoles, setTargetRoles] = useState("");
  const [education, setEducation] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (settingsQ.data) {
      setBaseUrl(settingsQ.data.custom_base_url);
      setDebouncedUrl(settingsQ.data.custom_base_url);
      setModel(settingsQ.data.cloud_model);
    }
  }, [settingsQ.data]);

  useEffect(() => {
    if (profileQ.data) {
      setName(profileQ.data.name);
      setEmail(profileQ.data.email);
      setTargetRoles(profileQ.data.target_roles);
      setEducation(profileQ.data.education);
    }
  }, [profileQ.data]);

  // Refetch the catalog shortly after the user stops editing the endpoint.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedUrl(baseUrl), 400);
    return () => clearTimeout(timer);
  }, [baseUrl]);

  const modelsQ = useQuery({
    queryKey: ["models", debouncedUrl, apiKey ? "draft-key" : "saved-key"],
    queryFn: () => listModels(debouncedUrl || undefined, apiKey || undefined),
    retry: false,
  });

  const keySet = settingsQ.data?.custom_api_key_set ?? false;
  const catalog = modelsQ.data;

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
      });
      setApiKey("");
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    } catch (err) {
      setError(String(err));
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
          list="model-catalog"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="Pick from the list or type a model name"
        />
        <datalist id="model-catalog">
          {catalog?.models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name || m.id}
            </option>
          ))}
        </datalist>
        <div className="row">
          <button type="button" onClick={() => modelsQ.refetch()} disabled={modelsQ.isLoading}>
            {modelsQ.isLoading ? "Loading models…" : "Refresh model list"}
          </button>
        </div>
        {modelsQ.isLoading && <p className="msg-info">Fetching live model list…</p>}
        {catalog?.error && (
          <p className="msg-error">
            Could not list models: {catalog.error}
            {catalog.hint ? ` — ${catalog.hint}` : ""}
          </p>
        )}
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
      </div>

      <button className="primary" onClick={save} disabled={busy || settingsQ.isLoading || profileQ.isLoading}>
        {busy ? "Saving…" : "Save settings"}
      </button>
      {saved && <div className="msg-success">Saved.</div>}
      {error && <div className="msg-error">{error}</div>}
    </>
  );
}
