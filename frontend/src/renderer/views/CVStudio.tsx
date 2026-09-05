import React, { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  deleteCVFile,
  downloadBlob,
  downloadCVFile,
  getCVContent,
  getCVStatus,
  renderCV,
  uploadCVFile,
} from "../api/client";

export default function CVStudio() {
  const queryClient = useQueryClient();
  const statusQ = useQuery({ queryKey: ["cv-status"], queryFn: getCVStatus });
  const contentQ = useQuery({ queryKey: ["cv-content"], queryFn: getCVContent });

  const [contentError, setContentError] = useState("");
  const [parsing, setParsing] = useState(false);
  const [ingestNote, setIngestNote] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const invalidateCV = () => {
    queryClient.invalidateQueries({ queryKey: ["cv-status"] });
    queryClient.invalidateQueries({ queryKey: ["cv-content"] });
  };

  const upload = async (file: File) => {
    setContentError("");
    setIngestNote("");
    setParsing(true);
    try {
      const result = await uploadCVFile(file);
      if (result.ingested) {
        setIngestNote("Resume parsed — your details are shown below.");
      } else if (result.ingest_error) {
        setIngestNote(`Saved, but automatic parsing failed (${result.ingest_error}). Ask Nemo to add the content instead.`);
      }
      invalidateCV();
    } catch (err) {
      setContentError(err instanceof ApiRequestError ? `${err.message} ${err.hint}` : String(err));
    } finally {
      setParsing(false);
    }
  };

  const removeFile = async () => {
    await deleteCVFile();
    setIngestNote("");
    invalidateCV();
  };

  const downloadOriginal = async () => {
    const { blob, filename } = await downloadCVFile();
    downloadBlob(blob, filename);
  };

  const onRender = async () => {
    setContentError("");
    try {
      const { blob, filename } = await renderCV();
      downloadBlob(blob, filename);
    } catch (err) {
      setContentError(err instanceof ApiRequestError ? `${err.message} ${err.hint}`.trim() : String(err));
    }
  };

  const content = contentQ.data;
  const hasContent =
    !!content &&
    (content.skills.length > 0 ||
      content.experience.length > 0 ||
      content.projects.length > 0 ||
      content.achievements.length > 0);

  return (
    <>
      <h1>CV Studio</h1>
      <p className="subtitle">
        Your single source of truth, managed hands-free. Tell the Nemo Agent what to add or
        change — it updates your CV and offers a download plus one-click replacement of the app version.
      </p>

      <div className="card">
        <h2>1 · Your CV file</h2>
        <input
          ref={fileRef}
          type="file"
          accept=".docx"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload(f);
            e.target.value = "";
          }}
        />
        {statusQ.data?.has_file ? (
          <div className="cv-file-row">
            <div className="cv-file-info">
              <span className="cv-file-name">✓ {statusQ.data.filename}</span>
              {statusQ.data.uploaded_at && (
                <span className="cv-file-date">
                  stored {new Date(statusQ.data.uploaded_at).toLocaleString()}
                </span>
              )}
            </div>
            <div className="cv-file-actions">
              <button className="ghost" onClick={downloadOriginal}>Download</button>
              <button className="ghost" onClick={() => fileRef.current?.click()}>Replace</button>
              <button className="ghost" onClick={removeFile}>Remove</button>
            </div>
          </div>
        ) : (
          <div className="file-drop" onClick={() => fileRef.current?.click()}>
            Click to choose a .docx file — it is stored in ~/.nemo, no re-upload needed
          </div>
        )}
        {parsing ? (
          <p className="msg-info">Parsing resume… this takes a few seconds.</p>
        ) : ingestNote ? (
          <p className="msg-info">{ingestNote}</p>
        ) : (
          <p className="msg-info">
            Content edits happen through the Nemo Agent; per-job tailoring runs from the Job Tracker.
          </p>
        )}
      </div>

      <div className="card">
        <h2>2 · CV content (read-only)</h2>
        <p className="msg-info">
          Content is managed by the Nemo Agent. Open the Nemo panel and say things like
          “Add FastAPI to my skills” or “Add my internship at Acme, Jan–Jun 2025”.
        </p>

        {parsing && <p>Parsing resume… your details will appear here automatically.</p>}
        {!parsing && !hasContent && (
          <p>No CV content yet — upload a .docx resume above or ask Nemo to add your skills and experience.</p>
        )}

        {hasContent && content && (
          <>
            {content.skills.length > 0 && (
              <>
                <label>Skills</label>
                <div className="chips">
                  {content.skills.map((skill, i) => (
                    <span className="chip" key={`${skill}-${i}`}>{skill}</span>
                  ))}
                </div>
              </>
            )}

            {content.experience.length > 0 && (
              <>
                <label>Experience</label>
                {content.experience.map((item, i) => (
                  <div className="exp-item" key={i}>
                    <strong>{item.role}{item.company ? ` — ${item.company}` : ""}</strong>
                    {(item.start || item.end) && (
                      <span className="cv-file-date"> {item.start}{item.start || item.end ? " – " : ""}{item.end}</span>
                    )}
                    {item.description && <p>{item.description}</p>}
                  </div>
                ))}
              </>
            )}

            {content.projects.length > 0 && (
              <>
                <label>Projects</label>
                {content.projects.map((item, i) => (
                  <div className="exp-item" key={i}>
                    <strong>{item.name}{item.tech ? ` (${item.tech})` : ""}</strong>
                    {item.description && <p>{item.description}</p>}
                  </div>
                ))}
              </>
            )}

            {content.achievements.length > 0 && (
              <>
                <label>Achievements</label>
                <ul className="plain-list">
                  {content.achievements.map((achievement, i) => (
                    <li key={i}>{achievement}</li>
                  ))}
                </ul>
              </>
            )}

            <button className="ghost" onClick={onRender}>Download rendered .docx</button>
          </>
        )}
        {contentError && <div className="msg-error">{contentError}</div>}
      </div>
    </>
  );
}
