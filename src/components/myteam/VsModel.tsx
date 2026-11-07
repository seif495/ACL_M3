import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiService, httpStatusOf, type MyTeamHistoryRow } from '../../services/api';
import { Card, DataTable, SectionHeading, StatCard, type Column } from '../ui';

interface Row {
  gameweek: number;
  you: number;
  model: number;
  average: number | null;
}

const signed = (n: number) => (n > 0 ? `+${n}` : n < 0 ? `−${-n}` : '0');

/** "You vs the model": your points after hits against the ML manager's since its start gameweek. */
export function VsModel({ history }: { history: MyTeamHistoryRow[] }) {
  const { data } = useQuery({
    queryKey: ['ml-manager'],
    queryFn: () => apiService.getMlManager(),
    staleTime: 5 * 60_000,
    // 404 = the ML manager hasn't started: the card stays hidden
    retry: (count, err) => httpStatusOf(err) !== 404 && count < 1,
  });

  const rows = useMemo<Row[]>(() => {
    if (!data) return [];
    const mine = new Map(history.filter((h) => h.points != null).map((h) => [h.gameweek, (h.points ?? 0) - h.transfer_cost]));
    return data.history
      .filter((m) => m.gameweek >= data.start_gameweek && mine.has(m.gameweek))
      .map((m) => ({ gameweek: m.gameweek, you: mine.get(m.gameweek) ?? 0, model: m.points, average: m.gw_average }));
  }, [data, history]);

  if (!data || rows.length === 0) return null;
  const you = rows.reduce((s, r) => s + r.you, 0);
  const model = rows.reduce((s, r) => s + r.model, 0);
  const diff = you - model;
  const wins = rows.filter((r) => r.you > r.model).length;

  const columns: Column<Row>[] = [
    { key: 'gameweek', header: 'GW', description: 'Gameweek', sortValue: (r) => r.gameweek, render: (r) => <span className="font-semibold tabular-nums">{r.gameweek}</span> },
    { key: 'you', header: 'You', description: 'Your points after hits', align: 'right', sortValue: (r) => r.you, render: (r) => <span className="font-semibold tabular-nums">{r.you}</span> },
    { key: 'model', header: 'Model', description: 'The ML manager’s points after hits', align: 'right', sortValue: (r) => r.model, render: (r) => <span className="tabular-nums">{r.model}</span> },
    { key: 'avg', header: 'Avg', description: 'Average manager’s score', align: 'right', hideBelow: 'sm', sortValue: (r) => r.average, render: (r) => <span className="tabular-nums text-fg-muted">{r.average ?? '–'}</span> },
    { key: 'diff', header: '+/−', description: 'You minus the model', align: 'right', sortValue: (r) => r.you - r.model, render: (r) => <span className="tabular-nums">{signed(r.you - r.model)}</span> },
  ];

  return (
    <section aria-labelledby="my-vs-model">
      <SectionHeading
        as="h2"
        size="md"
        id="my-vs-model"
        title="You vs the model"
        subtitle={`Points after hits since gameweek ${data.start_gameweek}, when the model started its own FPL team`}
      />
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <StatCard label="You" value={String(you)} detail={`${rows.length} gameweeks`} />
          <StatCard label="The model" value={String(model)} detail={`${data.model} ML manager`} />
          <StatCard
            label={diff === 0 ? 'Level' : diff > 0 ? 'You’re ahead' : 'Model’s ahead'}
            value={signed(diff)}
            detail={`You won ${wins} of ${rows.length} gameweeks`}
            highlight={diff > 0}
          />
        </div>
        <Card padded={false} className="min-w-0">
          <DataTable
            caption="Your points against the model's, per gameweek"
            columns={columns}
            rows={rows}
            rowKey={(r) => r.gameweek}
            initialSort={{ key: 'gameweek', direction: 'desc' }}
            dense
          />
        </Card>
      </div>
    </section>
  );
}
