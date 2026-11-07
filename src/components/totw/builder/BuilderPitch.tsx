import { Lock } from 'lucide-react';
import { Pitch, PitchPlayer, PitchRow } from '../../ui';
import type { BuilderPlayer } from '../../../services/teamBuilderApi';
import { PITCH_LINES } from '../pitchLines';

interface Props {
  starters: BuilderPlayer[];
  gameweek: number;
  locked: string[];
  selected: string | null;
  onSelect: (name: string) => void;
}

/** The built XI. Every player is a button: tapping one opens the Lock / Exclude / profile actions. */
export function BuilderPitch({ starters, gameweek, locked, selected, onSelect }: Props) {
  return (
    <Pitch label={`Built team, gameweek ${gameweek}`}>
      {PITCH_LINES.map((line) => (
        <PitchRow key={line.position} label={line.label}>
          {starters
            .filter((p) => p.position === line.position)
            .map((p) => (
              <PitchPlayer
                key={p.name}
                name={p.name}
                displayName={p.web_name ?? undefined}
                team={p.team}
                photo={p.photo}
                opponents={p.opponents}
                captain={p.captain}
                viceCaptain={p.vice_captain}
                status={p.status}
                statusNote={p.news}
                value={`${p.points.toFixed(1)} pts`}
                detail={p.ownership != null ? `${p.ownership.toFixed(1)}%` : undefined}
                marker={
                  locked.includes(p.name) ? (
                    <span className="flex h-4 w-4 items-center justify-center rounded-full bg-accent text-accent-on ring-1 ring-white/70">
                      <Lock className="h-2.5 w-2.5" aria-hidden="true" />
                      <span className="sr-only">Locked</span>
                    </span>
                  ) : undefined
                }
                onClick={() => onSelect(p.name)}
                actionLabel={`${p.name}${selected === p.name ? ' (selected)' : ''}: lock, exclude or view`}
              />
            ))}
        </PitchRow>
      ))}
    </Pitch>
  );
}
