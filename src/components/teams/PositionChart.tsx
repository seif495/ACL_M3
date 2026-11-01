import { useState } from 'react';
import { useElementWidth } from '../explain/useElementWidth';

const HEIGHT = 180;
const PAD = { top: 12, right: 12, bottom: 24, left: 28 };

/** League position after each gameweek, 1st at the top, with a hover readout. */
export function PositionChart({
  history,
  teams,
  color,
}: {
  history: { gameweek: number; position: number; points: number }[];
  teams: number;
  /** "R G B" of the club's colour. */
  color: string;
}) {
  const [ref, width] = useElementWidth<HTMLDivElement>(600);
  const [active, setActive] = useState<number | null>(null);
  if (history.length < 2) return <p className="text-data text-fg-subtle">The chart starts after the second gameweek.</p>;

  const w = Math.max(width, 240);
  const innerW = w - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const first = history[0].gameweek;
  const last = history[history.length - 1].gameweek;
  const x = (gw: number) => PAD.left + ((gw - first) / Math.max(1, last - first)) * innerW;
  const y = (pos: number) => PAD.top + ((pos - 1) / Math.max(1, teams - 1)) * innerH;
  const path = history.map((h, i) => `${i ? 'L' : 'M'}${x(h.gameweek).toFixed(1)},${y(h.position).toFixed(1)}`).join(' ');
  const area = `${path} L${x(last).toFixed(1)},${(PAD.top + innerH).toFixed(1)} L${x(first).toFixed(1)},${(PAD.top + innerH).toFixed(1)} Z`;
  const shown = active != null ? history[active] : history[history.length - 1];
  const ticks = [1, Math.ceil(teams / 4), Math.ceil(teams / 2), Math.ceil((3 * teams) / 4), teams];

  return (
    <figure className="min-w-0">
      <p className="mb-1 text-data text-fg-muted" aria-hidden="true">
        GW{shown.gameweek}: <span className="font-semibold text-fg">{ordinal(shown.position)}</span> · {shown.points} pts
      </p>
      {/* overflow-hidden + min-w-0: the svg's first (fallback) width can't widen the card */}
      <div ref={ref} className="w-full min-w-0 overflow-hidden">
        <svg width={w} height={HEIGHT} role="img" aria-label={`League position by gameweek, ${ordinal(history[history.length - 1].position)} now`} onMouseLeave={() => setActive(null)}>
          <defs>
            <linearGradient id="pos-fill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={`rgb(${color})`} stopOpacity="0.35" />
              <stop offset="100%" stopColor={`rgb(${color})`} stopOpacity="0" />
            </linearGradient>
          </defs>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={w - PAD.right} y1={y(t)} y2={y(t)} stroke="rgb(var(--border))" strokeDasharray="3 4" />
              <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" fontSize="11" fill="rgb(var(--text-subtle))">{t}</text>
            </g>
          ))}
          <path d={area} fill="url(#pos-fill)" />
          <path d={path} fill="none" stroke={`rgb(${color})`} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
          {history.map((h, i) => (
            <g key={h.gameweek}>
              <circle cx={x(h.gameweek)} cy={y(h.position)} r={active === i ? 5 : 3} fill={`rgb(${color})`} stroke="rgb(var(--surface))" strokeWidth="2" />
              <rect
                x={x(h.gameweek) - innerW / history.length / 2}
                y={PAD.top}
                width={innerW / history.length}
                height={innerH}
                fill="transparent"
                onMouseEnter={() => setActive(i)}
              />
            </g>
          ))}
          {[first, last].map((gw) => (
            <text key={gw} x={x(gw)} y={HEIGHT - 6} textAnchor={gw === first ? 'start' : 'end'} fontSize="11" fill="rgb(var(--text-subtle))">
              GW{gw}
            </text>
          ))}
        </svg>
      </div>
      <figcaption className="sr-only">{history.map((h) => `GW${h.gameweek} ${ordinal(h.position)}`).join(', ')}</figcaption>
    </figure>
  );
}

function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
}
