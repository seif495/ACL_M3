import type { CSSProperties, ReactNode } from 'react';
import { clubColors } from '../../utils/clubColors';
import { cn } from '../../utils/cn';

/**
 * A page-top band lit by one club's colours (or two clubs', split, for a match): soft glows of
 * the colours over the dark brand surface, a big faded crest, and a colour stripe along the
 * bottom. Text stays on the dark surface, so contrast is the design system's.
 */
export function ClubHero({
  home,
  away,
  crest,
  className,
  children,
}: {
  home: string;
  /** A second club: its colours light the right half. */
  away?: string;
  /** Crest image path(s) shown faded behind the content. */
  crest?: { home?: string | null; away?: string | null };
  className?: string;
  children: ReactNode;
}) {
  const h = clubColors(home);
  const a = away ? clubColors(away) : null;
  const style = {
    '--c1': h.primary,
    '--c2': a ? a.primary : h.secondary,
  } as CSSProperties;
  return (
    <section
      style={style}
      className={cn('relative isolate overflow-hidden border-b border-line bg-brand', className)}
    >
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10"
        style={{
          background: a
            ? 'radial-gradient(60% 120% at 0% 0%, rgb(var(--c1) / 0.55), transparent 70%), radial-gradient(60% 120% at 100% 0%, rgb(var(--c2) / 0.55), transparent 70%), linear-gradient(180deg, rgb(var(--brand)) 0%, rgb(var(--bg)) 100%)'
            : 'radial-gradient(70% 140% at 0% 0%, rgb(var(--c1) / 0.6), transparent 65%), radial-gradient(50% 120% at 100% 100%, rgb(var(--c2) / 0.22), transparent 70%), linear-gradient(180deg, rgb(var(--brand)) 0%, rgb(var(--bg)) 100%)',
        }}
      />
      {crest?.home && (
        <img
          src={crest.home}
          alt=""
          aria-hidden="true"
          className={cn(
            'pointer-events-none absolute -z-10 h-72 w-72 object-contain opacity-[0.09] sm:h-96 sm:w-96',
            a ? '-left-16 -top-10' : '-right-10 -top-16 sm:right-6',
          )}
        />
      )}
      {crest?.away && (
        <img
          src={crest.away}
          alt=""
          aria-hidden="true"
          className="pointer-events-none absolute -right-16 -top-10 -z-10 h-72 w-72 object-contain opacity-[0.09] sm:h-96 sm:w-96"
        />
      )}
      {children}
      <div
        aria-hidden="true"
        className="absolute inset-x-0 bottom-0 h-1"
        style={{
          background: a
            ? 'linear-gradient(90deg, rgb(var(--c1)) 0%, rgb(var(--c1)) 50%, rgb(var(--c2)) 50%, rgb(var(--c2)) 100%)'
            : 'linear-gradient(90deg, rgb(var(--c1)), rgb(var(--c2)))',
        }}
      />
    </section>
  );
}
