import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { MessageCircle } from 'lucide-react';
import { cn } from '../../utils/cn';

/** Pixels of scrolling in one direction before the button reacts (ignores jitter). */
const SCROLL_STEP = 12;
/** Near the top of a page the button always shows. */
const TOP_ZONE = 120;

/**
 * Whether the button should be out of the way: true while the reader scrolls down a page,
 * false again as soon as they scroll up or return to the top. It sits over the bottom-right
 * corner, which on a phone is the last column of every table (the points) and the edge of the
 * pitch, so it steps aside while that content is being read.
 */
function useHiddenWhileScrollingDown(): boolean {
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    let last = window.scrollY;
    // A few comparisons per scroll event; React skips the update when the value is unchanged
    const onScroll = () => {
      const y = window.scrollY;
      if (y < TOP_ZONE) setHidden(false);
      else if (y - last > SCROLL_STEP) setHidden(true);
      else if (last - y > SCROLL_STEP) setHidden(false);
      else return;
      last = y;
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return hidden;
}

/**
 * Sticky call to action on phones and tablets: the assistant is one tap away from any page.
 * It slides away while the reader scrolls down and returns when they scroll up; keyboard focus
 * always brings it back, and it stays out of the open navigation menu (`data-nav-open`, set by
 * SiteHeader).
 */
export function MobileAskButton() {
  const hidden = useHiddenWhileScrollingDown();
  return (
    <Link
      to="/assistant"
      aria-label="Ask the assistant"
      className={cn(
        'fixed bottom-[calc(1rem+env(safe-area-inset-bottom))] right-4 z-40 inline-flex h-12 items-center gap-2 rounded-full bg-accent px-5 font-semibold text-accent-on shadow-pop transition-[transform,opacity] duration-200 hover:scale-[1.03] focus-visible:translate-y-0 focus-visible:opacity-100 focus-visible:outline-offset-4 motion-reduce:transition-none lg:hidden [[data-nav-open]_&]:hidden',
        hidden && 'pointer-events-none translate-y-[calc(100%+2rem)] opacity-0 focus-visible:pointer-events-auto',
      )}
    >
      <MessageCircle className="h-5 w-5" aria-hidden="true" />
      Ask AI
    </Link>
  );
}
