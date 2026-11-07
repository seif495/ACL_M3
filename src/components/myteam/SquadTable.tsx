import { DataTable, ClubCrest, PositionBadge, StatusBadge, type Column } from '../ui';
import type { MyTeamPlayer } from '../../services/api';
import { displayName } from '../../utils/playerNames';
import { PlayerNameButton } from '../players/PlayerNameButton';
import { money } from '../totw/mlManagerFormat';

const POSITION_ORDER = { GK: 0, DEF: 1, MID: 2, FWD: 3 } as const;
const pts = (v: number | null | undefined) => (v == null ? '–' : v.toFixed(1));

/** All 15 players: prices, next-gameweek prediction, each projected week and the total. */
export function SquadTable({ squad, captain, vice }: { squad: MyTeamPlayer[]; captain: number | null; vice: number | null }) {
  const weeks = squad.find((p) => p.projected.length > 0)?.projected.map((w) => w.gameweek) ?? [];

  const columns: Column<MyTeamPlayer>[] = [
    {
      key: 'name',
      header: 'Player',
      sticky: true,
      sortValue: (p) => displayName(p.name, p.web_name),
      render: (p) => (
        <span className="block min-w-[7rem]">
          <PlayerNameButton
            player={{ name: p.name, team: p.team, position: p.position, photo: p.photo, status: p.status, news: p.news, chance: p.chance }}
            label={displayName(p.name, p.web_name)}
            className="block text-sm font-semibold text-fg"
          />
          <span className="flex flex-wrap items-center gap-1">
            {p.element === captain && <span className="text-micro font-semibold text-fg-muted">Captain</span>}
            {p.element !== captain && p.element === vice && <span className="text-micro font-semibold text-fg-muted">Vice</span>}
            <StatusBadge status={p.status} chance={p.chance} news={p.news} />
          </span>
        </span>
      ),
    },
    {
      key: 'position',
      header: 'Pos',
      description: 'Position',
      sortValue: (p) => POSITION_ORDER[p.position],
      render: (p) => <PositionBadge position={p.position} />,
    },
    {
      key: 'team',
      header: 'Club',
      hideBelow: 'sm',
      sortValue: (p) => p.team,
      render: (p) => <ClubCrest team={p.team} size={18} label="short" />,
    },
    {
      key: 'now_cost',
      header: 'Price',
      description: 'Price now',
      align: 'right',
      sortValue: (p) => p.now_cost,
      render: (p) => <span className="tabular-nums">{money(p.now_cost)}</span>,
    },
    {
      key: 'selling_price',
      header: 'Sell',
      description: 'Selling price',
      align: 'right',
      hideBelow: 'sm',
      sortValue: (p) => p.selling_price,
      render: (p) => <span className="tabular-nums">{p.selling_price != null ? money(p.selling_price) : '–'}</span>,
    },
    {
      key: 'next',
      header: 'Next',
      description: 'Predicted points next gameweek',
      align: 'right',
      sortValue: (p) => p.predicted_points,
      render: (p) => <span className="font-semibold tabular-nums">{pts(p.predicted_points)}</span>,
    },
    ...weeks.map<Column<MyTeamPlayer>>((gw) => ({
      key: `gw${gw}`,
      header: `GW${gw}`,
      description: `Projected points, gameweek ${gw}`,
      align: 'right',
      hideBelow: 'md',
      sortValue: (p) => p.projected.find((w) => w.gameweek === gw)?.points,
      render: (p) => <span className="tabular-nums text-fg-muted">{pts(p.projected.find((w) => w.gameweek === gw)?.points)}</span>,
    })),
    {
      key: 'total',
      header: 'Total',
      description: 'Projected points over the weeks shown',
      align: 'right',
      sortValue: (p) => p.projected_total,
      render: (p) => <span className="font-semibold tabular-nums">{pts(p.projected_total)}</span>,
    },
  ];

  return (
    <DataTable
      caption="Your squad with prices and projected points"
      columns={columns}
      rows={squad}
      rowKey={(p) => p.element}
      initialSort={{ key: 'position', direction: 'asc' }}
      dense
    />
  );
}
