import { Link } from 'react-router-dom';
import { Logo } from '../ui';
import { MORE_NAV, PRIMARY_NAV } from './SiteHeader';
import { RepoLink } from './RepoLink';
import { setConsent } from '../../lib/consent';

const LEGAL_NAV = [
  { to: '/contact', label: 'Contact' },
  { to: '/privacy', label: 'Privacy policy' },
  { to: '/terms', label: 'Terms of use' },
];

export function SiteFooter() {
  return (
    // pb-20 on phones/tablets keeps the floating "Ask AI" button off the last line
    <footer className="relative mt-12 border-t border-line bg-surface pb-20 lg:pb-0">
      <div aria-hidden="true" className="absolute inset-x-0 -top-px h-px bg-gradient-to-r from-transparent via-accent/60 to-transparent" />
      <div className="mx-auto grid w-full max-w-page gap-8 px-4 py-8 sm:px-6 md:grid-cols-[1.4fr_1fr_1fr_1fr]">
        <div>
          <Logo size={24} />
          <p className="mt-3 max-w-sm text-data text-fg-subtle">
            Fantasy Premier League stats, fixtures and machine-learning predictions, with an assistant that answers
            questions from a knowledge graph of every season since 2020-21.
          </p>
          <RepoLink label="Source code on GitHub" className="mt-4" />
        </div>
        <nav aria-label="Footer: main">
          <ul className="space-y-1.5 text-data">
            {PRIMARY_NAV.map((item) => (
              <li key={item.to}>
                <Link to={item.to} className="text-fg-muted hover:text-fg">
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <nav aria-label="Footer: more">
          <ul className="space-y-1.5 text-data">
            {MORE_NAV.map((item) => (
              <li key={item.to}>
                <Link to={item.to} className="text-fg-muted hover:text-fg">
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <nav aria-label="Footer: about">
          <ul className="space-y-1.5 text-data">
            {LEGAL_NAV.map((item) => (
              <li key={item.to}>
                <Link to={item.to} className="text-fg-muted hover:text-fg">
                  {item.label}
                </Link>
              </li>
            ))}
            <li>
              {/* Reopens the consent banner so the choice can be changed at any time */}
              <button type="button" onClick={() => setConsent(null)} className="text-fg-muted hover:text-fg">
                Privacy choices
              </button>
            </li>
          </ul>
        </nav>
      </div>
      <div className="border-t border-line">
        <p className="mx-auto w-full max-w-page px-4 py-4 text-micro text-fg-subtle sm:px-6">
          Pitch Me is a fan-made project. Not affiliated with, endorsed by or connected to the Premier League or
          Fantasy Premier League. Data from the public FPL API.
        </p>
      </div>
    </footer>
  );
}
