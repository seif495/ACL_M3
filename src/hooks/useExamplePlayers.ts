import { useQuery } from '@tanstack/react-query';
import { apiService, type PlayerLeader } from '../services/api';
import { shortName } from '../utils/playerNames';

export interface ExamplePlayer {
  /** Full name: what a search or comparison looks up. */
  name: string;
  /** Short label for a chip ("Haaland", "Evanilson"). */
  label: string;
}

const toExample = (p: PlayerLeader): ExamplePlayer => ({ name: p.name, label: shortName(p.name, p.web_name) });

/**
 * Example players for the search and compare chips, taken from this season's leaders so they
 * are always in the league (hard-coded names went stale as players left). Empty until loaded.
 */
export function useExamplePlayers(enabled = true) {
  const { data } = useQuery({
    // Same request as the Players page's default leaders table would make with limit 40
    queryKey: ['player-leaders', 'examples'],
    queryFn: ({ signal }) => apiService.getPlayerLeaders({ limit: 40 }, signal),
    staleTime: 30 * 60_000,
    enabled,
  });
  const players = data?.players ?? [];
  const top = players.slice(0, 5).map(toExample);
  // The two best of each outfield position, for "X vs Y" matchups
  const pairs = (['FWD', 'MID', 'DEF'] as const)
    .map((pos) => players.filter((p) => p.position === pos).slice(0, 2))
    .filter((two) => two.length === 2)
    .map(([a, b]) => ({ p1: toExample(a), p2: toExample(b) }));
  return { top, pairs };
}
