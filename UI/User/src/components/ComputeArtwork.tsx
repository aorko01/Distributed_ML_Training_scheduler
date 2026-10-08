/** Decorative topology: bundled SVG, no requests or implied cluster telemetry. */
export default function ComputeArtwork({
  compact = false,
}: {
  compact?: boolean;
}) {
  const patternId = compact ? "topology-dots-small" : "topology-dots";
  return (
    <div
      className={`compute-artwork${compact ? " compute-artwork--compact" : ""}`}
      aria-hidden="true"
    >
      <svg viewBox="0 0 480 340" fill="none">
        <defs>
          <pattern
            id={patternId}
            width="20"
            height="20"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="1" cy="1" r=".65" fill="currentColor" opacity=".2" />
          </pattern>
        </defs>
        <rect width="480" height="340" fill={`url(#${patternId})`} />
        <g stroke="currentColor" strokeWidth=".8" opacity=".18">
          <path d="M40 248 240 132l200 116M40 208 240 92l200 116M40 288 240 172l200 116M80 310V112M400 310V112M160 320V72M320 320V72" />
        </g>
        <g className="topology-paths" stroke="currentColor" strokeWidth="1">
          <path d="m146 159 94 54 94-54M240 213v70M100 224l46-26M380 224l-46-26M240 78v35" />
          <circle cx="100" cy="224" r="3" fill="currentColor" />
          <circle cx="380" cy="224" r="3" fill="currentColor" />
          <circle cx="240" cy="78" r="3" fill="currentColor" />
          <circle cx="240" cy="283" r="3" fill="currentColor" />
        </g>
        {[
          { x: 240, y: 115 },
          { x: 146, y: 170 },
          { x: 334, y: 170 },
        ].map(({ x, y }, i) => (
          <g key={i} className={`topology-node topology-node-${i}`}>
            <path
              d={`M${x} ${y - 35}l40 23v47l-40 23-40-23v-47l40-23Z`}
              fill="var(--bg-secondary)"
              stroke="currentColor"
              strokeWidth="1.25"
            />
            <path
              d={`m${x - 40} ${y - 12} 40 23 40-23M${x} ${y + 11}v47`}
              stroke="currentColor"
              strokeWidth="1.25"
            />
            <path
              d={`m${x - 27} ${y + 9} 13 7m-13 2 13 7m-13 2 13 7`}
              stroke="currentColor"
              opacity=".5"
            />
            <path
              d={`m${x + 12} ${y + 18} 16-9m-16 19 16-9`}
              stroke="currentColor"
              opacity=".5"
            />
          </g>
        ))}
        <text
          x="34"
          y="43"
          fill="currentColor"
          fontSize="9"
          fontFamily="var(--font-mono)"
          letterSpacing="2"
          opacity=".6"
        >
          DISTRIBUTED BY DESIGN
        </text>
        <text
          x="337"
          y="310"
          fill="currentColor"
          fontSize="9"
          fontFamily="var(--font-mono)"
          opacity=".5"
        >
          [ compute ∞ ]
        </text>
      </svg>
    </div>
  );
}
