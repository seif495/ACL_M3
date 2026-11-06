import { useEffect, useState } from 'react';
import { Hourglass, Loader2, SearchX } from 'lucide-react';
import { handleApiError, isNotFoundError, isServiceUnavailableError } from '../../services/api';
import { Card, EmptyState, ErrorState, Skeleton } from '../ui';

/** No explanations yet (503): they're written by the daily prediction run. */
export function ExplainUnavailable({ bare = false }: { bare?: boolean }) {
  const state = (
    <EmptyState
      icon={<Hourglass className="h-8 w-8" aria-hidden="true" />}
      title="Explanations appear after the next prediction run"
      description="The model’s next-gameweek predictions are explained once a day, right after they’re made. Check back soon."
    />
  );
  return bare ? state : <Card padded={false}>{state}</Card>;
}

interface ExplainErrorProps {
  error: unknown;
  onRetry?: () => void;
  /** What failed to load, e.g. "the model insights". */
  what: string;
  /** Text for a 404, when it means something specific (a player not in this gameweek). */
  notFound?: { title: string; description: string };
  /** Without the card around it, for use inside another card. */
  bare?: boolean;
}

/** 503 → the friendly "not yet" state; a 404 with a meaning → its own message; anything else → an error with retry. */
export function ExplainError({ error, onRetry, what, notFound, bare = false }: ExplainErrorProps) {
  if (isServiceUnavailableError(error)) return <ExplainUnavailable bare={bare} />;
  const state =
    notFound && isNotFoundError(error) ? (
      <EmptyState icon={<SearchX className="h-8 w-8" aria-hidden="true" />} title={notFound.title} description={notFound.description} />
    ) : (
      <ErrorState title={`Couldn’t load ${what}`} description={handleApiError(error)} onRetry={onRetry} />
    );
  return bare ? state : <Card padded={false}>{state}</Card>;
}

// When the slow-load message appears and changes. The first request after each prediction run
// computes SHAP values for every player's upcoming fixture (about 10-15 s on the server); after
// that the answers are cached and arrive in well under a second, so quick loads never see it.
const SHOW_AFTER_MS = 1500;
const NEARLY_AFTER_MS = 12_000;
const SLOW_AFTER_MS = 30_000;
const EXPECTED_MS = 15_000;

/** Seconds-based stage of a load that has been running since mount: 0 hidden, 1 working, 2 nearly, 3 slow. */
function useLoadStage(): number {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    const timers = [SHOW_AFTER_MS, NEARLY_AFTER_MS, SLOW_AFTER_MS].map((ms, i) => window.setTimeout(() => setStage(i + 1), ms));
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, []);
  return stage;
}

/** What the wait is, shown once a load takes more than a moment. */
function SlowLoadNote({ title, stage }: { title: string; stage: number }) {
  const [started, setStarted] = useState(false);
  useEffect(() => {
    // Start the bar on the next frame so its transition runs from empty
    const frame = window.requestAnimationFrame(() => setStarted(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);
  const detail =
    stage >= 3
      ? 'This is taking longer than usual. It should finish soon; if it doesn’t, refresh the page.'
      : stage === 2
        ? 'Nearly there. Thanks for waiting.'
        : 'The model explains every player’s prediction with SHAP and LIME. The first visit after each daily prediction update works this out for every player and fixture, which takes about 10–15 seconds. After that it loads instantly.';
  return (
    <Card>
      <div className="flex items-start gap-3">
        <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-accent" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-display text-h3 font-bold text-fg">{title}</p>
          <p className="mt-1 text-data text-fg-muted">{detail}</p>
          {/* Paced to the usual wait, not real progress, so it slows near the end and never completes */}
          <div aria-hidden="true" className="mt-3 h-1 overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full w-full origin-left rounded-full bg-accent transition-transform ease-out"
              style={{ transform: `scaleX(${started ? 0.92 : 0.04})`, transitionDuration: `${EXPECTED_MS}ms` }}
            />
          </div>
        </div>
      </div>
    </Card>
  );
}

/**
 * Loading placeholder for a stack of chart cards. With `slowTitle`, a load that takes more than
 * a moment also explains the wait (the first explanation after each prediction run is slow).
 */
export function ExplainSkeleton({ label = 'Loading…', blocks = 2, slowTitle }: { label?: string; blocks?: number; slowTitle?: string }) {
  const stage = useLoadStage();
  const showNote = slowTitle != null && stage >= 1;
  return (
    <div role="status" className="space-y-4">
      {/* Announced once, then again only when the wait gets long */}
      <span className="sr-only">{showNote && stage >= 3 ? `${label} This is taking longer than usual.` : label}</span>
      {showNote && <SlowLoadNote title={slowTitle} stage={stage} />}
      {Array.from({ length: blocks }, (_, i) => (
        <Skeleton key={i} className={i === 0 ? 'h-32 w-full rounded-lg' : 'h-80 w-full rounded-lg'} />
      ))}
    </div>
  );
}
