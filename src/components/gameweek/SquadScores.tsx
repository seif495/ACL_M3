import type { ReactNode } from 'react';
import { Card, Chip, SectionHeading } from '../ui';
import type { GameweekManager, GameweekModelSquad, GameweekSquadPlayer } from '../../services/gameweekApi';
import { PlayerLine } from './PlayerLine';

function Versus({ points, average, highest }: { points: number; average: number | null; highest: number | null }) {
  const diff = average != null ? points - average : null;
  return (
    <div className="mb-3 flex flex-wrap items-end gap-x-6 gap-y-1">
      <div>
        <p className="font-display text-display font-bold leading-none text-gradient tabular-nums">{points}</p>
        <p className="text-micro uppercase text-fg-subtle">points</p>
      </div>
      <dl className="flex gap-5 text-data">
        {average != null && (
          <div>
            <dt className="text-micro uppercase text-fg-subtle">FPL average</dt>
            <dd className="font-semibold tabular-nums text-fg">
              {average}
              {diff != null && (
                <span className={diff >= 0 ? 'ml-1.5 text-accent' : 'ml-1.5 text-danger'}>
                  {diff >= 0 ? '+' : ''}
                  {diff}
                </span>
              )}
            </dd>
          </div>
        )}
        {highest != null && (
          <div>
            <dt className="text-micro uppercase text-fg-subtle">Highest</dt>
            <dd className="font-semibold tabular-nums text-fg">{highest}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

function Counted({ players }: { players: GameweekSquadPlayer[] }) {
  return (
    <ul>
      {players.map((p) => {
        const mult = p.multiplier ?? 1;
        const tags = [p.captain ? 'C' : p.vice_captain ? 'VC' : null, p.subbed_in ? 'came on' : null].filter(Boolean);
        return (
          <PlayerLine
            key={p.name}
            name={p.name}
            webName={p.web_name}
            team={p.team}
            position={p.position}
            photo={p.photo}
            detail={tags.join(' · ') || undefined}
            value={p.points == null ? '-' : p.points * (mult || 1)}
            valueLabel={mult > 1 ? `${p.points} x ${mult}` : p.minutes === 0 ? 'did not play' : 'pts'}
          />
        );
      })}
    </ul>
  );
}

export function ModelSquadCard({ squad }: { squad: GameweekModelSquad }) {
  return (
    <Card>
      <SectionHeading
        title="The model's squad"
        subtitle={`Picked before the deadline, scored like FPL (${squad.formation}). Predicted ${squad.predicted_points.toFixed(1)}.`}
        as="h3"
        action={squad.live ? <Chip tone="warning">Live</Chip> : undefined}
      />
      <Versus points={squad.points} average={squad.fpl_average} highest={squad.fpl_highest} />
      <Counted players={squad.starters} />
      <p className="mt-2 text-data text-fg-subtle">Bench: {squad.bench_points} points</p>
    </Card>
  );
}

export function ManagerCard({ manager }: { manager: GameweekManager }) {
  const rank = manager.league?.rank;
  const lines: ReactNode[] = [];
  if (manager.chip) lines.push(<Chip key="chip" tone="brand">{manager.chip.replace(/_/g, ' ')}</Chip>);
  if (manager.hit_cost) lines.push(<Chip key="hit" tone="danger">-{manager.hit_cost} hit</Chip>);
  return (
    <Card>
      <SectionHeading
        title="The ML manager"
        subtitle={manager.captain ? `Captain ${manager.captain}. Season total ${manager.total ?? '-'}.` : undefined}
        as="h3"
        action={manager.live ? <Chip tone="warning">Live</Chip> : undefined}
      />
      <Versus points={manager.points} average={manager.gw_average} highest={manager.gw_highest} />
      <div className="mb-2 flex flex-wrap items-center gap-2 text-data text-fg-muted">
        {lines}
        {rank != null && manager.league?.size != null && (
          <span>
            Rank {rank.toLocaleString()} of {manager.league.size.toLocaleString()} in {manager.league.name ?? 'its league'}
          </span>
        )}
        {manager.transfers.length > 0 && (
          <span>{manager.transfers.map((t) => `${t.out ?? '?'} to ${t.in ?? '?'}`).join(', ')}</span>
        )}
        {!manager.transfers.length && manager.transfer_count ? <span>{manager.transfer_count} transfers</span> : null}
      </div>
      {manager.players.length > 0 && <Counted players={manager.players.slice(0, 6)} />}
    </Card>
  );
}
