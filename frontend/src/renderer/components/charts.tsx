import React from "react";
import { SkillGap } from "../api/client";

const STATUS_COLORS: Record<SkillGap["status"], string> = {
  have: "var(--green)",
  partial: "var(--yellow)",
  missing: "var(--red)",
};

const STATUS_WIDTH: Record<SkillGap["status"], number> = {
  have: 100,
  partial: 60,
  missing: 30,
};

export function Donut({ value, size = 150, caption }: { value: number; size?: number; caption?: string }) {
  const stroke = 14;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const filled = (Math.max(0, Math.min(100, value)) / 100) * circumference;

  return (
    <svg width={size} height={size} role="img" aria-label={`${value} percent match`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--border)" strokeWidth={stroke} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={value >= 70 ? "var(--green)" : value >= 40 ? "var(--yellow)" : "var(--red)"}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={`${filled} ${circumference - filled}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y="47%" textAnchor="middle" dominantBaseline="middle" fontSize={size / 4} fontWeight={700} fill="var(--text)">
        {value}%
      </text>
      {caption && (
        <text x="50%" y="66%" textAnchor="middle" fontSize={11} fill="var(--text-dim)">
          {caption}
        </text>
      )}
    </svg>
  );
}

export function GapBars({ gaps }: { gaps: SkillGap[] }) {
  return (
    <div className="gap-bars">
      {gaps.map((gap, i) => (
        <div className="gap-row" key={`${gap.skill}-${i}`}>
          <div className="gap-label">
            <span>{gap.skill}</span>
            <span className="gap-note">{gap.note}</span>
          </div>
          <div className="gap-track">
            <div
              className="gap-fill"
              style={{ width: `${STATUS_WIDTH[gap.status]}%`, background: STATUS_COLORS[gap.status] }}
            />
          </div>
          <span className={`gap-status status-${gap.status}`}>
            {gap.status === "have" ? "have" : gap.status === "partial" ? "partial" : "missing"}
          </span>
        </div>
      ))}
    </div>
  );
}

export function GapLegend() {
  return (
    <div className="gap-legend">
      <span><i style={{ background: "var(--green)" }} /> you have</span>
      <span><i style={{ background: "var(--yellow)" }} /> partial</span>
      <span><i style={{ background: "var(--red)" }} /> gap</span>
    </div>
  );
}
