import type { ReactNode } from 'react';
import { Card, EmptyState, SectionHeading } from '../ui';
import type { GameweekPlayer } from '../../services/gameweekApi';
import { PlayerLine } from './PlayerLine';

/** A titled card listing players, each with a value (points by default). */
export function PlayersCard({
  title,
  subtitle,
  players,
  empty = 'Nothing to show yet.',
  value = (p) => p.points ?? 0,
  valueLabel = () => 'pts',
  detail,
  action,
}: {
  title: string;
  subtitle?: ReactNode;
  players: GameweekPlayer[];
  empty?: string;
  value?: (p: GameweekPlayer) => ReactNode;
  valueLabel?: (p: GameweekPlayer) => ReactNode;
  detail?: (p: GameweekPlayer) => ReactNode;
  action?: ReactNode;
}) {
  return (
    <Card>
      <SectionHeading title={title} subtitle={subtitle} action={action} as="h3" />
      {players.length === 0 ? (
        <EmptyState title={empty} className="py-6" />
      ) : (
        <ul>
          {players.map((p) => (
            <PlayerLine
              key={p.element}
              name={p.name}
              webName={p.web_name}
              team={p.team}
              position={p.position}
              photo={p.photo}
              detail={detail?.(p)}
              value={value(p)}
              valueLabel={valueLabel(p)}
            />
          ))}
        </ul>
      )}
    </Card>
  );
}
