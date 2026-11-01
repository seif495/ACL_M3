import { useRef, type KeyboardEvent, type ReactNode } from 'react';
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

/**
 * Underline tabs (mint bar under the active tab). Implements the WAI-ARIA tabs
 * pattern: roving tabindex, arrow/Home/End keys, automatic activation.
 */
export function Tabs<T extends string>({ items, value, onChange, label, idPrefix, className }: TabsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

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
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      // The divider is an inset shadow, not a border: the tabs' mint underline paints over it
      // without overhanging the row (a -1px overlap made the row 1px taller than its box and
      // showed a useless vertical scrollbar). Horizontal scroll stays for narrow screens.
      className={cn(
        'flex gap-1 overflow-x-auto overflow-y-hidden shadow-[inset_0_-1px_0_rgb(var(--border))]',
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
              selected ? 'text-fg' : 'text-fg-subtle hover:text-fg',
            )}
          >
            {tab.label}
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
