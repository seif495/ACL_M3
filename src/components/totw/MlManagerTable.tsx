import type { MlManagerResultPlayer, MlManagerSquadPlayer } from '../../services/api';
import { cn } from '../../utils/cn';
import { displayName } from '../../utils/playerNames';
import { PlayerNameButton } from '../players/PlayerNameButton';
import { ClubCrest, DataTable, PlayerAvatar, PositionBadge, type Column } from '../ui';
import { signed } from '../explain/format';
import { ArmbandTag, Missing, SubMarker } from './ModelReview';
import type { Lineup } from './mlManagerFormat';

// The ML manager's 15 players for a gameweek being played or scored, laid out like the Model vs
// actual tab's table: the XI after auto-subs, then the bench, with minutes so far, the model's
// prediction and the points (both multiplied for whoever wore the armband).

interface Row {
  player: MlManagerSquadPlayer;
  result: MlManagerResultPlayer | undefined;
  /** Not counted: on the bench after auto-subs (all 15 count with Bench Boost). */
  bench: boolean;
}

/** The armband's multiplier as counted; bench players and everyone else at face value. */
const shownMultiplier = (r: Row) => Math.max(1, r.result?.multiplier ?? 1);

function multiplierText(points: number, multiplier: number) {
  return multiplier === 2 ? `${points} doubled` : `${points} times ${multiplier}`;
}

function diffTone(diff: number, bench: boolean) {
  if (bench || Math.abs(diff) < 0.05) return 'text-fg-muted';
  return diff > 0 ? 'text-accent' : 'text-danger';
}

interface MlManagerTableProps {
  gameweek: number;
  lineup: Lineup;
  /** Bench Boost: the bench counts too. */
  benchBoost: boolean;
  /** Points still coming in. */
  live: boolean;
  updating?: boolean;
}

export function MlManagerTable({ gameweek, lineup, benchBoost, live, updating = false }: MlManagerTableProps) {
  const rows: Row[] = [
    ...lineup.xi.map((player) => ({ player, result: lineup.results.get(player.element), bench: false })),
    ...lineup.bench.map((player) => ({ player, result: lineup.results.get(player.element), bench: !benchBoost })),
  ];
  const yetToPlay = live ? 'Yet to play' : 'No points recorded';

  const columns: Column<Row>[] = [
    {
      key: 'player',
      header: 'Player',
      sticky: true,
      render: ({ player: p, result, bench }) => {
        const replaced = lineup.cameOnFor.get(p.element);
        const replacement = lineup.replacedBy.get(p.element);
        const minutes = result?.minutes;
        return (
          <span className="flex items-center gap-2">
            <PlayerAvatar name={p.name} src={p.photo} size={28} />
            <span className="min-w-0">
              <span className="flex items-center gap-1.5">
                <PlayerNameButton
                  player={{ name: p.name, team: p.team, position: p.position, photo: p.photo }}
                  label={displayName(p.name, p.web_name)}
                  className={cn('max-w-[7.5rem] truncate font-semibold sm:max-w-[14rem]', bench && 'text-fg-muted')}
                />
                <ArmbandTag player={p} />
                {replaced && <SubMarker direction="in" label={`Came off the bench for ${replaced.name}`} />}
                {replacement && <SubMarker direction="out" label={`Didn’t play, replaced by ${replacement.name}`} />}
              </span>
              <span className="flex items-center gap-1.5 text-micro text-fg-subtle">
                <ClubCrest team={p.team} size={14} label="short" />
                <PositionBadge position={p.position} className="w-auto px-1 sm:hidden" />
                {/* Phones have no Min column: the minutes go here. */}
                {minutes != null && (
                  <span className="tabular-nums sm:hidden">
                    {minutes}
                    <span aria-hidden="true">′</span>
                    <span className="sr-only"> minutes</span>
                  </span>
                )}
                {lineup.bench.includes(p) && <span>Bench</span>}
              </span>
            </span>
          </span>
        );
      },
    },
    {
      key: 'position',
      header: 'Pos',
      description: 'Position',
      hideBelow: 'sm',
      render: ({ player }) => <PositionBadge position={player.position} />,
    },
    {
      key: 'minutes',
      header: 'Min',
      description: live ? 'Minutes played so far' : 'Minutes played',
      align: 'right',
      hideBelow: 'sm',
      render: ({ result, bench }) =>
        result?.minutes == null ? (
          <Missing label={yetToPlay} />
        ) : (
          <span className={cn('tabular-nums', bench || result.minutes === 0 ? 'text-fg-subtle' : 'text-fg-muted')}>
            {result.minutes}
            <span className="sr-only"> minutes</span>
          </span>
        ),
    },
    {
      key: 'predicted',
      header: 'Pred',
      description: 'Predicted points (multiplied for the captain)',
      align: 'right',
      render: (r) =>
        r.player.predicted_points == null ? (
          <Missing label="No prediction" />
        ) : (
          <span className={cn('tabular-nums', r.bench ? 'text-fg-subtle' : 'text-fg-muted')}>
            {(r.player.predicted_points * shownMultiplier(r)).toFixed(1)}
          </span>
        ),
    },
    {
      key: 'points',
      header: 'Pts',
      description: live
        ? 'Points so far (multiplied for the captain; bench points don’t count)'
        : 'Points (multiplied for the captain; bench points don’t count)',
      align: 'right',
      render: (r) => {
        const points = r.result?.points;
        if (points == null) return <Missing label={yetToPlay} />;
        const m = shownMultiplier(r);
        return (
          <span className={cn('font-bold tabular-nums', r.bench ? 'text-fg-muted' : 'text-fg')}>
            {points * m}
            {m > 1 && (
              <span aria-hidden="true" className="ml-1 text-micro font-semibold text-fg-subtle">
                ×{m}
              </span>
            )}
            <span className="sr-only">{m > 1 ? ` (${multiplierText(points, m)})` : r.bench ? ' (bench, not counted)' : ''}</span>
          </span>
        );
      },
    },
    {
      key: 'diff',
      header: '+/−',
      description: 'Points minus predicted',
      align: 'right',
      render: (r) => {
        const points = r.result?.points;
        if (points == null || r.player.predicted_points == null) return <Missing label="Not known yet" />;
        const diff = (points - r.player.predicted_points) * shownMultiplier(r);
        return <span className={cn('font-semibold tabular-nums', diffTone(diff, r.bench))}>{signed(diff, 1)}</span>;
      },
    },
  ];

  return (
    <DataTable
      caption={`Gameweek ${gameweek}: the ML manager’s 15 players${live ? ' so far' : ''}, with minutes, predicted and actual points; the XI after automatic substitutions first, then the bench`}
      columns={columns}
      rows={rows}
      rowKey={(r) => r.player.element}
      // A heavier line where the bench starts.
      rowClassName={(_, i) => (i === lineup.xi.length ? 'border-t-2 border-t-line-strong' : undefined)}
      dense
      className={cn('transition-opacity', updating && 'opacity-60')}
    />
  );
}
