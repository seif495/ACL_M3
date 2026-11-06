import { Link } from 'react-router-dom';
import { BarChart3 } from 'lucide-react';
import { Button } from '../ui';
import { setConsent, useConsent } from '../../lib/consent';

/**
 * Asks once whether visits may add the browser's timezone and visitor flags (lib/visitor.ts) to the
 * cookie-free page-view counter, which counts views anonymously either way. The choice can be
 * changed later from the footer or the privacy page.
 *
 * Sticky, not fixed: it takes its own space at the end of the page column, so it never hides
 * the bottom of a page or the assistant's question box (the chat page shrinks to make room).
 */
export function ConsentBanner() {
  const consent = useConsent();
  if (consent !== null) return null;

  return (
    <section
      aria-label="Privacy choices"
      className="sticky bottom-0 z-50 shrink-0 border-t border-line bg-surface-2/95 shadow-pop backdrop-blur-sm"
    >
      <div className="mx-auto flex w-full max-w-page flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:gap-6 sm:px-6">
        <BarChart3 className="hidden h-6 w-6 shrink-0 text-accent sm:block" aria-hidden="true" />
        <p className="flex-1 text-data text-fg-muted">
          <strong className="font-semibold text-fg">Help improve Pitch Me?</strong> We count page views anonymously,
          with no cookies and nothing that identifies you. With your OK we also count visitors: your browser sends its
          timezone, so we see roughly which country visits come from, and keeps the date of your last visit.{' '}
          <Link to="/privacy" className="font-semibold text-accent hover:underline">
            Privacy policy
          </Link>
        </p>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" size="sm" onClick={() => setConsent('declined')}>
            Decline
          </Button>
          {/* Equal weight for both choices: no nudging towards Accept */}
          <Button variant="secondary" size="sm" onClick={() => setConsent('accepted')}>
            Accept
          </Button>
        </div>
      </div>
    </section>
  );
}
