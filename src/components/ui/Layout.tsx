import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '../../utils/cn';

/** Centred page-width container with responsive gutters. */
export function Container({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('mx-auto w-full max-w-page px-4 sm:px-6', className)} {...rest} />;
}

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Featured panels use the brand purple instead of the neutral surface. */
  tone?: 'default' | 'brand';
  padded?: boolean;
}

export function Card({ tone = 'default', padded = true, className, ...rest }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-lg border shadow-card',
        tone === 'brand' ? 'border-brand-2 bg-brand surface-sheen' : 'border-line bg-surface surface-sheen',
        padded && 'p-4 sm:p-5',
        className,
      )}
      {...rest}
    />
  );
}

interface SectionHeadingProps {
  title: ReactNode;
  subtitle?: ReactNode;
  /** Right-aligned action, e.g. a "See all" link. */
  action?: ReactNode;
  as?: 'h2' | 'h3';
  /** Visual size; defaults to match the tag (h2 large, h3 medium). */
  size?: 'lg' | 'md';
  className?: string;
  id?: string;
}

export function SectionHeading({ title, subtitle, action, as: Tag = 'h2', size, className, id }: SectionHeadingProps) {
  const large = (size ?? (Tag === 'h2' ? 'lg' : 'md')) === 'lg';
  return (
    <div className={cn('mb-3 flex items-end justify-between gap-3', className)}>
      <div className="min-w-0">
        <Tag id={id} className={cn('flex items-center gap-2.5 font-display font-bold text-fg', large ? 'text-h2' : 'text-h3')}>
          {/* Large headings get the signature mint-to-cyan tick */}
          {large && <span aria-hidden="true" className="h-[0.9em] w-1 shrink-0 rounded-full" style={{ backgroundImage: 'var(--grad-accent)' }} />}
          {title}
        </Tag>
        {subtitle && <p className="mt-0.5 text-data text-fg-subtle">{subtitle}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

interface PageHeaderProps {
  /** Small label above the title, e.g. "Gameweek 7". */
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({ eyebrow, title, description, actions, className }: PageHeaderProps) {
  return (
    <header className={cn('relative isolate mb-6 py-7 sm:py-9', className)}>
      {/*
        Full-width banner behind the title, Pitch Me's own: deep purple through magenta into
        cyan (mixed over the brand purple so white text keeps 4.5:1 across it, phones
        included), faint pitch markings on the right, the mint-to-cyan line along the bottom.
        It reaches the window edges from inside the page container; .pm-root clips the overhang.
      */}
      <div
        aria-hidden="true"
        className="absolute inset-y-0 left-1/2 -z-10 w-screen -translate-x-1/2 overflow-hidden bg-brand-2"
        style={{
          backgroundImage:
            'linear-gradient(100deg, transparent 0%, rgb(var(--glow-3) / 0.55) 52%, rgb(var(--accent-2) / 0.45) 100%)',
        }}
      >
        <svg
          className="absolute -right-10 top-1/2 h-[150%] -translate-y-1/2 opacity-[0.12] sm:right-[8%]"
          viewBox="0 0 160 100"
          fill="none"
          stroke="white"
          strokeWidth="0.7"
          preserveAspectRatio="xMidYMid meet"
        >
          <rect x="2" y="2" width="156" height="96" rx="1" />
          <line x1="80" y1="2" x2="80" y2="98" />
          <circle cx="80" cy="50" r="13" />
          <rect x="2" y="26" width="22" height="48" />
          <rect x="136" y="26" width="22" height="48" />
          <path d="M24 42 a10 10 0 0 1 0 16" />
          <path d="M136 42 a10 10 0 0 0 0 16" />
        </svg>
        <div className="absolute inset-x-0 bottom-0 h-[3px]" style={{ backgroundImage: 'var(--grad-accent)' }} />
      </div>
      <div className="animate-rise flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          {eyebrow && <p className="mb-1 text-micro font-semibold uppercase text-accent">{eyebrow}</p>}
          <h1 className="font-display text-h1 font-extrabold text-fg drop-shadow-sm sm:text-display">{title}</h1>
          {description && <p className="mt-2 max-w-2xl text-body text-fg/90">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
      </div>
    </header>
  );
}
