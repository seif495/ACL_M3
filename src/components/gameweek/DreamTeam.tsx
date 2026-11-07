import { Card, Pitch, PitchPlayer, PitchRow, SectionHeading } from '../ui';
import { usePlayerDialog } from '../../hooks/usePlayerDialog';
import type { TeamOfWeekResponse } from '../../services/api';
import { PITCH_LINES } from '../totw/pitchLines';

/** The gameweek's Dream Team: the best XI by actual points (team of the week, actual mode). */
export function DreamTeam({ team }: { team: TeamOfWeekResponse }) {
  const dialog = usePlayerDialog();
  return (
    <Card>
      <SectionHeading
        title="Dream Team"
        subtitle={`Best XI by points, ${team.formation}. ${Math.round(team.total_points)} points in total.`}
        as="h3"
      />
      <Pitch label={`Dream team, gameweek ${team.gameweek}`}>
        {PITCH_LINES.map((line) => (
          <PitchRow key={line.position} label={line.label}>
            {team.starters
              .filter((p) => p.position === line.position)
              .map((p) => (
                <PitchPlayer
                  key={p.name}
                  name={p.name}
                  displayName={p.web_name ?? undefined}
                  team={p.team}
                  photo={p.photo}
                  captain={p.captain}
                  viceCaptain={p.vice_captain}
                  value={`${Math.round(p.points)} pts`}
                  onClick={
                    dialog
                      ? () => dialog.openPlayer({ name: p.name, team: p.team, position: p.position, photo: p.photo })
                      : undefined
                  }
                  actionLabel={`${p.name}: show stats`}
                />
              ))}
          </PitchRow>
        ))}
      </Pitch>
    </Card>
  );
}
