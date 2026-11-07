import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { apiService } from '../../services/api';
import { usePlayerAvatars } from '../../hooks/usePlayerAvatars';
import { PlayerCombobox, type PlayerSuggestion } from './PlayerCombobox';

const SEARCH_DEBOUNCE_MS = 200;

interface PlayerSlotProps {
  /** Unique per slot: element ids derive from it. */
  id: string;
  label: string;
  color: string;
  value: string;
  placeholder?: string;
  disabled?: boolean;
  error?: string | null;
  onChange: (value: string) => void;
  onRemove?: () => void;
}

/** One player picker: the input plus its own debounced search-as-you-type suggestions. */
export function PlayerSlot({ id, label, color, value, placeholder, disabled, error, onChange, onRemove }: PlayerSlotProps) {
  const queryClient = useQueryClient();
  const [suggestions, setSuggestions] = useState<PlayerSuggestion[]>([]);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Search only after the user typed here (not when a name was set by a pick, the URL or an example)
  const typed = useRef(false);
  // Bumped on every change so out-of-order search responses are dropped
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    if (!typed.current || value.trim().length < 2) {
      setSuggestions([]);
      setActiveIndex(-1);
      return;
    }
    const term = value.trim();
    const timer = window.setTimeout(async () => {
      try {
        const res = await queryClient.fetchQuery({
          queryKey: ['players', 'search', term.toLowerCase(), 10],
          queryFn: ({ signal }) => apiService.searchPlayers(term, { limit: 10, includeAvatars: false, signal }),
          staleTime: 5 * 60 * 1000,
        });
        if (mine !== seq.current || !typed.current) return;
        const found = (res.players || [])
          .map((p) => ({
            name: p.player_name || p.name || '',
            position: p.position || p.pos || undefined,
            season: p.season || undefined,
            avatar: p.avatar || p.image || p.photo || undefined,
          }))
          .filter((x: PlayerSuggestion) => x.name);
        setSuggestions(found.slice(0, 10));
        setActiveIndex(-1);
      } catch {
        if (mine === seq.current) setSuggestions([]);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [value, queryClient]);

  const avatarByName = usePlayerAvatars(suggestions.filter((s) => s.name && !s.avatar).map((s) => s.name));

  const pick = (s: PlayerSuggestion) => {
    typed.current = false;
    seq.current++;
    onChange(s.name);
    setSuggestions([]);
    setActiveIndex(-1);
    inputRef.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (suggestions.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      if (activeIndex >= 0 && activeIndex < suggestions.length) {
        e.preventDefault();
        pick(suggestions[activeIndex]);
      }
    } else if (e.key === 'Escape') {
      setSuggestions([]);
      setActiveIndex(-1);
    }
  };

  return (
    <PlayerCombobox
      id={id}
      listId={`${id}-suggestions-list`}
      label={label}
      color={color}
      name={`${id}_search`}
      error={error}
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      suggestions={suggestions}
      activeIndex={activeIndex}
      avatarByName={avatarByName}
      inputRef={inputRef}
      onChange={(v) => {
        typed.current = true;
        onChange(v);
      }}
      onKeyDown={onKeyDown}
      onPick={pick}
      onRemove={onRemove}
    />
  );
}
