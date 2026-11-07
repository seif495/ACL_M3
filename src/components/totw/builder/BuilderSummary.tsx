import { Lock } from 'lucide-react';
import { Card, ClubCrest, PlayerAvatar, PositionBadge, SectionHeading, StatCard } from '../../ui';
import { displayName } from '../../../utils/playerNames';
import type { BuildResponse, BuilderPlayer } from '../../../services/teamBuilderApi';
import { fmtDelta, fmtMoney, fmtOwnership } from './builderModel';

interface SummaryProps {
  team: BuildResponse;
  /** The default predicted team's points, for the comparison. */
  defaultPoints: number | null;
}

/** Points against the default team and the most-owned template, budget used, formation, captain. */
export function BuilderSummary({ team, defaultPoints }: SummaryProps) {
  const captain = team.starters.find((p) => p.captain);
  const vsDefault = defaultPoints != null ? team.total_points - defaultPoints : null;
  const vsTemplate = team.template ? team.total_points - team.template.total_points : null;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <StatCard
          label="Predicted points"
          value={team.total_points.toFixed(1)}
          detail={vsDefault != null ? `${fmtDelta(vsDefault)} vs default team` : 'Captain counted twice'}
          highlight
        />
        <StatCard
          label="Budget used"
          value={fmtMoney(team.budget_used)}
          detail={team.budget != null ? `of ${fmtMoney(team.budget)}` : undefined}
        />
        <StatCard label="Formation" value={team.formation} detail={`Max ${team.max_per_club} per club`} />
        <StatCard
          label="Captain"
          value={captain ? <span className="text-h3">{displayName(captain.name, captain.web_name)}</span> : '–'}
          detail={captain ? `${captain.points.toFixed(1)} pts predicted` : undefined}
        />
      </div>

      <Card className="space-y-1.5">
        <SectionHeading
          as="h2"
          size="md"
          className="mb-0"
          title="Most-owned template"
          subtitle="The most-owned player at each position (3 a club), arranged by the same optimiser. Not limited by budget."
        />
        {team.template ? (
          <>
            <p className="flex items-baseline gap-2">
              <span className="font-display text-h2 font-bold tabular-nums text-fg">{team.template.total_points.toFixed(1)}</span>
              <span className="text-data text-fg-muted">
                pts · {fmtMoney(team.template.total_cost)} · {team.template.formation}
              </span>
            </p>
            {vsTemplate != null && (
              <p className="text-data text-fg-muted">
                Your squad is <span className="font-semibold text-fg">{fmtDelta(vsTemplate)}</span> pts against it.
              </p>
            )}
            <details className="pt-1 text-data text-fg-muted">
              <summary className="cursor-pointer select-none font-semibold text-fg">Who’s in it</summary>
              <ul className="mt-2 space-y-1">
                {team.template.players.map((p) => (
                  <li key={p.name} className="flex items-center gap-2">
                    <PositionBadge position={p.position} />
                    <span className="min-w-0 flex-1 truncate">{p.name}</span>
                    <span className="tabular-nums text-fg-subtle">{p.ownership != null ? `${p.ownership.toFixed(1)}%` : ''}</span>
                    <span className="w-12 text-right tabular-nums">{p.points.toFixed(1)}</span>
                  </li>
                ))}
              </ul>
            </details>
          </>
        ) : (
          <p className="text-data text-fg-subtle">Ownership isn’t available yet, so there’s no template to compare with.</p>
        )}
      </Card>
    </div>
  );
}

interface BenchProps {
  bench: BuilderPlayer[];
  locked: string[];
  onSelect: (name: string) => void;
}

/** The bench in order, each row a button for the same Lock / Exclude actions as the pitch. */
export function BuilderBench({ bench, locked, onSelect }: BenchProps) {
  if (bench.length === 0) return null;
  return (
    <Card padded={false}>
      <div className="px-4 pt-4">
        <SectionHeading as="h2" size="md" title="Bench" subtitle="Tap a player to lock or exclude him" />
      </div>
      <ul>
        {bench.map((p) => {
          const owned = fmtOwnership(p.ownership);
          return (
            <li key={p.name} className="border-t border-line">
              <button
                type="button"
                onClick={() => onSelect(p.name)}
                aria-label={`${p.name}: lock, exclude or view`}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2"
              >
                <PlayerAvatar name={p.name} src={p.photo} size={32} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1 text-sm font-semibold text-fg">
                    <span className="truncate">{displayName(p.name, p.web_name)}</span>
                    {locked.includes(p.name) && (
                      <>
                        <Lock className="h-3 w-3 shrink-0 text-accent" aria-hidden="true" />
                        <span className="sr-only">(locked)</span>
                      </>
                    )}
                  </span>
                  <ClubCrest team={p.team} size={14} label="full" className="text-data text-fg-subtle" />
                </span>
                <PositionBadge position={p.position} />
                <span className="w-16 text-right text-data tabular-nums text-fg-muted">
                  <span className="block">{p.points.toFixed(1)} pts</span>
                  <span className="block text-micro text-fg-subtle">{owned ?? (p.cost != null ? `£${p.cost.toFixed(1)}m` : '')}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
