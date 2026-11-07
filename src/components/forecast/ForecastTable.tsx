import { Card, DataTable, type Column } from '../ui';
import { DataDisclosure } from '../explain/DataDisclosure';
import { TeamLink } from '../teams/MatchBits';
import { cn } from '../../utils/cn';
import type { ForecastRow, TableForecastResponse } from '../../services/forecastApi';
import { formatChance, spokenChance } from './format';
import { PositionBar } from './PositionBar';
import { ZONE_BAR, ZONE_LABEL, zone } from './zones';

type Tone = 'good' | 'bad';

/** A chance as text: bold once it is more likely than not, quiet when it is nil. */
function Chance({ p, tone }: { p: number; tone: Tone }) {
  const likely = p >= 0.5;
  return (
    <span
      className={cn(
        'tabular-nums',
        p <= 0 ? 'text-fg-subtle' : likely ? (tone === 'good' ? 'font-bold text-accent' : 'font-bold text-danger') : 'text-fg-muted',
      )}
    >
      <span aria-hidden="true">{formatChance(p)}</span>
      <span className="sr-only">{spokenChance(p)}</span>
    </span>
  );
}

function signed(v: number): string {
  const text = Math.abs(v).toFixed(2);
  return Number(text) === 0 ? text : `${v > 0 ? '+' : '−'}${text}`;
}

function percentCell(p: number): string {
  return p < 0.005 ? '·' : String(Math.round(p * 100));
}

function columns(total: number): Column<ForecastRow>[] {
  return [
    {
      key: 'predicted_position',
      header: 'Pos',
      description: 'Predicted final position, by expected points',
      align: 'center',
      className: 'w-12',
      render: (r) => {
        const z = zone(r.predicted_position, total);
        return (
          <span className="relative inline-flex w-6 justify-center font-semibold">
            {z && <span aria-hidden="true" className={cn('absolute -left-3 top-1/2 h-6 w-1 -translate-y-1/2 rounded-full', ZONE_BAR[z])} />}
            {r.predicted_position}
            {z && <span className="sr-only">, {ZONE_LABEL[z]}</span>}
          </span>
        );
      },
    },
    { key: 'team', header: 'Club', sticky: true, render: (r) => <TeamLink team={r.team} short /> },
    {
      key: 'current_position',
      header: 'Now',
      description: 'Position in the table today',
      align: 'right',
      hideBelow: 'sm',
      render: (r) => r.current_position ?? '–',
    },
    { key: 'points', header: 'Pts', description: 'Points now', align: 'right', hideBelow: 'sm' },
    {
      key: 'expected_points',
      header: 'xPts',
      description: 'Expected points at the end of the season',
      align: 'right',
      render: (r) => <span className="font-bold tabular-nums text-fg">{r.expected_points.toFixed(1)}</span>,
    },
    {
      key: 'expected_position',
      header: 'xPos',
      description: 'Expected final position (the average over the simulations)',
      align: 'right',
      hideBelow: 'md',
      render: (r) => <span className="tabular-nums">{r.expected_position.toFixed(1)}</span>,
    },
    { key: 'p_title', header: 'Title', description: 'Chance of winning the league', align: 'right', render: (r) => <Chance p={r.p_title} tone="good" /> },
    { key: 'p_top4', header: 'Top 4', description: 'Chance of a Champions League place (top four)', align: 'right', render: (r) => <Chance p={r.p_top4} tone="good" /> },
    {
      key: 'p_europe',
      header: 'Europe',
      description: 'Chance of finishing 5th or 6th (Europa or Conference League)',
      align: 'right',
      hideBelow: 'md',
      render: (r) => <Chance p={r.p_europe} tone="good" />,
    },
    { key: 'p_relegation', header: 'Down', description: 'Chance of relegation (bottom three)', align: 'right', render: (r) => <Chance p={r.p_relegation} tone="bad" /> },
    {
      key: 'finish',
      header: 'Finish',
      description: 'Chance of each final position, 1st to last',
      hideBelow: 'lg',
      render: (r) => <PositionBar probs={r.position_probabilities} />,
    },
  ];
}

/** The predicted final table with its method note and, behind disclosures, the numbers. */
export function ForecastTable({ data, loading }: { data?: TableForecastResponse; loading?: boolean }) {
  const rows = data?.rows ?? [];
  const total = rows.length || 20;
  return (
    <>
      <DataTable
        caption="Predicted final table"
        columns={columns(total)}
        rows={rows}
        rowKey={(r) => r.team}
        loading={loading}
        skeletonRows={20}
        empty="There are no fixtures to predict yet."
      />
      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-data text-fg-subtle" aria-label="Table zones">
        <li className="flex items-center gap-2"><span aria-hidden="true" className="h-3 w-1 rounded-full bg-accent" />Champions League places</li>
        <li className="flex items-center gap-2"><span aria-hidden="true" className="h-3 w-1 rounded-full bg-accent-2" />Europa / Conference League places</li>
        <li className="flex items-center gap-2"><span aria-hidden="true" className="h-3 w-1 rounded-full bg-danger" />Relegation</li>
      </ul>
      {data && (
        <Card className="mt-5">
          <p className="text-data text-fg-muted">
            Simulated {data.simulations.toLocaleString('en-GB')} times from a team-strength model fitted to this season&rsquo;s results.
          </p>
          <p className="mt-1 text-data text-fg-subtle">
            {data.played_matches} matches played, {data.remaining_matches} to go. Recent matches count more
            {data.model.prior_season ? ', and last season’s form still carries weight early on' : ''}. xPts and xPos are
            averages over the simulations; the percentages are the share of simulations. It uses results only, no bookmakers&rsquo; prices.
          </p>
          <DataDisclosure summary="Show the numbers: chance of each final position">
            <table className="w-full text-left text-data">
              <caption className="sr-only">Chance of finishing in each position, per cent; a dot means under half a per cent</caption>
              <thead className="text-micro uppercase text-fg-subtle">
                <tr>
                  <th scope="col" className="py-1.5 pr-3 font-semibold">Club</th>
                  {rows.map((_, i) => (
                    <th key={i} scope="col" className="px-1.5 py-1.5 text-right font-semibold">{i + 1}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.team} className="border-t border-line">
                    <th scope="row" className="whitespace-nowrap py-1 pr-3 font-normal text-fg">{r.team}</th>
                    {r.position_probabilities.map((p, i) => (
                      <td key={i} className={cn('px-1.5 py-1 text-right tabular-nums', p >= 0.25 ? 'font-bold text-fg' : 'text-fg-muted')}>{percentCell(p)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </DataDisclosure>
          <DataDisclosure summary="Show the numbers: team ratings" className="mt-1">
            <table className="w-full text-left text-data">
              <caption className="sr-only">Team ratings from the model, strongest first</caption>
              <thead className="text-micro uppercase text-fg-subtle">
                <tr>
                  <th scope="col" className="py-1.5 pr-3 font-semibold">Club</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-semibold">Attack</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-semibold">Defence</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-semibold">Strength</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-semibold">Goal diff / match</th>
                </tr>
              </thead>
              <tbody>
                {data.ratings.map((r) => (
                  <tr key={r.team} className="border-t border-line">
                    <th scope="row" className="whitespace-nowrap py-1 pr-3 font-normal text-fg">
                      {r.team}
                      {r.promoted && <span className="text-fg-subtle"> · promoted</span>}
                    </th>
                    <td className="py-1 pr-3 text-right tabular-nums text-fg-muted">{signed(r.attack)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums text-fg-muted">{signed(r.defence)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums text-fg">{signed(r.strength)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums text-fg-muted">{signed(r.goal_difference)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-micro text-fg-subtle">
              Attack and defence are on a log scale where 0 is the league average and higher is better. Goal diff is the expected goal
              difference against an average side on neutral ground. Home sides score about {data.model.home_goals.toFixed(2)} a match and
              away sides {data.model.away_goals.toFixed(2)}.
            </p>
          </DataDisclosure>
        </Card>
      )}
    </>
  );
}
