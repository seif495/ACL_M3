import { useEffect, useRef, useState } from 'react';

const DURATION_MS = 700;

/**
 * Counts from 0 up to `target` the first time the element scrolls into view (ease-out), then
 * follows `target` without animating. Shows the final value at once under reduced motion,
 * without IntersectionObserver, or for non-finite targets.
 */
function willAnimate(target: number): boolean {
  if (!Number.isFinite(target) || typeof window === 'undefined' || typeof IntersectionObserver === 'undefined') return false;
  return !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

export function useCountUp<T extends Element>(target: number): [(el: T | null) => void, number] {
  // Start at 0 when it will count, so the final number doesn't flash first
  const [value, setValue] = useState(() => (willAnimate(target) ? 0 : target));
  const done = useRef(false);
  const [el, setEl] = useState<T | null>(null);

  useEffect(() => {
    if (done.current || !el || !Number.isFinite(target)) {
      setValue(target);
      return;
    }
    if (!willAnimate(target)) {
      done.current = true;
      setValue(target);
      return;
    }
    let frame = 0;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        io.disconnect();
        const start = performance.now();
        const tick = (now: number) => {
          const t = Math.min(1, (now - start) / DURATION_MS);
          setValue(target * (1 - Math.pow(1 - t, 3)));
          if (t < 1) frame = requestAnimationFrame(tick);
          else done.current = true;
        };
        frame = requestAnimationFrame(tick);
      },
      { threshold: 0.3 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [el, target]);

  return [setEl, value];
}
