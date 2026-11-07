import { Sparkles } from 'lucide-react';
import type { PlayerProfile } from '../../services/api';
import { Chip, ClubCrest, PlayerAvatar, PositionBadge, StatCard } from '../ui';
import { ShareImageButton } from '../share/ShareImage';
import { useExportPhoto } from '../share/exportPhoto';
import { playerPath, playerSlug } from '../../utils/playerPath';
import { ALL_SEASONS } from './seasons';

// "Share image" for a player's profile: the summary block only (who, the season's points and the
// six key numbers), drawn off-screen in a compact layout, not the whole long page.

const fmt = (v: number | null | undefined) => (v == null ? '–' : Math.round(v).toLocaleString());

function Summary({ profile, photo }: { profile: PlayerProfile; photo?: string | null }) {
  const photoOf = useExportPhoto();
  const t = profile.totals;
  const isGk = profile.position === 'GK';
  const isDefensive = isGk || profile.position === 'DEF';
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-4">
        <PlayerAvatar name={profile.name} src={photoOf(profile.photo ?? photo)} size={80} loading="eager" className="border border-brand-2 bg-surface" />
        <div className="min-w-0 flex-1">
          <h4 className="font-display text-h2 font-extrabold leading-tight text-fg">{profile.name}</h4>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-data text-fg-muted">
            {profile.team && <ClubCrest team={profile.team} size={20} label="full" className="font-semibold text-fg" />}
            <PositionBadge position={profile.position ?? undefined} />
          </div>
          {profile.predicted_points != null && (
            <Chip tone="accent" icon={<Sparkles className="h-3 w-3" aria-hidden="true" />} className="mt-2">
              Next GW: {profile.predicted_points.toFixed(1)} pts
            </Chip>
          )}
        </div>
        <div className="shrink-0 rounded-md border border-brand-2 bg-bg/30 px-3 py-2 text-right">
          <p className="text-micro font-semibold uppercase text-fg-subtle">FPL points</p>
          <p className="font-display text-display font-extrabold leading-none tabular-nums text-accent">{fmt(t.total_points)}</p>
          <p className="mt-1 text-micro text-fg-muted">
            {profile.points_per_game != null ? `${profile.points_per_game.toFixed(2)} per match` : 'No matches played'}
          </p>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Minutes" value={fmt(t.minutes)} detail={`${profile.appearances} apps`} />
        <StatCard label="Goals" value={fmt(t.goals)} detail={t.xg != null ? `${t.xg.toFixed(1)} xG` : undefined} />
        <StatCard label="Assists" value={fmt(t.assists)} detail={t.xa != null ? `${t.xa.toFixed(1)} xA` : undefined} />
        <StatCard label="Clean sheets" value={fmt(t.clean_sheets)} detail={isDefensive ? `${fmt(t.goals_conceded)} conceded` : undefined} />
        {isGk ? (
          <StatCard label="Saves" value={fmt(t.saves)} />
        ) : (
          <StatCard label="Def. contributions" value={fmt(t.defensive_contribution)} />
        )}
        <StatCard label="Bonus" value={fmt(t.bonus)} detail={`${fmt(t.bps)} BPS`} />
      </div>
    </div>
  );
}

/** A "Share image" button for a loaded profile with at least one match. */
export function PlayerShare({ profile, photo, className }: { profile: PlayerProfile; photo?: string | null; className?: string }) {
  const career = profile.season === ALL_SEASONS || profile.season.toLowerCase().startsWith('all');
  return (
    <ShareImageButton
      title={profile.name}
      eyebrow={career ? 'All seasons' : profile.season}
      subtitle={[profile.team, `${fmt(profile.totals.total_points)} FPL points`].filter(Boolean).join(' · ')}
      fileName={`pitch-me-${playerSlug(profile.name) || 'player'}`}
      shareText={`${profile.name}${career ? '' : `, ${profile.season}`}: ${fmt(profile.totals.total_points)} FPL points.`}
      url={playerPath(profile.name) + (career ? '' : `?season=${encodeURIComponent(profile.season)}`)}
      width={480}
      className={className}
    >
      <Summary profile={profile} photo={photo} />
    </ShareImageButton>
  );
}
