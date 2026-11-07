import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Loader2, Search, X } from 'lucide-react';
import { apiService } from '../../services/api';
import { loadSearchIndex } from '../../services/searchApi';
import { ClubCrest, PlayerAvatar, PositionBadge } from '../ui';
import { cn } from '../../utils/cn';
import { teamPath } from '../../utils/clubColors';
import { playerPath } from '../../utils/playerPath';
import { searchClubs, searchFixtures, searchPages } from './searchModel';

interface SearchResult {
  player_name: string;
  position?: string | null;
  team_name?: string | null;
  avatar?: string | null;
}

interface Item {
  key: string;
  group: 'Players' | 'Clubs' | 'Matches' | 'Pages';
  to: string;
  node: ReactNode;
}

const GROUPS: Item['group'][] = ['Players', 'Clubs', 'Matches', 'Pages'];

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

function kickoffLabel(iso: string | null, gameweek: number): string {
  if (!iso) return `Gameweek ${gameweek}`;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return `Gameweek ${gameweek}`;
  return `Gameweek ${gameweek} · ${d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}`;
}

interface PlayerSearchDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Header search: a modal combobox over players (typo-tolerant search API), clubs, this season's
 * fixtures and the site's pages. Arrow keys move through every group, Enter opens the result.
 */
export function PlayerSearchDialog({ open, onClose }: PlayerSearchDialogProps) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const listId = useId();
  const term = useDebounced(query.trim(), 150);
  const searching = term.length >= 2;

  const { data, isFetching, isError } = useQuery({
    queryKey: ['player-search', term],
    queryFn: ({ signal }) => apiService.searchPlayers(term, { limit: 6, signal }),
    enabled: open && searching,
    staleTime: 60_000,
  });
  const { data: index } = useQuery({
    queryKey: ['search-index'],
    queryFn: loadSearchIndex,
    enabled: open,
    staleTime: 5 * 60_000,
    retry: false,
  });

  const items = useMemo<Item[]>(() => {
    if (!searching) return [];
    const players = (data?.players ?? []) as SearchResult[];
    const clubs = index?.clubs ?? [];
    const out: Item[] = [];
    players.forEach((row, i) =>
      out.push({
        key: `player-${row.player_name}-${i}`,
        group: 'Players',
        to: playerPath(row.player_name),
        node: (
          <>
            <PlayerAvatar name={row.player_name} src={row.avatar} size={36} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-fg">{row.player_name}</span>
              {row.team_name && <ClubCrest team={row.team_name} size={14} label="full" className="text-data text-fg-subtle" />}
            </span>
            <PositionBadge position={row.position} />
          </>
        ),
      }),
    );
    searchClubs(clubs, term).forEach((club) =>
      out.push({
        key: `club-${club}`,
        group: 'Clubs',
        to: teamPath(club),
        node: (
          <>
            <ClubCrest team={club} size={32} />
            <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{club}</span>
            <span className="text-data text-fg-subtle">Club</span>
          </>
        ),
      }),
    );
    searchFixtures(clubs, index?.fixtures ?? [], term).forEach((f) =>
      out.push({
        key: `match-${f.id}`,
        group: 'Matches',
        to: f.to,
        node: (
          <>
            <span className="flex shrink-0 items-center -space-x-1">
              <ClubCrest team={f.home} size={28} />
              <ClubCrest team={f.away} size={28} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-fg">
                {f.home} v {f.away}
              </span>
              <span className="block truncate text-data text-fg-subtle">{kickoffLabel(f.kickoff, f.gameweek)}</span>
            </span>
          </>
        ),
      }),
    );
    searchPages(term).forEach((p) =>
      out.push({
        key: `page-${p.to}`,
        group: 'Pages',
        to: p.to,
        node: (
          <>
            <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{p.title}</span>
            <span className="text-data text-fg-subtle">{p.to}</span>
          </>
        ),
      }),
    );
    return out;
  }, [searching, data, index, term]);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setActive(0);
    const previous = document.activeElement as HTMLElement | null;
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    document.body.style.overflow = 'hidden';
    return () => {
      window.clearTimeout(id);
      document.body.style.overflow = '';
      previous?.focus?.();
    };
  }, [open]);

  useEffect(() => setActive(0), [term]);

  const activeKey = items[active]?.key;
  useEffect(() => {
    if (!activeKey) return;
    listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [activeKey]);

  if (!open) return null;

  const choose = (item: Item) => {
    onClose();
    navigate(item.to);
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'ArrowDown' && items.length) {
      e.preventDefault();
      setActive((i) => (i + 1) % items.length);
    } else if (e.key === 'ArrowUp' && items.length) {
      e.preventDefault();
      setActive((i) => (i - 1 + items.length) % items.length);
    } else if (e.key === 'Enter' && items[active]) {
      e.preventDefault();
      choose(items[active]);
    } else if (e.key === 'Tab') {
      // Only the input and the close button are focusable; keep focus inside.
      e.preventDefault();
    }
  };

  const optionId = (key: string) => `${listId}-${key}`;
  const indexOf = new Map(items.map((item, i) => [item.key, i]));

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-4 pt-[10vh] backdrop-blur-[2px]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
        className="w-full max-w-xl overflow-hidden rounded-lg border border-line bg-surface shadow-pop"
      >
        <div className="flex items-center gap-3 border-b border-line px-4">
          {isFetching ? (
            <Loader2 className="h-5 w-5 shrink-0 animate-spin text-fg-subtle" aria-hidden="true" />
          ) : (
            <Search className="h-5 w-5 shrink-0 text-fg-subtle" aria-hidden="true" />
          )}
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded={items.length > 0}
            aria-controls={listId}
            aria-activedescendant={items[active] ? optionId(items[active].key) : undefined}
            aria-autocomplete="list"
            aria-label="Search players, clubs, matches and pages"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search players, clubs, matches, pages"
            className="h-14 min-w-0 flex-1 bg-transparent text-body text-fg placeholder:text-fg-subtle focus:outline-none"
          />
          <button type="button" onClick={onClose} className="rounded-sm p-1 text-fg-subtle hover:text-fg" aria-label="Close search">
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        <div ref={listRef} id={listId} role="listbox" aria-label="Results" className="max-h-[60vh] overflow-y-auto py-1">
          {GROUPS.map((group) => {
            const rows = items.filter((item) => item.group === group);
            if (!rows.length) return null;
            const headingId = `${listId}-${group}`;
            return (
              <div key={group} role="group" aria-labelledby={headingId}>
                <div id={headingId} className="px-4 pb-1 pt-2 text-micro font-semibold uppercase tracking-wide text-fg-subtle">
                  {group}
                </div>
                {rows.map((item) => {
                  const i = indexOf.get(item.key) ?? 0;
                  return (
                    <div
                      key={item.key}
                      id={optionId(item.key)}
                      role="option"
                      aria-selected={i === active}
                      onMouseEnter={() => setActive(i)}
                      onClick={() => choose(item)}
                      className={cn('flex cursor-pointer items-center gap-3 px-4 py-2', i === active && 'bg-surface-2')}
                    >
                      {item.node}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>

        <div aria-live="polite" className="text-data text-fg-subtle">
          {!searching ? (
            <p className="px-4 py-3">Type at least two letters. Try a player, a club, “Arsenal v Hull” or “captain”.</p>
          ) : isError && items.length === 0 ? (
            <p className="px-4 py-3 text-danger">Search is unavailable right now.</p>
          ) : !isFetching && items.length === 0 ? (
            <p className="px-4 py-3">Nothing matches “{term}”.</p>
          ) : (
            <p className="sr-only">{items.length} {items.length === 1 ? 'result' : 'results'} found</p>
          )}
        </div>
      </div>
    </div>
  );
}
