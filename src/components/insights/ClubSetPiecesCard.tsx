import { Card, ClubCrest } from '../ui';
import type { ClubSetPieces, SetPieceTaker } from '../../services/insightsApi';

function Takers({ title, items }: { title: string; items: SetPieceTaker[] }) {
  return (
    <div className="min-w-0">
      <h4 className="mb-1 text-micro font-semibold uppercase text-fg-muted">{title}</h4>
      {items.length === 0 ? (
        <p className="text-data text-fg-subtle">None listed</p>
      ) : (
        <ol className="space-y-1">
          {items.map((t) => (
            <li key={t.name} className="flex items-center gap-2 text-data">
              <span className="w-4 shrink-0 text-center font-bold tabular-nums text-fg-subtle">{t.order}</span>
              <span className="min-w-0 truncate font-semibold text-fg" title={t.name}>
                {t.web_name}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** One club's penalty, free-kick and corner takers, first choice first. */
export function ClubSetPiecesCard({ club, title = club.team, showCrest = true }: { club: ClubSetPieces; title?: string; showCrest?: boolean }) {
  return (
    <Card padded={false} className="min-w-0">
      <h3 className="flex items-center gap-2 border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">
        {showCrest && <ClubCrest team={club.team} size={22} />}
        {title}
      </h3>
      <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-3">
        <Takers title="Penalties" items={club.penalties} />
        <Takers title="Free kicks" items={club.free_kicks} />
        <Takers title="Corners" items={club.corners} />
      </div>
    </Card>
  );
}
