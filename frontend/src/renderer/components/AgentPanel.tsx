import React, { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  AgentHistoryItem,
  agentChat,
  agentHistory,
  clearAgentHistory,
  downloadBlob,
  renderCV,
  replaceCVFile,
  transcribeAudio,
} from "../api/client";
import NemoIcon from "./NemoIcon";

interface ChatMessage {
  role: "user" | "agent";
  content: string;
  pending?: boolean;
  cvUpdated?: boolean;
}

function CvUpdateCard({ onReplaced }: { onReplaced: () => void }) {
  const [busy, setBusy] = useState(false);
  const [replaced, setReplaced] = useState(false);
  const [error, setError] = useState("");

  const download = async () => {
    setError("");
    try {
      const { blob, filename } = await renderCV();
      downloadBlob(blob, filename);
    } catch (err) {
      setError(err instanceof ApiRequestError ? `${err.message} ${err.hint}`.trim() : String(err));
    }
  };

  const replace = async () => {
    setBusy(true);
    setError("");
    try {
      await replaceCVFile();
      setReplaced(true);
      onReplaced();
    } catch (err) {
      setError(err instanceof ApiRequestError ? `${err.message} ${err.hint}`.trim() : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="chat-cv-card">
      <div className="chat-cv-card-title">📄 Your updated CV is ready.</div>
      <p className="chat-cv-card-note">
        Download the revised file, then remove the old version from the app and replace it with
        this one to keep everything in sync.
      </p>
      <div className="cv-file-actions">
        <button className="ghost" onClick={download}>Download .docx</button>
        {replaced ? (
          <span className="msg-success" style={{ margin: 0 }}>App CV replaced ✓</span>
        ) : (
          <button className="primary" style={{ marginTop: 0 }} onClick={replace} disabled={busy}>
            {busy ? "Replacing…" : "Replace current version in app"}
          </button>
        )}
      </div>
      {error && <div className="msg-error">{error}</div>}
    </div>
  );
}

const SUGGESTIONS = [
  "Add FastAPI and Docker to my skills",
  "Point my LLM endpoint at Groq",
  "Create a 6-week roadmap for Backend Engineer",
  "What should I do on this screen?",
];

interface Props {
  context: string;
}

export default function AgentPanel({ context }: Props) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const historyQ = useQuery({ queryKey: ["agent-history"], queryFn: () => agentHistory(100), enabled: open });

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [speakReplies, setSpeakReplies] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const loadedRef = useRef(false);

  useEffect(() => {
    if (open && historyQ.data && !loadedRef.current) {
      loadedRef.current = true;
      setMessages(historyQ.data.map((m: AgentHistoryItem) => ({ role: m.role, content: m.content })));
    }
  }, [open, historyQ.data]);

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, open]);

  useEffect(() => () => {
    const recorder = recorderRef.current;
    if (recorder) {
      recorder.onstop = null;
      if (recorder.state !== "inactive") recorder.stop();
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  const speak = (text: string) => {
    if (!speakReplies || !("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text.replace(/[*_#`-]/g, " "));
    utterance.rate = 1.05;
    window.speechSynthesis.speak(utterance);
  };

  const invalidateTouched = () => {
    queryClient.invalidateQueries({ queryKey: ["cv-content"] });
    queryClient.invalidateQueries({ queryKey: ["cv-status"] });
    queryClient.invalidateQueries({ queryKey: ["settings"] });
    queryClient.invalidateQueries({ queryKey: ["profile"] });
    queryClient.invalidateQueries({ queryKey: ["roadmap-latest"] });
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
  };

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    setError("");
    setHint("");
    setBusy(true);
    setMessages((prev) => [...prev, { role: "user", content: message }, { role: "agent", content: "…", pending: true }]);
    try {
      const turn = await agentChat(message, context);
      setMessages((prev) => {
        const next = prev.slice(0, -1);
        return [...next, { role: "agent", content: turn.reply, cvUpdated: turn.cv_updated }];
      });
      if (turn.changes.length > 0) invalidateTouched();
      speak(turn.reply);
    } catch (err) {
      setMessages((prev) => prev.slice(0, -1));
      setError(err instanceof ApiRequestError ? err.message : String(err));
      if (err instanceof ApiRequestError) setHint(err.hint);
    } finally {
      setBusy(false);
    }
  };

  const toggleMic = async () => {
    if (transcribing) return;
    if (listening) {
      recorderRef.current?.stop();
      return;
    }
    setError("");
    setHint("");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone unavailable.");
      setHint("Allow mic access for voice input — typing always works.");
      return;
    }
    const recorder = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      recorderRef.current = null;
      setListening(false);
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      if (blob.size === 0) return;
      setTranscribing(true);
      try {
        const text = await transcribeAudio(blob);
        if (text) {
          send(text);
        } else {
          setError("No speech detected.");
          setHint("Try again, a little closer to the mic.");
        }
      } catch (err) {
        setError(err instanceof ApiRequestError ? err.message : String(err));
        if (err instanceof ApiRequestError) setHint(err.hint);
      } finally {
        setTranscribing(false);
      }
    };
    streamRef.current = stream;
    recorderRef.current = recorder;
    setListening(true);
    recorder.start();
  };

  const clearHistory = async () => {
    await clearAgentHistory();
    setMessages([]);
    queryClient.invalidateQueries({ queryKey: ["agent-history"] });
  };

  if (!open) {
    return (
      <button className="agent-fab" onClick={() => setOpen(true)} title="Ask Nemo for help on this screen">
        <NemoIcon size={32} />
      </button>
    );
  }

  return (
    <div className="agent-panel">
      <div className="agent-panel-head">
        <span><NemoIcon size={22} /> Nemo Agent</span>
        <div className="agent-panel-head-actions">
          <button className="ghost" onClick={clearHistory} title="Clear chat history">
            Clear
          </button>
          <button className="ghost" onClick={() => setOpen(false)} title="Close">
            ✕
          </button>
        </div>
      </div>
      <div className="chat-scroll">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>Nemo sees this screen and can act on it. Ask anything — or tap a suggestion.</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip chip-btn" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-${m.role} ${m.pending ? "chat-pending" : ""}`}>
            <div className="chat-bubble">{m.content}</div>
            {m.cvUpdated && !m.pending && (
              <CvUpdateCard
                onReplaced={() => {
                  queryClient.invalidateQueries({ queryKey: ["cv-status"] });
                  queryClient.invalidateQueries({ queryKey: ["cv-content"] });
                }}
              />
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {error && (
        <div className="msg-error agent-panel-error">
          {error}
          {hint && <span className="hint">💡 {hint}</span>}
        </div>
      )}

      <div className="chat-input-row">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
              setInput("");
            }
          }}
          placeholder={
            listening
              ? "Listening… speak now, tap ■ to finish"
              : transcribing
                ? "Transcribing…"
                : "Message Nemo…"
          }
          disabled={busy}
        />
        <button
          className={`ghost mic-btn ${listening ? "mic-active" : ""}`}
          onClick={toggleMic}
          disabled={busy || transcribing}
          title="Voice input"
        >
          {listening ? "■" : transcribing ? "…" : "🎤"}
        </button>
        <button
          className="primary"
          style={{ marginTop: 0 }}
          onClick={() => { send(input); setInput(""); }}
          disabled={busy || !input.trim()}
        >
          {busy ? "…" : "Send"}
        </button>
      </div>
      <div className="chat-footer">
        <label className="chat-toggle">
          <input
            type="checkbox"
            checked={speakReplies}
            onChange={(e) => {
              setSpeakReplies(e.target.checked);
              if (!e.target.checked) window.speechSynthesis?.cancel();
            }}
            style={{ width: "auto" }}
          />
          Speak replies
        </label>
      </div>
    </div>
  );
}
