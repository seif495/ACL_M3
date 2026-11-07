import { Card, Chip, ClubCrest, SectionHeading } from '../ui';
import type { GameweekBonusTable } from '../../services/gameweekApi';
import { displayName } from '../../utils/playerNames';

/** Bonus points per match by BPS, 3/2/1 with FPL's tie rules. Provisional until FPL adds them. */
export function BonusTables({ tables }: { tables: GameweekBonusTable[] }) {
  return (
    <Card>
      <SectionHeading
        title="Bonus points"
        subtitle="Awarded by BPS, top three per match. Provisional until FPL confirms them after the day's last match."
        as="h3"
      />
      {tables.length === 0 ? (
        <p className="text-data text-fg-subtle">Bonus appears once a match has started.</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {tables.map((t) => (
            <div key={t.fixture_id} className="min-w-0 rounded-md border border-line bg-surface-2 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="flex min-w-0 items-center gap-1.5 text-data font-semibold text-fg">
                  <ClubCrest team={t.home_team} size={16} label="short" />
                  <span className="tabular-nums">
                    {t.home_score ?? '-'}-{t.away_score ?? '-'}
                  </span>
                  <ClubCrest team={t.away_team} size={16} label="short" />
                </p>
                <Chip tone={t.confirmed ? 'accent' : 'warning'}>{t.confirmed ? 'Confirmed' : 'Provisional'}</Chip>
              </div>
              <ol className="space-y-1">
                {t.players.map((p) => (
                  <li key={p.element} className="flex items-center justify-between gap-2 text-data">
                    <span className="min-w-0 truncate text-fg">{displayName(p.name, p.web_name)}</span>
                    <span className="shrink-0 text-fg-subtle">
                      <span className="tabular-nums">{p.bps}</span> BPS{' '}
                      <span className="font-bold tabular-nums text-accent">+{p.bonus}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
