import { Component, useCallback, useEffect, useRef, useState, type ErrorInfo, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { CornerDownRight } from 'lucide-react';
import type { AnswerCard } from '../../../services/api';
import { Skeleton } from '../../ui';
import { cn } from '../../../utils/cn';
import { AskProvider, ExportProvider, PhotoLookupProvider, type ShareState } from './shared';
import { PlayerAnswerCard } from './PlayerAnswerCard';
import { ComparisonAnswerCard } from './ComparisonAnswerCard';
import { LeaderboardAnswerCard } from './LeaderboardAnswerCard';
import { FixturePicksAnswerCard } from './FixturePicksAnswerCard';
import { PredictionAnswerCard } from './PredictionAnswerCard';
import { TeamAnswerCard } from './TeamAnswerCard';
import { FixturesAnswerCard } from './FixturesAnswerCard';
import { SeasonAnswerCard } from './SeasonAnswerCard';
import { PitchAnswerCard } from './PitchAnswerCard';

/** A card that fails to render is left out; the answer text and other cards stay. */
class CardBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Answer card failed to render', error, info.componentStack);
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function CardView({ card }: { card: AnswerCard }) {
  switch (card.type) {
    case 'fixture_picks':
      return <FixturePicksAnswerCard card={card} />;
    case 'player':
      return <PlayerAnswerCard card={card} />;
    case 'comparison':
      return <ComparisonAnswerCard card={card} />;
    case 'leaderboard':
      return <LeaderboardAnswerCard card={card} />;
    case 'prediction':
      return <PredictionAnswerCard card={card} />;
    case 'team':
      return <TeamAnswerCard card={card} />;
    case 'fixtures':
      return <FixturesAnswerCard card={card} />;
    case 'season':
      return <SeasonAnswerCard card={card} />;
    case 'pitch':
      return <PitchAnswerCard card={card} />;
    default:
      return null; // a card type this build doesn't know yet
  }
}

/** A short name for a card, for screen-reader summaries ("Player: Erling Haaland"). */
export function cardLabel(card: AnswerCard): string {
  switch (card.type) {
    case 'fixture_picks':
      return `${card.title}${card.gameweek ? `, gameweek ${card.gameweek}` : ''}`;
    case 'player':
      return `Player: ${card.player.name}`;
    case 'comparison':
      return `Comparison: ${card.players[0].name} and ${card.players[1].name}`;
    case 'leaderboard':
      return `${card.title}, ${card.subtitle}`;
    case 'prediction':
      return `${card.title}, gameweek ${card.gameweek}`;
    case 'team':
      return `Team: ${card.team}`;
    case 'fixtures':
      return card.title;
    case 'season':
      return `Season ${card.season}`;
    case 'pitch':
      return `${card.mode === 'predicted' ? 'Best squad' : 'Dream team'}, gameweek ${card.gameweek}`;
    default:
      return 'Card';
  }
}

// --- Sharing a card as an image ------------------------------------------------------------

/** After the next paint; a background tab doesn't paint, so give up waiting after 100 ms. */
const nextFrame = () =>
  new Promise<void>((resolve) => {
    requestAnimationFrame(() => resolve());
    setTimeout(resolve, 100);
  });

/** Waits until every photo in `node` has loaded or failed (a failed one shows its fallback). */
async function imagesSettled(node: HTMLElement, timeoutMs = 6000) {
  const pending = Array.from(node.querySelectorAll('img'))
    .filter((img) => {
      img.loading = 'eager'; // the copy is off-screen, where lazy images never load
      return !img.complete;
    })
    .map(
      (img) =>
        new Promise<void>((resolve) => {
          img.addEventListener('load', () => resolve(), { once: true });
          img.addEventListener('error', () => resolve(), { once: true });
        }),
    );
  await Promise.race([Promise.all(pending), new Promise((resolve) => setTimeout(resolve, timeoutMs))]);
  await nextFrame();
  await nextFrame();
}

/** Phones get the share sheet; elsewhere the image is downloaded. */
function preferredShareMode(): 'share' | 'save' {
  try {
    const probe = new File([new Blob()], 'card.png', { type: 'image/png' });
    const coarse = window.matchMedia?.('(pointer: coarse)').matches;
    return coarse && navigator.canShare?.({ files: [probe] }) ? 'share' : 'save';
  } catch {
    return 'save';
  }
}

function fileName(card: AnswerCard): string {
  const slug = cardLabel(card)
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  return `pitch-me-${slug || 'card'}.png`;
}

function download(file: File) {
  const url = URL.createObjectURL(file);
  const link = document.createElement('a');
  link.href = url;
  link.download = file.name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/**
 * A card with a "Share/Save as image" button. The image is drawn from a second copy of the card,
 * rendered off-screen at the same width, so the card on screen doesn't change while it's made:
 * the copy loads photos through our image proxy (other hosts' images would block the export)
 * and ends with the Pitch Me mark instead of links.
 */
function ShareableCard({ card, children }: { card: AnswerCard; children: ReactNode }) {
  const onPage = useRef<HTMLDivElement>(null);
  const copy = useRef<HTMLDivElement | null>(null);
  const prepared = useRef<File | null>(null);
  const working = useRef(false);
  const [copyWidth, setCopyWidth] = useState<number | null>(null);
  const [state, setState] = useState<ShareState>('idle');
  const [mode] = useState(preferredShareMode);

  useEffect(() => {
    if (state !== 'failed') return;
    const timer = setTimeout(() => setState('idle'), 4000);
    return () => clearTimeout(timer);
  }, [state]);

  const deliver = useCallback(
    async (file: File) => {
      if (mode === 'save') {
        download(file);
        return;
      }
      try {
        await navigator.share({ files: [file], title: cardLabel(card) });
      } catch (err) {
        const name = (err as DOMException)?.name;
        if (name === 'AbortError') return; // closed the share sheet
        if (name === 'NotAllowedError') {
          // Making the image took too long for the browser to count this as the user's tap:
          // keep the image for the next one
          prepared.current = file;
          setState('ready');
          return;
        }
        throw err;
      }
    },
    [card, mode],
  );

  const share = useCallback(async () => {
    if (working.current) return;
    if (prepared.current) {
      const file = prepared.current;
      prepared.current = null;
      setState('idle');
      await deliver(file).catch(() => setState('failed'));
      return;
    }
    const width = onPage.current?.getBoundingClientRect().width;
    if (!width) return;
    working.current = true;
    setState('working');
    try {
      setCopyWidth(Math.round(width));
      await nextFrame();
      await nextFrame();
      const node = copy.current;
      if (!node) throw new Error('Card copy not rendered');
      await Promise.all([document.fonts?.ready, imagesSettled(node)]);
      const { toBlob } = await import('html-to-image');
      const blob = await toBlob(node, {
        pixelRatio: 2,
        // Every photo is /api/images/proxy?url=...: without the query string all of them share
        // one cache entry and the image repeats the first photo
        includeQueryParams: true,
        style: { position: 'static', left: 'auto', top: 'auto' }, // the copy sits off-screen
        filter: (el) => !(el instanceof HTMLElement && 'exportHide' in el.dataset),
      });
      if (!blob) throw new Error('No image');
      setState('idle');
      await deliver(new File([blob], fileName(card), { type: 'image/png' }));
    } catch (err) {
      console.error('Sharing the card failed', err);
      setState('failed');
    } finally {
      working.current = false;
      setCopyWidth(null);
    }
  }, [card, deliver]);

  return (
    <div ref={onPage}>
      <ExportProvider value={{ exporting: false, share, shareState: state, shareMode: mode }}>{children}</ExportProvider>
      {copyWidth != null &&
        createPortal(
          <div
            ref={(el) => {
              copy.current = el;
              el?.setAttribute('inert', '');
            }}
            aria-hidden="true"
            className="pointer-events-none fixed left-[-10000px] top-0 bg-bg p-4"
            style={{ width: copyWidth + 32 }}
          >
            <ExportProvider value={{ exporting: true }}>
              <CardView card={card} />
            </ExportProvider>
          </div>,
          document.body,
        )}
    </div>
  );
}

interface AnswerCardsProps {
  cards: AnswerCard[];
  /** Look up photos the backend didn't send (off for /design's made-up players). */
  lookupPhotos?: boolean;
  /** Asks the assistant about a player tapped on a card; without it players link to their profile. */
  onAsk?: (question: string) => void;
  askDisabled?: boolean;
  className?: string;
}

/** The visual blocks of an assistant answer, revealed one after another. */
export function AnswerCards({ cards, lookupPhotos = true, onAsk, askDisabled, className }: AnswerCardsProps) {
  const reduceMotion = useReducedMotion();
  if (!cards.length) return null;
  return (
    <PhotoLookupProvider enabled={lookupPhotos}>
      <AskProvider ask={onAsk} disabled={askDisabled}>
        <div className={cn('mt-3 space-y-3', className)}>
          {cards.map((card, i) => (
            <motion.div
              key={card.id ?? i}
              initial={reduceMotion ? false : { opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25, delay: reduceMotion ? 0 : 0.1 + i * 0.08, ease: 'easeOut' }}
            >
              <CardBoundary>
                <ShareableCard card={card}>
                  <CardView card={card} />
                </ShareableCard>
              </CardBoundary>
            </motion.div>
          ))}
        </div>
      </AskProvider>
    </PhotoLookupProvider>
  );
}

interface FollowUpsProps {
  questions: string[];
  onAsk: (question: string) => void;
  disabled?: boolean;
  className?: string;
}

/** Suggested next questions; each one sends straight away. */
export function FollowUps({ questions, onAsk, disabled, className }: FollowUpsProps) {
  if (!questions.length) return null;
  return (
    <nav aria-label="Suggested follow-up questions" className={cn('mt-3', className)}>
      <p className="mb-1.5 text-micro font-semibold uppercase text-fg-subtle">Ask next</p>
      <ul className="flex flex-wrap gap-2">
        {questions.map((q) => (
          <li key={q} className="min-w-0 max-w-full">
            <button
              type="button"
              onClick={() => onAsk(q)}
              disabled={disabled}
              className={cn(
                'inline-flex max-w-full items-center gap-1.5 rounded-full border border-line-strong bg-surface px-3 py-1.5 text-left text-data text-fg-muted transition-colors',
                'hover:border-accent hover:text-fg disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-line-strong disabled:hover:text-fg-muted',
              )}
            >
              <CornerDownRight className="h-3.5 w-3.5 shrink-0 text-accent" aria-hidden="true" />
              <span className="min-w-0 break-words">{q}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** Placeholder while an answer is on its way: the shape of a card, not its data. */
export function AnswerCardSkeleton({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className={cn('overflow-hidden rounded-lg border border-line bg-surface', className)}>
      <div className="flex items-center gap-3 p-4">
        <Skeleton className="h-14 w-14 rounded-md" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-5 w-40 max-w-full" />
          <Skeleton className="h-3 w-32" />
        </div>
      </div>
      <div className="grid grid-cols-3 gap-px border-t border-line bg-line sm:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className={cn('space-y-1.5 bg-surface px-3 py-2.5', i >= 3 && 'hidden sm:block')}>
            <Skeleton className="h-2.5 w-10" />
            <Skeleton className="h-4 w-8" />
          </div>
        ))}
      </div>
      <div className="flex h-16 items-end gap-[2px] border-t border-line px-4 py-3">
        {[40, 75, 55, 90, 30, 65, 50, 80].map((h, i) => (
          <div key={i} className="max-w-6 flex-1 animate-pulse rounded-t-[4px] bg-surface-2" style={{ height: `${h}%` }} />
        ))}
      </div>
    </div>
  );
}
