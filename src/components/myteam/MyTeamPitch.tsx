import { Card, ClubCrest, PlayerAvatar, PositionBadge, Pitch, PitchPlayer, PitchRow, SectionHeading, StatusBadge } from '../ui';
import type { MyTeamLineup, MyTeamPlayer } from '../../services/api';
import { usePlayerDialog } from '../../hooks/usePlayerDialog';
import { displayName } from '../../utils/playerNames';
import { PlayerNameButton } from '../players/PlayerNameButton';
import { PITCH_LINES } from '../totw/pitchLines';
import { money } from '../totw/mlManagerFormat';

const hint = (p: MyTeamPlayer) => ({
  name: p.name,
  team: p.team,
  position: p.position,
  photo: p.photo,
  status: p.status,
  news: p.news,
  chance: p.chance,
});

/** The suggested XI on the pitch: captain and vice marked, predicted points and opponent per player. */
export function MyTeamPitch({ squad, lineup }: { squad: MyTeamPlayer[]; lineup: MyTeamLineup }) {
  const dialog = usePlayerDialog();
  const byElement = new Map(squad.map((p) => [p.element, p]));
  const starters = lineup.starters.flatMap((e) => byElement.get(e) ?? []);

  return (
    <Pitch label={`Suggested team, formation ${lineup.formation}`}>
      {PITCH_LINES.map((line) => (
        <PitchRow key={line.position} label={line.label}>
          {starters
            .filter((p) => p.position === line.position)
            .map((p) => (
              <PitchPlayer
                key={p.element}
                name={p.name}
                // Name plates use FPL's short name, as on the other pitches
                displayName={p.web_name ?? undefined}
                team={p.team}
                photo={p.photo}
                opponents={p.opponents}
                captain={p.element === lineup.captain}
                viceCaptain={p.element === lineup.vice_captain}
                status={p.status}
                statusNote={p.news}
                value={p.predicted_points != null ? `${p.predicted_points.toFixed(1)} pts` : '–'}
                onClick={dialog ? () => dialog.openPlayer(hint(p)) : undefined}
                actionLabel={`${p.name}: show stats`}
              />
            ))}
        </PitchRow>
      ))}
    </Pitch>
  );
}

/** The four substitutes in the order they come on. */
export function MyTeamBench({ squad, lineup }: { squad: MyTeamPlayer[]; lineup: MyTeamLineup }) {
  const byElement = new Map(squad.map((p) => [p.element, p]));
  const bench = lineup.bench.flatMap((e) => byElement.get(e) ?? []);
  return (
    <Card padded={false}>
      <div className="px-4 pt-4">
        <SectionHeading as="h3" title="Bench" subtitle="In priority order: they come on for starters who don’t play" />
      </div>
      <ol>
        {bench.map((p, i) => (
          <li key={p.element} className="flex items-center gap-3 border-t border-line px-4 py-2.5">
            <span className="inline-flex h-6 w-7 shrink-0 items-center justify-center rounded-sm bg-surface-2 text-micro font-bold text-fg-muted">
              <span aria-hidden="true">{i === 0 ? 'GK' : i}</span>
              <span className="sr-only">{i === 0 ? 'Substitute goalkeeper' : `Substitute ${i}`}:</span>
            </span>
            <PlayerAvatar name={p.name} src={p.photo} size={32} />
            <span className="min-w-0 flex-1">
              <PlayerNameButton
                player={hint(p)}
                label={displayName(p.name, p.web_name)}
                className="block text-sm font-semibold text-fg"
              />
              <span className="flex flex-wrap items-center gap-1.5">
                <ClubCrest team={p.team} size={14} label="full" className="text-data text-fg-subtle" />
                <StatusBadge status={p.status} chance={p.chance} news={p.news} />
              </span>
            </span>
            <PositionBadge position={p.position} />
            <span className="w-14 shrink-0 text-right tabular-nums">
              <span className="block text-sm font-bold text-fg-muted">
                {p.predicted_points != null ? p.predicted_points.toFixed(1) : '–'}
                <span className="sr-only"> predicted points</span>
              </span>
              <span className="block text-micro text-fg-subtle">{money(p.now_cost)}</span>
            </span>
          </li>
        ))}
      </ol>
    </Card>
  );
}
