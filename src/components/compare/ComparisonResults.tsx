import { Fragment } from 'react';
import { ArrowRight, Check, RotateCcw } from 'lucide-react';
import { Button, Card, Chip, ClubCrest, FdrBadge, PlayerAvatar, PositionBadge, SectionHeading } from '../ui';
import { cn } from '../../utils/cn';
import { getClubAbbreviation } from '../../utils/clubLogos';
import type { TickerClub } from '../../services/api';
import { GroupedBars, Radar } from './CompareCharts';
import {
  bestIndices,
  buildRows,
  leadCounts,
  nameOf,
  playerColor,
  playerTint,
  type ComparedPlayer,
  type MetricRow,
  type Section,
} from './compareModel';

const SECTION_TITLES: Record<Section, string> = {
  season: 'Season stats',
  rates: 'Underlying rates',
  outlook: 'Looking ahead',
};

function PlayerCard({ player, index, season, onOpenDetails }: { player: ComparedPlayer; index: number; season: string; onOpenDetails: () => void }) {
  const p = player.profile;
  const stats: [string, string | number][] = [
    ['Points', p.totals?.total_points ?? 0],
    ['Games', p.appearances],
  ];
  return (
    <Card padded={false} className="flex min-w-0 flex-col overflow-hidden">
      <div aria-hidden="true" className="h-1" style={{ background: playerColor(index) }} />
      <div className="flex flex-1 flex-col items-start gap-3 p-3 sm:p-4">
        <PlayerAvatar name={p.name} src={p.photo ?? undefined} size={64} loading="eager" />
        <div className="min-w-0 max-w-full">
          <p className="text-micro font-semibold uppercase" style={{ color: playerColor(index) }}>
            Player {index + 1}
          </p>
          <h2 className="truncate font-display text-h3 font-bold text-fg" title={p.name}>
            {p.name}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-data text-fg-muted">
            {p.team && <ClubCrest team={p.team} size={18} label="full" className="max-w-full" />}
            <PositionBadge position={p.position ?? undefined} />
            <Chip>{season || 'All seasons'}</Chip>
          </div>
        </div>
      </div>
      <dl className="grid grid-cols-2 divide-x divide-line border-t border-line">
        {stats.map(([label, value]) => (
          <div key={label} className="px-3 py-2">
            <dt className="text-micro font-semibold uppercase text-fg-subtle">{label}</dt>
            <dd className="font-display text-h3 font-bold tabular-nums text-fg">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="border-t border-line px-1.5 py-1.5">
        <Button variant="ghost" size="sm" onClick={onOpenDetails} aria-label={`View details for ${p.name}`}>
          View details
          <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      </div>
    </Card>
  );
}

function FixtureChips({ club }: { club: TickerClub | null }) {
  if (!club) return <span className="text-fg-subtle">–</span>;
  const weeks = club.gameweeks.slice(0, 5);
  return (
    <ul className="flex flex-wrap justify-end gap-1">
      {weeks.map((w) =>
        w.fixtures.length === 0 ? (
          <li key={w.gameweek} className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-micro font-bold text-fg-subtle" title={`GW${w.gameweek}: no fixture`}>
            –
          </li>
        ) : (
          w.fixtures.map((f) => (
            <li key={f.fixture_id}>
              <FdrBadge difficulty={f.difficulty ?? 3} className="whitespace-nowrap">
                <span aria-hidden="true">
                  {getClubAbbreviation(f.opponent)} ({f.home ? 'H' : 'A'})
                </span>
                <span className="sr-only">
                  GW{w.gameweek}: {f.opponent} {f.home ? 'at home' : 'away'}, difficulty {f.difficulty ?? 'unknown'}
                </span>
              </FdrBadge>
            </li>
          ))
        ),
      )}
    </ul>
  );
}

function ValueCell({ row, i, best }: { row: MetricRow; i: number; best: boolean }) {
  const v = row.values[i];
  if (v == null) return <span className="text-fg-subtle">–</span>;
  return (
    <span
      className={cn('inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 tabular-nums', best ? 'font-bold' : 'font-medium text-fg')}
      style={best ? { color: playerColor(i), background: playerTint(i, 0.15) } : undefined}
    >
      {best && <Check aria-hidden="true" className="h-3.5 w-3.5" />}
      {row.format(v)}
      {best && <span className="sr-only"> (best)</span>}
    </span>
  );
}

interface ComparisonResultsProps {
  players: ComparedPlayer[];
  season: string;
  current: boolean;
  onOpenDetails: (player: ComparedPlayer) => void;
  onChangePlayers: () => void;
}

export function ComparisonResults({ players, season, current, onOpenDetails, onChangePlayers }: ComparisonResultsProps) {
  const n = players.length;
  const names = players.map(nameOf);
  const rows = buildRows(players, current);
  const wins = leadCounts(rows, n);
  const chartPlayers = names.map((name) => ({ name }));
  const sections = (['season', 'rates', 'outlook'] as Section[]).filter((s) => rows.some((r) => r.section === s));
  const hasFixtures = current && players.some((p) => p.ticker);

  const total = (p: ComparedPlayer, k: string) => {
    const v = p.profile.totals?.[k];
    return v == null ? null : Number(v);
  };
  const bars = [
    { label: 'Points', values: players.map((p) => total(p, 'total_points')) },
    { label: 'Goals', values: players.map((p) => total(p, 'goals')) },
    { label: 'Assists', values: players.map((p) => total(p, 'assists')) },
    { label: 'Bonus', values: players.map((p) => total(p, 'bonus')) },
    { label: 'Clean sheets', values: players.map((p) => total(p, 'clean_sheets')) },
  ];
  const radarAxes = [
    { label: 'Points', max: 300, values: bars[0].values },
    { label: 'Goals', max: 30, values: bars[1].values },
    { label: 'Assists', max: 20, values: bars[2].values },
    { label: 'Bonus', max: 50, values: bars[3].values },
    { label: 'Clean sheets', max: 20, values: bars[4].values },
    { label: 'Games', max: 38, values: players.map((p) => p.profile.appearances) },
  ];

  const cardGrid =
    n === 2 ? 'grid-cols-2' : n === 3 ? 'grid-cols-1 sm:grid-cols-3' : 'grid-cols-2 lg:grid-cols-4';

  return (
    <div className="space-y-4">
      <div className={cn('grid gap-3 sm:gap-4', cardGrid)}>
        {players.map((p, i) => (
          <PlayerCard key={p.profile.name} player={p} index={i} season={season} onOpenDetails={() => onOpenDetails(p)} />
        ))}
      </div>

      <Card padded={false}>
        <div className="px-4 pt-4 sm:px-5">
          <SectionHeading
            as="h2"
            size="md"
            title="Head to head"
            subtitle={
              <>
                Best value in each row is ticked. Price is shown but not scored; for fixtures, lower difficulty is better.{' '}
                <span className="text-fg-muted">{names.map((nm, i) => `${nm} leads ${wins[i]}`).join(', ')}.</span>
              </>
            }
          />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-max border-collapse text-data">
            <caption className="sr-only">Stat-by-stat comparison of {names.join(', ')}</caption>
            <thead>
              <tr className="border-y border-line bg-surface-2 text-micro uppercase">
                <th scope="col" className="sticky left-0 z-[1] bg-surface-2 px-3 py-2 text-left font-semibold text-fg-subtle sm:px-5">
                  Stat
                </th>
                {names.map((nm, i) => (
                  <th key={nm} scope="col" className="min-w-[6.5rem] px-3 py-2 text-right font-semibold" style={{ color: playerColor(i) }}>
                    <span className="block max-w-[9rem] truncate" title={nm}>
                      {nm}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sections.map((s) => (
                <Fragment key={s}>
                  <tr className="bg-surface-2/40">
                    <th scope="colgroup" colSpan={n + 1} className="sticky left-0 px-3 py-1.5 text-left text-micro font-semibold uppercase text-fg-subtle sm:px-5">
                      {SECTION_TITLES[s]}
                    </th>
                  </tr>
                  {rows
                    .filter((r) => r.section === s)
                    .map((row) => {
                      const best = bestIndices(row);
                      return (
                        <tr key={row.key} className="border-b border-line hover:bg-surface-2/50">
                          <th scope="row" className="sticky left-0 z-[1] whitespace-nowrap bg-surface px-3 py-2 text-left font-normal text-fg-muted sm:px-5">
                            {row.label}
                          </th>
                          {players.map((p, i) => (
                            <td key={p.profile.name} className="px-3 py-2 text-right">
                              <ValueCell row={row} i={i} best={best.includes(i)} />
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  {s === 'outlook' && hasFixtures && (
                    <tr className="border-b border-line hover:bg-surface-2/50">
                      <th scope="row" className="sticky left-0 z-[1] whitespace-nowrap bg-surface px-3 py-2 text-left font-normal text-fg-muted sm:px-5">
                        Next 5 fixtures
                      </th>
                      {players.map((p) => (
                        <td key={p.profile.name} className="px-3 py-2 text-right align-top">
                          <div className="ml-auto w-max max-w-[11rem]">
                            <FixtureChips club={p.ticker} />
                          </div>
                        </td>
                      ))}
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionHeading as="h2" size="md" title="Totals" subtitle="Each stat has its own scale, so bars compare players within a stat" />
          <GroupedBars categories={bars} players={chartPlayers} />
        </Card>

        <Card>
          <SectionHeading
            as="h2"
            size="md"
            title="Profile"
            subtitle="Normalised: 100 = 300 pts, 30 goals, 20 assists, 50 bonus, 20 clean sheets, 38 games"
          />
          <Radar axes={radarAxes} players={chartPlayers} />
        </Card>
      </div>

      <div className="flex justify-center">
        <Button variant="secondary" onClick={onChangePlayers} icon={<RotateCcw className="h-4 w-4" aria-hidden="true" />}>
          Change players
        </Button>
      </div>
    </div>
  );
}
