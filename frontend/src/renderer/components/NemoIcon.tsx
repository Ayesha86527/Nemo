import React, { useId } from "react";

interface Props {
  size?: number;
}

export default function NemoIcon({ size = 28 }: Props) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const body = `nemo-body-${id}`;
  const tail = `nemo-tail-${id}`;
  const clip = `nemo-clip-${id}`;

  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id={body} x1="12" y1="18" x2="48" y2="48" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#F2A796" />
          <stop offset="1" stopColor="#DD7460" />
        </linearGradient>
        <linearGradient id={tail} x1="44" y1="24" x2="60" y2="42" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#EC9180" />
          <stop offset="1" stopColor="#D96C57" />
        </linearGradient>
        <clipPath id={clip}>
          <ellipse cx="28" cy="33" rx="19" ry="14" />
        </clipPath>
      </defs>

      {/* dorsal fin */}
      <path d="M20 20 Q27 9 37 19 L32 23 Q28 18 23 22 Z" fill="#D96C57" />
      {/* tail */}
      <path d="M44 33 L59 21 Q61.5 33 59 45 Z" fill={`url(#${tail})`} />
      {/* body */}
      <ellipse cx="28" cy="33" rx="19" ry="14" fill={`url(#${body})`} />
      {/* clownfish bands, clipped to the body */}
      <g clipPath={`url(#${clip})`}>
        <path d="M19 16 Q24 33 19 50 L26 50 Q30 33 26 16 Z" fill="#F9F8F8" opacity="0.92" />
        <path d="M34 17 Q38 33 34 49 L40 49 Q43.5 33 40 17 Z" fill="#F9F8F8" opacity="0.85" />
        <ellipse cx="28" cy="44" rx="14" ry="6" fill="#272838" opacity="0.14" />
      </g>
      {/* pectoral fin */}
      <path d="M27 37 Q31 44 25 46 Q23 41 24 37.5 Z" fill="#C95F4B" opacity="0.85" />
      {/* eye */}
      <circle cx="16.5" cy="29.5" r="2.6" fill="#272838" />
      <circle cx="15.7" cy="28.6" r="0.9" fill="#F9F8F8" />
      {/* body sheen */}
      <path d="M14 24 Q22 17.5 34 19.5" stroke="#F9F8F8" strokeOpacity="0.35" strokeWidth="1.6" strokeLinecap="round" fill="none" />
    </svg>
  );
}
