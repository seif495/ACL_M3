import { useMemo } from 'react';
import { Card, DataTable, EmptyState, SectionHeading, type Column } from '../ui';
import type { MlManagerHistoryRow, MyTeamHistoryRow } from '../../services/api';
import { CHIP_NAMES } from '../totw/mlManagerFormat';
import { PointsChart } from '../totw/MlManagerChart';
import { bigNumber } from './format';

const net = (r: MyTeamHistoryRow) => (r.points ?? 0) - r.transfer_cost;

/** Points per gameweek (after hits) against the average, as the ML manager's chart, plus a table. */
export function SeasonSoFar({ history }: { history: MyTeamHistoryRow[] }) {
  // The ML manager's chart takes its own row shape; only gameweek, points, hits and average are drawn.
  const chartRows = useMemo<MlManagerHistoryRow[]>(
    () =>
      history.map((r) => ({
        gameweek: r.gameweek,
        points: net(r),
        gross: r.points,
        hit_cost: r.transfer_cost,
        chip: r.chip,
        captain: null,
        transfers: 0,
        total: 0,
        team_value: 0,
        bank: 0,
        gw_average: r.average,
        gw_highest: null,
        league_rank: null,
        league_size: null,
        percentile: null,
      })),
    [history],
  );

  const columns: Column<MyTeamHistoryRow>[] = [
    { key: 'gameweek', header: 'GW', description: 'Gameweek', sortValue: (r) => r.gameweek, render: (r) => <span className="font-semibold tabular-nums">{r.gameweek}</span> },
    {
      key: 'points',
      header: 'Pts',
      description: 'Points after any transfer cost',
      align: 'right',
      sortValue: net,
      render: (r) => (
        <span className="tabular-nums">
          <span className="font-semibold">{net(r)}</span>
          {r.transfer_cost > 0 && <span className="text-fg-subtle"> (−{r.transfer_cost})</span>}
        </span>
      ),
    },
    { key: 'average', header: 'Avg', description: 'Average manager’s score', align: 'right', sortValue: (r) => r.average, render: (r) => <span className="tabular-nums text-fg-muted">{r.average ?? '–'}</span> },
    {
      key: 'diff',
      header: '+/−',
      description: 'Points against the average',
      align: 'right',
      sortValue: (r) => (r.average == null ? null : net(r) - r.average),
      render: (r) => {
        if (r.average == null) return '–';
        const d = net(r) - r.average;
        return <span className="tabular-nums">{d > 0 ? `+${d}` : d < 0 ? `−${-d}` : '0'}</span>;
      },
    },
    { key: 'rank', header: 'Rank', description: 'Overall rank after the gameweek', align: 'right', hideBelow: 'sm', sortValue: (r) => r.overall_rank, render: (r) => <span className="tabular-nums text-fg-muted">{bigNumber(r.overall_rank)}</span> },
    { key: 'chip', header: 'Chip', hideBelow: 'sm', sortValue: (r) => r.chip ?? '', render: (r) => (r.chip ? CHIP_NAMES[r.chip] : '') },
  ];

  return (
    <section aria-labelledby="my-season">
      <SectionHeading as="h2" size="md" id="my-season" title="Season so far" subtitle="Your points each gameweek against the average manager’s, hits included" />
      {history.length === 0 ? (
        <Card>
          <EmptyState title="No gameweeks played yet" description="Your points appear here after your first gameweek." className="py-6" />
        </Card>
      ) : (
        <div className="space-y-4">
          <Card>
            <PointsChart history={chartRows} />
          </Card>
          <DataTable
            caption="Your points per gameweek"
            columns={columns}
            rows={history}
            rowKey={(r) => r.gameweek}
            initialSort={{ key: 'gameweek', direction: 'desc' }}
            dense
          />
        </div>
      )}
    </section>
  );
}
