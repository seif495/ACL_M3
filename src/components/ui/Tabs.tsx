import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { cn } from '../../utils/cn';

export interface TabItem<T extends string> {
  value: T;
  label: ReactNode;
}

interface TabsProps<T extends string> {
  items: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  /** Accessible name for the tab list. */
  label: string;
  /** Prefix for tab/panel ids; panels use `${idPrefix}-panel-${value}`. */
  idPrefix: string;
  className?: string;
}

/** A tab's horizontal padding (px-3 on both sides) and the gap between tabs (gap-1), in px. */
const TAB_PADDING_X = 24;
const TAB_GAP = 4;

/**
 * Whether the tabs need more than one row: the labels' own widths (which don't change when the
 * tabs stretch to fill a wrapped row), padding and gaps against the list's width. Re-measured when
 * the list resizes and when a label does (fonts loading, a label filled in later).
 */
function useWrapWhenCrowded(count: number, signature: string) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const labelRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const [wrap, setWrap] = useState(false);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const measure = () => {
      const labels = labelRefs.current.slice(0, count);
      const needed =
        labels.reduce((sum, el) => sum + (el ? el.getBoundingClientRect().width : 0) + TAB_PADDING_X, 0) +
        TAB_GAP * Math.max(0, count - 1);
      setWrap(needed > list.clientWidth + 0.5);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    for (const el of labelRefs.current.slice(0, count)) if (el) observer.observe(el);
    return () => observer.disconnect();
  }, [count, signature]);

  return { listRef, labelRefs, wrap };
}

/**
 * Underline tabs (mint bar under the active tab). Implements the WAI-ARIA tabs
 * pattern: roving tabindex, arrow/Home/End keys, automatic activation.
 *
 * When the tabs don't fit on one row (phones), they wrap onto more rows and stretch to fill them,
 * each with its own divider, so every tab stays visible instead of hiding behind a sideways scroll.
 */
export function Tabs<T extends string>({ items, value, onChange, label, idPrefix, className }: TabsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const { listRef, labelRefs, wrap } = useWrapWhenCrowded(items.length, items.map((t) => t.value).join('|'));

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = items.findIndex((t) => t.value === value);
    let next = -1;
    if (e.key === 'ArrowRight') next = (i + 1) % items.length;
    else if (e.key === 'ArrowLeft') next = (i - 1 + items.length) % items.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = items.length - 1;
    if (next < 0) return;
    e.preventDefault();
    onChange(items[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      // The divider is an inset shadow, not a border: the tabs' mint underline paints over it
      // without overhanging the row (a -1px overlap made the row 1px taller than its box and
      // showed a useless vertical scrollbar). Wrapped, each tab draws its own divider instead.
      // Horizontal scroll stays as a fallback before the first measurement.
      className={cn(
        'flex gap-x-1',
        wrap ? 'flex-wrap' : 'overflow-x-auto overflow-y-hidden shadow-[inset_0_-1px_0_rgb(var(--border))]',
        className,
      )}
    >
      {items.map((tab, i) => {
        const selected = tab.value === value;
        return (
          <button
            key={tab.value}
            ref={(el) => (refs.current[i] = el)}
            role="tab"
            type="button"
            id={`${idPrefix}-tab-${tab.value}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel-${tab.value}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.value)}
            className={cn(
              'relative whitespace-nowrap px-3 py-2.5 text-sm font-semibold transition-colors',
              'border-b-[3px] border-transparent',
              wrap && 'flex-auto text-center',
              selected ? 'text-fg' : 'text-fg-subtle hover:text-fg',
            )}
          >
            <span ref={(el) => (labelRefs.current[i] = el)}>{tab.label}</span>
            {wrap && <span aria-hidden="true" className="absolute inset-x-0 -bottom-[3px] h-px bg-line" />}
            {selected && (
              <span
                aria-hidden="true"
                className="absolute inset-x-0 -bottom-[3px] h-[3px] rounded-t-sm"
                style={{ backgroundImage: 'var(--grad-accent)' }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}

interface TabPanelProps {
  idPrefix: string;
  value: string;
  children: ReactNode;
  className?: string;
}

export function TabPanel({ idPrefix, value, children, className }: TabPanelProps) {
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel-${value}`}
      aria-labelledby={`${idPrefix}-tab-${value}`}
      tabIndex={0}
      className={cn('focus-visible:outline-offset-4', className)}
    >
      {children}
    </div>
  );
}
