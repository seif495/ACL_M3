import type { CSSProperties, KeyboardEvent, MutableRefObject } from 'react';
import { CornerDownLeft, Search, X } from 'lucide-react';
import { PlayerAvatar, PositionBadge } from '../ui';
import { cn } from '../../utils/cn';

export type PlayerSuggestion = { name: string; position?: string; season?: string; avatar?: string };

interface PlayerComboboxProps {
  /** Prefix for element ids (`${id}-input`, `${id}-suggestion-${i}`). */
  id: string;
  listId: string;
  label: string;
  /** The slot's colour (CSS colour), shown as a dot and on focus. */
  color: string;
  /** Adds a remove button next to the label. */
  onRemove?: () => void;
  value: string;
  placeholder?: string;
  disabled?: boolean;
  name?: string;
  suggestions: PlayerSuggestion[];
  activeIndex: number;
  avatarByName: Record<string, string | null | undefined>;
  inputRef: MutableRefObject<HTMLInputElement | null>;
  onChange: (value: string) => void;
  onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => void;
  onPick: (suggestion: PlayerSuggestion) => void;
  /** Validation message shown under the input (the input is marked invalid). */
  error?: string | null;
}

/**
 * Presentational ARIA combobox for picking a player. The page owns the search
 * state and keyboard handling; this only renders the input and its listbox.
 */
export function PlayerCombobox({
  id,
  listId,
  label,
  color,
  onRemove,
  value,
  placeholder,
  disabled,
  name,
  suggestions,
  activeIndex,
  avatarByName,
  inputRef,
  onChange,
  onKeyDown,
  onPick,
  error,
}: PlayerComboboxProps) {
  const inputId = `${id}-input`;
  const errorId = `${id}-error`;
  const open = suggestions.length > 0;

  return (
    <div className="relative flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={inputId} className="flex items-center gap-2 text-micro font-semibold uppercase text-fg-muted">
          <span aria-hidden="true" className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
          {label}
        </label>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove ${label}`}
            className="-my-1 inline-flex h-6 w-6 items-center justify-center rounded-sm text-fg-subtle transition-colors hover:text-fg"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>
      <div className="relative">
        <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" />
        <input
          ref={(el) => (inputRef.current = el)}
          id={inputId}
          name={name}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          aria-autocomplete="list"
          aria-controls={listId}
          aria-expanded={open}
          aria-activedescendant={activeIndex >= 0 ? `${id}-suggestion-${activeIndex}` : undefined}
          role="combobox"
          autoComplete="off"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          style={{ '--slot-color': color } as CSSProperties}
          className={cn(
            'h-11 w-full rounded-md border border-line-strong bg-surface-2 pl-9 pr-3 text-sm text-fg placeholder:text-fg-subtle',
            'transition-colors hover:border-fg-subtle focus:border-[var(--slot-color)] disabled:cursor-not-allowed disabled:opacity-50',
            error && 'border-danger hover:border-danger',
          )}
        />
      </div>
      {error && (
        <p id={errorId} role="alert" className="text-micro font-semibold text-danger">
          {error}
        </p>
      )}
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={`${label} suggestions`}
          className="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-auto rounded-md border border-line-strong bg-surface-2 py-1 shadow-pop"
        >
          {suggestions.map((s, i) => {
            const active = i === activeIndex;
            return (
              <li
                id={`${id}-suggestion-${i}`}
                role="option"
                aria-selected={active}
                key={s.name + i}
                onClick={() => onPick(s)}
                className={cn(
                  'flex cursor-pointer items-center justify-between gap-2 px-3 py-2 text-data text-fg transition-colors',
                  active ? 'bg-brand-2' : 'hover:bg-brand-2/60',
                )}
              >
                <span className="flex min-w-0 items-center gap-3">
                  <PlayerAvatar name={s.name} src={s.avatar ?? avatarByName[s.name]} size={28} />
                  <span className="min-w-0">
                    <span className="block truncate font-semibold">{s.name}</span>
                    {s.season && <span className="block text-micro text-fg-subtle">{s.season}</span>}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <PositionBadge position={s.position} />
                  {active && <CornerDownLeft aria-hidden="true" className="h-3.5 w-3.5 text-fg-subtle" />}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
