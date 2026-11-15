import { useEffect, useId, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronDown, Menu, Search, X } from 'lucide-react';
import { Logo } from '../ui';
import { cn } from '../../utils/cn';

export interface NavItem {
  to: string;
  label: string;
  /**
   * Shown in the bar only from 1200px; between the desktop breakpoint (1024px) and that width it
   * moves into "More". The ten items plus More and search need about 1190px, so on a 1024-1180px
   * screen the last ones ran off the edge and took More and the search button with them.
   */
  wide?: boolean;
}

export const PRIMARY_NAV: NavItem[] = [
  { to: '/', label: 'Home' },
  { to: '/my-team', label: 'My team' },
  { to: '/fixtures', label: 'Fixtures' },
  { to: '/gameweek', label: 'Gameweek', wide: true },
  { to: '/table', label: 'Table' },
  { to: '/teams', label: 'Clubs', wide: true },
  { to: '/team-of-the-week', label: 'Team of the Week' },
  { to: '/players', label: 'Players' },
  { to: '/predictions', label: 'Predictions' },
  { to: '/assistant', label: 'Assistant' },
];

export const MORE_NAV: NavItem[] = [
  { to: '/compare', label: 'Compare players' },
  { to: '/model', label: 'Model insights' },
  { to: '/insights', label: 'Captains & differentials' },
  { to: '/trivia', label: 'Trivia' },
  { to: '/glossary', label: 'Glossary' },
  { to: '/settings', label: 'Settings' },
];

interface SiteHeaderProps {
  onOpenSearch?: () => void;
  /** Force which item looks active (design preview only); normally the router decides. */
  activePath?: string;
}

function isActive(path: string, to: string) {
  return to === '/' ? path === '/' : path === to || path.startsWith(`${to}/`);
}

/** Close a popover on outside click or Escape. */
function useDismiss(open: boolean, close: () => void, ref: React.RefObject<HTMLElement>) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, close, ref]);
}

/**
 * Site header: brand bar with the primary nav (mint underline on the active
 * item), a "More" disclosure for secondary pages, player search, and a
 * full-width menu on small screens. A thin mint-to-cyan rule separates it
 * from the page.
 */
export function SiteHeader({ onOpenSearch, activePath }: SiteHeaderProps) {
  const location = useLocation();
  const path = activePath ?? location.pathname;
  const [moreOpen, setMoreOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);
  const mobileRef = useRef<HTMLDivElement>(null);
  const moreId = useId();
  const mobileId = useId();

  useDismiss(moreOpen, () => setMoreOpen(false), moreRef);
  useDismiss(mobileOpen, () => setMobileOpen(false), mobileRef);

  // Navigating closes any open menu.
  useEffect(() => {
    setMoreOpen(false);
    setMobileOpen(false);
  }, [location.pathname]);

  // The open phone menu covers the page: the floating "Ask AI" button stays out of it
  // (MobileAskButton reads this attribute).
  useEffect(() => {
    document.documentElement.toggleAttribute('data-nav-open', mobileOpen);
    return () => document.documentElement.removeAttribute('data-nav-open');
  }, [mobileOpen]);

  const moreActive = MORE_NAV.some((item) => isActive(path, item.to));
  // The items that live in "More" on narrower desktops
  const wideNav = PRIMARY_NAV.filter((item) => item.wide);
  const wideActive = wideNav.some((item) => isActive(path, item.to));

  return (
    <header ref={mobileRef} className="sticky top-0 z-40">
      <div className="bg-brand" style={{ backgroundImage: 'linear-gradient(90deg, rgb(var(--brand)) 55%, rgb(var(--brand-2) / 0.55))' }}>
        <div className="mx-auto flex h-header w-full max-w-page items-stretch gap-2 px-4 sm:px-6">
          <NavLink to="/" className="mr-2 flex shrink-0 items-center rounded-sm min-[1200px]:mr-4" aria-label="Pitch Me home">
            <Logo size={26} />
          </NavLink>

          <nav aria-label="Main" className="hidden flex-1 items-stretch lg:flex">
            {PRIMARY_NAV.map((item) => {
              const active = isActive(path, item.to);
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'relative items-center whitespace-nowrap px-2.5 text-sm font-semibold transition-colors min-[1200px]:px-3',
                    item.wide ? 'hidden min-[1200px]:flex' : 'flex',
                    active ? 'text-fg' : 'text-fg-muted hover:text-fg',
                  )}
                >
                  {item.label}
                  <span
                    aria-hidden="true"
                    className={cn(
                      'absolute inset-x-2.5 bottom-0 h-[3px] rounded-t-sm bg-gradient-to-r from-accent to-accent-2 transition-transform min-[1200px]:inset-x-3',
                      active ? 'scale-x-100' : 'scale-x-0',
                    )}
                  />
                </NavLink>
              );
            })}

            <div ref={moreRef} className="relative flex items-stretch">
              <button
                type="button"
                aria-expanded={moreOpen}
                aria-controls={moreId}
                onClick={() => setMoreOpen((v) => !v)}
                className={cn(
                  'relative flex items-center gap-1 px-2.5 text-sm font-semibold transition-colors min-[1200px]:px-3',
                  moreActive || moreOpen ? 'text-fg' : 'text-fg-muted hover:text-fg',
                  wideActive && !moreActive && !moreOpen && 'max-[1199px]:text-fg',
                )}
              >
                More
                <ChevronDown className={cn('h-4 w-4 transition-transform', moreOpen && 'rotate-180')} aria-hidden="true" />
                {(moreActive || wideActive) && (
                  <span
                    aria-hidden="true"
                    className={cn(
                      'absolute inset-x-2.5 bottom-0 h-[3px] rounded-t-sm bg-gradient-to-r from-accent to-accent-2 min-[1200px]:inset-x-3',
                      !moreActive && 'min-[1200px]:hidden',
                    )}
                  />
                )}
              </button>
              {moreOpen && (
                <ul
                  id={moreId}
                  className="absolute right-0 top-full mt-1 min-w-[12rem] overflow-hidden rounded-md border border-line bg-surface-2 py-1 shadow-pop"
                >
                  {[...wideNav, ...MORE_NAV].map((item) => (
                    <li key={item.to} className={cn(item.wide && 'min-[1200px]:hidden')}>
                      <NavLink
                        to={item.to}
                        aria-current={isActive(path, item.to) ? 'page' : undefined}
                        className={cn(
                          'block px-4 py-2 text-sm hover:bg-brand-2/60',
                          isActive(path, item.to) ? 'font-semibold text-accent' : 'text-fg',
                        )}
                      >
                        {item.label}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </nav>

          <div className="ml-auto flex items-center gap-1">
            <button
              type="button"
              onClick={onOpenSearch}
              // The labelled box needs the full bar width: below 1340px it is the icon alone
              className="flex h-9 items-center gap-2 rounded-md border border-white/15 bg-black/20 px-2.5 text-sm text-fg-muted transition-colors hover:border-white/30 hover:text-fg min-[1340px]:w-44"
            >
              <Search className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="hidden min-[1340px]:inline">Search</span>
              <span className="sr-only min-[1340px]:hidden">Search</span>
              <kbd className="ml-auto hidden rounded-sm border border-white/20 px-1.5 text-micro text-fg-subtle min-[1340px]:inline">/</kbd>
            </button>
            <button
              type="button"
              aria-expanded={mobileOpen}
              aria-controls={mobileId}
              onClick={() => setMobileOpen((v) => !v)}
              className="flex h-9 w-9 items-center justify-center rounded-md text-fg hover:bg-white/10 lg:hidden"
            >
              {mobileOpen ? <X className="h-5 w-5" aria-hidden="true" /> : <Menu className="h-5 w-5" aria-hidden="true" />}
              <span className="sr-only">{mobileOpen ? 'Close menu' : 'Open menu'}</span>
            </button>
          </div>
        </div>
      </div>
      <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-accent to-accent-2" />

      {mobileOpen && (
        <nav id={mobileId} aria-label="Main" className="border-b border-line bg-surface shadow-pop lg:hidden">
          <ul className="mx-auto max-w-page px-2 py-2">
            {[...PRIMARY_NAV, ...MORE_NAV].map((item) => {
              const active = isActive(path, item.to);
              return (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === '/'}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'flex items-center border-l-[3px] px-3 py-2.5 text-body font-semibold',
                      active ? 'border-accent bg-surface-2 text-fg' : 'border-transparent text-fg-muted hover:text-fg',
                    )}
                  >
                    {item.label}
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </nav>
      )}
    </header>
  );
}
