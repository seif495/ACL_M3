import { Pitch, PitchPlayer, PitchRow } from '../ui';
import type { TeamOfWeekResponse } from '../../services/api';
import { ShareImageButton } from '../share/ShareImage';
import { useExportPhoto } from '../share/exportPhoto';
import { PITCH_LINES } from './pitchLines';

// "Share image" for the Predicted and Dream team tabs of the Team of the Week page. Self-contained:
// it draws its own copy of the pitch (no click handlers, photos through the image proxy) and is
// rendered only while an image is being made.

const points = (p: number, predicted: boolean) => (predicted ? p.toFixed(1) : String(Math.round(p)));

function SharedPitch({ team }: { team: TeamOfWeekResponse }) {
  const photoOf = useExportPhoto();
  const predicted = team.mode === 'predicted';
  return (
    <Pitch label={`${predicted ? 'Predicted' : 'Dream'} team, gameweek ${team.gameweek}`}>
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
                photo={photoOf(p.photo)}
                opponents={p.opponents}
                captain={p.captain}
                viceCaptain={p.vice_captain}
                status={predicted ? p.status : null}
                value={`${points(p.points, predicted)} pts`}
              />
            ))}
        </PitchRow>
      ))}
    </Pitch>
  );
}

/** A "Share image" button for the pitch of the Team of the Week's predicted or Dream team tab. */
export function TeamOfWeekShare({ team, className }: { team: TeamOfWeekResponse; className?: string }) {
  const predicted = team.mode === 'predicted';
  const total = predicted ? `${team.total_points.toFixed(1)} predicted points` : `${Math.round(team.total_points)} points`;
  const subtitle = `${team.formation} · ${total}${predicted ? ', captain counted twice' : ''}`;
  return (
    <ShareImageButton
      title={predicted ? `Predicted team, GW${team.gameweek}` : `Dream team, GW${team.gameweek}`}
      eyebrow={predicted ? 'Team of the Week' : 'Team of the Week · best by actual points'}
      subtitle={subtitle}
      fileName={predicted ? `pitch-me-team-of-the-week-gw${team.gameweek}` : `pitch-me-dream-team-gw${team.gameweek}`}
      shareText={
        predicted
          ? `The XI our model expects to score most in gameweek ${team.gameweek} (${total}).`
          : `The best XI of gameweek ${team.gameweek} by actual FPL points (${total}).`
      }
      url={predicted ? '/team-of-the-week' : `/team-of-the-week?mode=actual&gw=${team.gameweek}`}
      className={className}
    >
      <SharedPitch team={team} />
    </ShareImageButton>
  );
}
