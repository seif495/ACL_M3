import { ClubCrest, PlayerAvatar } from '../ui';
import { ShareImageButton } from '../share/ShareImage';
import { useExportPhoto } from '../share/exportPhoto';
import { getClubAbbreviation } from '../../utils/clubLogos';

// "Share image" for the Predictions page's top 10: name, club, opponent and predicted points in a
// compact list, drawn off-screen (not a screenshot of the big table).

export interface TopTenItem {
  name: string;
  /** Short name for display (FPL's web name), when known. */
  label: string;
  team?: string | null;
  photo?: string | null;
  opponent?: { team: string; home: boolean } | null;
  points: number;
}

function List({ items, pointsLabel }: { items: TopTenItem[]; pointsLabel: string }) {
  const photoOf = useExportPhoto();
  return (
    <ol className="divide-y divide-line rounded-md border border-line bg-bg/40">
      {items.map((it, i) => (
        <li key={it.name} className="flex items-center gap-3 px-3 py-2">
          <span className="w-5 text-center text-data font-semibold text-fg-subtle">{i + 1}</span>
          <PlayerAvatar name={it.name} src={photoOf(it.photo)} size={36} loading="eager" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-fg">{it.label}</span>
            {it.team && <ClubCrest team={it.team} size={14} label="full" className="text-micro text-fg-subtle" />}
          </span>
          <span className="inline-flex w-16 items-center gap-1 text-data text-fg-muted">
            {it.opponent ? (
              <>
                <ClubCrest team={it.opponent.team} size={16} />
                <span className="font-semibold text-fg">{getClubAbbreviation(it.opponent.team) || it.opponent.team.slice(0, 3).toUpperCase()}</span>
                <span>({it.opponent.home ? 'H' : 'A'})</span>
              </>
            ) : (
              <span className="text-fg-subtle">–</span>
            )}
          </span>
          <span className="w-12 text-right font-display text-h3 font-bold tabular-nums text-accent">
            {it.points.toFixed(1)}
            <span className="sr-only"> {pointsLabel}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

interface TopTenShareProps {
  items: TopTenItem[];
  gameweek?: number;
  /** Position wording, e.g. "midfielders"; omit for all players. */
  positionLabel?: string;
  /** More than one gameweek: the points are the projected total. */
  weeks?: number;
  className?: string;
}

/** A "Share image" button for the ten highest predicted scorers of the current selection. */
export function TopTenShare({ items, gameweek, positionLabel, weeks = 1, className }: TopTenShareProps) {
  const top = [...items].sort((a, b) => b.points - a.points).slice(0, 10);
  if (!top.length) return null;
  const scope = positionLabel && positionLabel !== 'all players' ? ` ${positionLabel}` : '';
  const title = weeks > 1 ? `Top 10${scope}, next ${weeks} gameweeks` : `Top 10${scope}${gameweek ? `, GW${gameweek}` : ''}`;
  return (
    <ShareImageButton
      title={title}
      eyebrow="Predicted points"
      subtitle={weeks > 1 ? 'Projected points over the coming gameweeks' : 'Expected FPL points next gameweek'}
      fileName={`pitch-me-top-10${gameweek && weeks === 1 ? `-gw${gameweek}` : ''}`}
      shareText={`Who our model expects to score most${gameweek ? ` in gameweek ${gameweek}` : ''}.`}
      url="/predictions"
      className={className}
    >
      <List items={top} pointsLabel={weeks > 1 ? 'projected points' : 'predicted points'} />
    </ShareImageButton>
  );
}
