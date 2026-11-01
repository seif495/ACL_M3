import { useEffect, useState, type ReactNode } from 'react';
import { useCountUp } from '../../hooks/useCountUp';
import { cn } from '../../utils/cn';

interface StatCardProps {
  label: ReactNode;
  value: ReactNode;
  /** Small line under the value, e.g. "Haaland · MCI". */
  detail?: ReactNode;
  icon?: ReactNode;
  /** Highlight the value in mint. */
  highlight?: boolean;
  className?: string;
}

/** A whole number or a decimal ("66.7") counts up when it first appears; anything else shows as is. */
function CountingValue({ value }: { value: ReactNode }) {
  const text = typeof value === 'number' ? String(value) : typeof value === 'string' ? value : null;
  const match = text != null ? /^(-?)(\d{1,3}(?:,\d{3})*|\d+)(\.\d+)?$/.exec(text.trim()) : null;
  const target = match ? Number(text!.replace(/,/g, '')) : NaN;
  const decimals = match?.[3] ? match[3].length - 1 : 0;
  const grouped = !!match && text!.includes(',');
  const [ref, shown] = useCountUp<HTMLSpanElement>(target);
  if (!match) return <>{value}</>;
  const display = grouped
    ? Math.round(shown).toLocaleString()
    : shown.toFixed(decimals);
  return (
    <span ref={ref}>
      <span aria-hidden="true">{display}</span>
      <span className="sr-only">{text}</span>
    </span>
  );
}

export function StatCard({ label, value, detail, icon, highlight, className }: StatCardProps) {
  return (
    <div className={cn('surface-sheen relative overflow-hidden rounded-lg border border-line bg-surface p-4 shadow-card', className)}>
      {highlight && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full blur-2xl"
          style={{ background: 'rgb(var(--accent) / 0.16)' }}
        />
      )}
      <div className="relative flex items-center justify-between gap-2">
        <p className="text-micro font-semibold uppercase text-fg-subtle">{label}</p>
        {icon && <span className="text-fg-subtle">{icon}</span>}
      </div>
      <p className={cn('relative mt-1 font-display text-h1 font-bold tabular-nums', highlight ? 'text-gradient' : 'text-fg')}>
        <CountingValue value={value} />
      </p>
      {detail && <p className="relative mt-0.5 truncate text-data text-fg-muted">{detail}</p>}
    </div>
  );
}

function parts(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return { d: Math.floor(s / 86400), h: Math.floor((s % 86400) / 3600), m: Math.floor((s % 3600) / 60) };
}

interface CountdownProps {
  /** ISO timestamp or Date of the deadline. */
  target: string | Date;
  /** Shown once the deadline has passed. */
  passedLabel?: ReactNode;
  className?: string;
}

/**
 * Days/hours/minutes until a deadline. Ticks every 30 s; minute precision is all
 * a deadline needs, and it avoids announcing a change every second.
 */
export function Countdown({ target, passedLabel = 'Deadline passed', className }: CountdownProps) {
  const targetMs = new Date(target).getTime();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  if (Number.isNaN(targetMs)) return null;
  const remaining = targetMs - now;
  if (remaining <= 0) return <span className={cn('font-semibold text-fg-muted', className)}>{passedLabel}</span>;

  const { d, h, m } = parts(remaining);
  const units: [number, string, string][] = [
    [d, 'd', d === 1 ? 'day' : 'days'],
    [h, 'h', h === 1 ? 'hour' : 'hours'],
    [m, 'm', m === 1 ? 'minute' : 'minutes'],
  ];
  const visible = d > 0 ? units : units.slice(1);

  return (
    <time dateTime={new Date(targetMs).toISOString()} className={cn('inline-flex items-baseline gap-2 tabular-nums', className)}>
      {visible.map(([n, short, long]) => (
        <span key={short}>
          <span className="font-display text-h3 font-bold text-fg">{n}</span>
          <span aria-hidden="true" className="ml-0.5 text-data text-fg-subtle">{short}</span>
          <span className="sr-only"> {long}</span>
        </span>
      ))}
    </time>
  );
}
