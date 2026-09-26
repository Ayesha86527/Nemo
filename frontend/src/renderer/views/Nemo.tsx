import React, { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  AgentHistoryItem,
  agentStream,
  agentHistory,
  clearAgentHistory,
  downloadBlob,
  getAgentGreeting,
  renderCV,
  replaceCVFile,
  transcribeAudio,
} from "../api/client";
import NemoIcon from "../components/NemoIcon";

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
        this one to keep everything in sync. Word and PDF versions are in CV Studio.
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

interface Props {
  context: string;
}

export default function NemoView({ context }: Props) {
  const queryClient = useQueryClient();
  const greetingQ = useQuery({ queryKey: ["agent-greeting"], queryFn: getAgentGreeting });
  const historyQ = useQuery({ queryKey: ["agent-history"], queryFn: () => agentHistory(100) });

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
    if (historyQ.data && !loadedRef.current) {
      loadedRef.current = true;
      setMessages(
        greetingQ.data
          ? [{ role: "agent", content: greetingQ.data.greeting }, ...historyQ.data.map((m: AgentHistoryItem) => ({ role: m.role, content: m.content }))]
          : historyQ.data.map((m: AgentHistoryItem) => ({ role: m.role, content: m.content }))
      );
    }
  }, [historyQ.data, greetingQ.data]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => () => {
    const recorder = recorderRef.current;
    if (recorder) {
      recorder.onstop = null;
      if (recorder.state !== "inactive") recorder.stop();
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  const speak = (text: string) => {
    if (!("speechSynthesis" in window)) return;
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
    queryClient.invalidateQueries({ queryKey: ["agent-greeting"] });
  };

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    setError("");
    setHint("");
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: message },
      { role: "agent", content: "", pending: true },
    ]);

    try {
      const turn = await agentStream(message, context, (chunk) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.pending) {
            next[next.length - 1] = { ...last, content: last.content + chunk };
          }
          return next;
        });
      });
      setMessages((prev) => {
        const next = prev.slice(0, -1);
        return [...next, { role: "agent", content: turn.reply, cvUpdated: turn.cv_updated }];
      });
      if (turn.changes.length > 0) invalidateTouched();
      if (speakReplies) speak(turn.reply);
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
    setMessages(greetingQ.data ? [{ role: "agent", content: greetingQ.data.greeting }] : []);
    queryClient.invalidateQueries({ queryKey: ["agent-history"] });
  };

  return (
    <div className="nemo-view">
      <div className="nemo-view-head">
        <span><NemoIcon size={22} /> Nemo</span>
        <button className="ghost" onClick={clearHistory} title="Clear chat history">
          Clear history
        </button>
      </div>
      <div className="chat-scroll">
        {messages.length === 0 && greetingQ.data && (
          <div className="chat-msg chat-agent">
            <div className="chat-bubble">{greetingQ.data.greeting}</div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-${m.role} ${m.pending ? "chat-pending" : ""}`}>
            <div className="chat-bubble">{m.content}</div>
            {m.role === "agent" && !m.pending && m.content && "speechSynthesis" in window && (
              <button
                className="ghost chat-speak-btn"
                title="Read this reply aloud (uses your system's built-in voices)"
                onClick={() => speak(m.content)}
              >
                🔊
              </button>
            )}
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
          title="Voice input (on-device)"
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
