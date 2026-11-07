import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Download, Loader2, Share2 } from 'lucide-react';
import { Button, Logo } from '../ui';
import { ExportProvider } from './exportContext';
import { cn } from '../../utils/cn';

// "Share image" for blocks that are not assistant cards (pitches, player profiles, top-10 lists).
// Same machinery as the assistant's ShareableCard: the image is drawn from a second, off-screen
// copy of the content (so the page doesn't change while it is made), whose photos load through
// our image proxy so the canvas isn't tainted, and which ends with the Pitch Me mark. Phones get
// the share sheet, everything else downloads a PNG.

type ShareState = 'idle' | 'working' | 'ready' | 'failed';

const nextFrame = () =>
  new Promise<void>((resolve) => {
    requestAnimationFrame(() => resolve());
    setTimeout(resolve, 100); // a background tab doesn't paint
  });

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

function preferredShareMode(): 'share' | 'save' {
  try {
    const probe = new File([new Blob()], 'image.png', { type: 'image/png' });
    const coarse = window.matchMedia?.('(pointer: coarse)').matches;
    return coarse && navigator.canShare?.({ files: [probe] }) ? 'share' : 'save';
  } catch {
    return 'save';
  }
}

function download(file: File) {
  const url = URL.createObjectURL(file);
  const link = document.createElement('a');
  link.href = url;
  link.download = file.name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** The image's own frame: title, the content, and the Pitch Me mark with the site's address. */
function ShareFrame({ eyebrow, title, subtitle, children }: { eyebrow?: ReactNode; title: ReactNode; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <header className="px-4 pb-3 pt-4">
        {eyebrow && <p className="text-micro font-semibold uppercase text-accent">{eyebrow}</p>}
        <h3 className="font-display text-h3 font-bold text-fg">{title}</h3>
        {subtitle && <p className="mt-0.5 text-data text-fg-muted">{subtitle}</p>}
      </header>
      <div className="px-4 pb-4">{children}</div>
      <footer className="flex items-center justify-between gap-3 border-t border-line px-4 py-2.5">
        <Logo size={16} />
        <span className="truncate text-micro text-fg-subtle">{window.location.host} · not affiliated with the Premier League</span>
      </footer>
    </div>
  );
}

export interface ShareImageButtonProps {
  /** What the image shows, drawn in the image's header (and used as the share sheet's title). */
  title: string;
  eyebrow?: ReactNode;
  subtitle?: ReactNode;
  /** File name without the extension, e.g. `pitch-me-team-of-the-week-gw10`. */
  fileName: string;
  /** Text shared along with the image; the page URL is added. */
  shareText: string;
  /** Link to share; defaults to the current page. */
  url?: string;
  /** The content of the image. Rendered off-screen with `exporting` set (see `useExportPhoto`). */
  children: ReactNode;
  /** Width of the image's content in CSS px (drawn at 2x). */
  width?: number;
  className?: string;
}

/**
 * A small "Share image" button. Clicking it renders `children` in an off-screen copy under an
 * `ExportProvider` with `exporting: true`, turns it into a PNG and shares or downloads it. The
 * content is only rendered while an image is being made.
 */
export function ShareImageButton({ title, eyebrow, subtitle, fileName, shareText, url, children, width = 520, className }: ShareImageButtonProps) {
  const copy = useRef<HTMLDivElement | null>(null);
  const prepared = useRef<File | null>(null);
  const working = useRef(false);
  const [building, setBuilding] = useState(false);
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
      const link = url ? new URL(url, window.location.origin).href : window.location.href;
      try {
        await navigator.share({ files: [file], title, text: `${shareText} ${link}` });
      } catch (err) {
        const name = (err as DOMException)?.name;
        if (name === 'AbortError') return; // closed the share sheet
        if (name === 'NotAllowedError') {
          // Making the image took too long for the browser to count this as the user's tap
          prepared.current = file;
          setState('ready');
          return;
        }
        throw err;
      }
    },
    [mode, shareText, title, url],
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
    working.current = true;
    setState('working');
    try {
      setBuilding(true);
      await nextFrame();
      await nextFrame();
      const node = copy.current;
      if (!node) throw new Error('Image copy not rendered');
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
      await deliver(new File([blob], `${fileName}.png`, { type: 'image/png' }));
    } catch (err) {
      console.error('Sharing the image failed', err);
      setState('failed');
    } finally {
      working.current = false;
      setBuilding(false);
    }
  }, [deliver, fileName]);

  const Icon = state === 'working' ? Loader2 : mode === 'share' ? Share2 : Download;
  const label = {
    idle: 'Share image',
    working: 'Creating image…',
    ready: 'Image ready: tap to share',
    failed: "Couldn't create the image",
  }[state];

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        data-export-hide
        onClick={share}
        disabled={state === 'working'}
        icon={<Icon className={cn('h-4 w-4', state === 'working' && 'animate-spin')} aria-hidden="true" />}
        className={cn(state === 'ready' && 'text-accent', className)}
      >
        {label}
      </Button>
      <span className="sr-only" role="status">
        {state === 'ready' || state === 'failed' ? label : ''}
      </span>
      {building &&
        createPortal(
          <div
            ref={(el) => {
              copy.current = el;
              el?.setAttribute('inert', '');
            }}
            aria-hidden="true"
            className="pointer-events-none fixed left-[-10000px] top-0 bg-bg p-4"
            style={{ width: width + 32 }}
          >
            <ExportProvider value={{ exporting: true }}>
              <ShareFrame eyebrow={eyebrow} title={title} subtitle={subtitle}>
                {children}
              </ShareFrame>
            </ExportProvider>
          </div>,
          document.body,
        )}
    </>
  );
}
