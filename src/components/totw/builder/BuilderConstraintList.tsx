import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Ban, Lock, X } from 'lucide-react';
import { apiService } from '../../../services/api';
import { BUILDER_LIMITS } from '../../../services/teamBuilderApi';
import { useDebounced } from '../../../hooks/usePlayerFilters';
import { Button, Card, Chip, PositionBadge, SearchInput, SectionHeading } from '../../ui';
import type { Constraints } from './builderModel';

interface ChipListProps {
  title: string;
  empty: string;
  names: string[];
  tone: 'accent' | 'danger';
  icon: ReactNode;
  verb: string;
  labelFor: (name: string) => string;
  onRemove: (name: string) => void;
}

function ChipList({ title, empty, names, tone, icon, verb, labelFor, onRemove }: ChipListProps) {
  return (
    <div>
      <h3 className="mb-1.5 text-micro font-semibold uppercase text-fg-muted">
        {title} <span className="tabular-nums text-fg-subtle">({names.length})</span>
      </h3>
      {names.length === 0 ? (
        <p className="text-data text-fg-subtle">{empty}</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {names.map((name) => (
            <li key={name}>
              <Chip tone={tone} icon={icon} className="gap-1.5 py-1 pl-2 pr-1 text-data">
                {labelFor(name)}
                <button
                  type="button"
                  onClick={() => onRemove(name)}
                  aria-label={`Remove ${name} from the ${verb} list`}
                  className="inline-flex h-6 w-6 items-center justify-center rounded-sm hover:bg-surface-2 hover:text-fg"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </Chip>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface Props {
  draft: Constraints;
  labelFor: (name: string) => string;
  onLock: (name: string) => void;
  onExclude: (name: string) => void;
  onUnlock: (name: string) => void;
  onUnexclude: (name: string) => void;
}

/** The locked and excluded players as removable chips, and a search to add someone who isn't in the squad. */
export function BuilderConstraintList({ draft, labelFor, onLock, onExclude, onUnlock, onUnexclude }: Props) {
  const [query, setQuery] = useState('');
  const term = useDebounced(query.trim(), 250);
  const search = useQuery({
    queryKey: ['players', 'search', term.toLowerCase(), 8],
    queryFn: ({ signal }) => apiService.searchPlayers(term, { limit: 8, includeAvatars: false, signal }),
    enabled: term.length >= 2,
    staleTime: 5 * 60_000,
  });
  const results = term.length >= 2 ? (search.data?.players ?? []) : [];
  const lockFull = draft.locked.length >= BUILDER_LIMITS.maxLocked;

  const pick = (action: (name: string) => void, name: string) => {
    action(name);
    setQuery('');
  };

  return (
    <Card className="space-y-4">
      <SectionHeading
        as="h2"
        size="md"
        className="mb-0"
        title="Locked and excluded"
        subtitle="Locked players are always in the squad, excluded players never are."
      />
      <ChipList
        title="Locked"
        empty="Tap a player on the pitch to lock him."
        names={draft.locked}
        tone="accent"
        icon={<Lock className="h-3 w-3" aria-hidden="true" />}
        verb="locked"
        labelFor={labelFor}
        onRemove={onUnlock}
      />
      <ChipList
        title="Excluded"
        empty="Nobody is excluded."
        names={draft.excluded}
        tone="danger"
        icon={<Ban className="h-3 w-3" aria-hidden="true" />}
        verb="excluded"
        labelFor={labelFor}
        onRemove={onUnexclude}
      />

      <div>
        <SearchInput
          label="Find a player to lock or exclude"
          placeholder="Find a player to lock or exclude"
          value={query}
          onChange={setQuery}
          name="builder_player_search"
          autoComplete="off"
        />
        {term.length >= 2 && (
          <ul className="mt-2 divide-y divide-line overflow-hidden rounded-md border border-line" aria-label="Search results">
            {search.isLoading && <li className="px-3 py-2 text-data text-fg-subtle">Searching…</li>}
            {!search.isLoading && results.length === 0 && (
              <li className="px-3 py-2 text-data text-fg-subtle">No players found.</li>
            )}
            {results.map((p) => {
              const name = String(p.player_name ?? p.name ?? '');
              if (!name) return null;
              const isLocked = draft.locked.includes(name);
              const isExcluded = draft.excluded.includes(name);
              return (
                <li key={`${name}-${p.team_name ?? ''}`} className="flex items-center gap-2 px-3 py-2">
                  <PositionBadge position={p.position} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-fg">{name}</span>
                    {p.team_name && <span className="block truncate text-data text-fg-subtle">{p.team_name}</span>}
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={isLocked || lockFull}
                    onClick={() => pick(onLock, name)}
                    aria-label={`Lock ${name}`}
                  >
                    Lock
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={isExcluded}
                    onClick={() => pick(onExclude, name)}
                    aria-label={`Exclude ${name}`}
                  >
                    Exclude
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
        <p className="mt-1.5 text-micro text-fg-subtle">
          Only players with a prediction for the gameweek can be locked; the builder says so if you pick someone else.
        </p>
      </div>
    </Card>
  );
}
