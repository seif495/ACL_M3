import { useId, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { apiService, httpStatusOf, handleApiError, type MyTeamPlan, type MyTeamPlayer, type MyTeamChip } from '../../services/api';
import { Button, Card, Chip, EmptyState, ErrorState, Field, SectionHeading, Select, StatCard } from '../ui';
import { cn } from '../../utils/cn';
import { displayName } from '../../utils/playerNames';
import { CHIP_NAMES, money, plural } from '../totw/mlManagerFormat';
import { TransferRow } from '../totw/MlManagerPanels';
import { errorCopy } from './format';

const HIT_OPTIONS = [
  { value: 0, label: 'Free transfers only' },
  { value: 1, label: 'Allow 1 hit (−4)' },
  { value: 2, label: 'Allow 2 hits (−8)' },
];

function planError(error: unknown): { title: string; description: string } {
  const status = httpStatusOf(error);
  if (status === 503) return { title: 'The planner isn’t ready', description: handleApiError(error) };
  if (status === 422) return { title: 'No valid plan found', description: handleApiError(error) };
  return errorCopy(error);
}

const signedPoints = (v: number) => (v > 0 ? `+${v.toFixed(1)}` : v < 0 ? `−${Math.abs(v).toFixed(1)}` : '0.0');

function PlanResult({ plan, squad }: { plan: MyTeamPlan; squad: MyTeamPlayer[] }) {
  const photos = new Map<number, string | null>(squad.map((p) => [p.element, p.photo]));
  for (const p of plan.squad) if (p.photo) photos.set(p.element, p.photo);
  const captain = plan.squad.find((p) => p.captain);
  const vice = plan.squad.find((p) => p.vice_captain);
  const net = plan.projected_points - plan.hit_cost;
  const gain = net - plan.current_projected;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Projected points"
          value={net.toFixed(1)}
          detail={plan.hit_cost > 0 ? `After the −${plan.hit_cost} hit` : `GW${plan.gameweek}, captain counted twice`}
          highlight
        />
        <StatCard
          label="Your team now"
          value={plan.current_projected.toFixed(1)}
          detail={plan.transfers.length > 0 ? `${signedPoints(gain)} with these moves` : 'Best XI as it stands'}
        />
        <StatCard label="Bank after" value={money(plan.bank_after)} detail={`Was ${money(plan.bank_before)}`} />
        <StatCard
          label="Captain"
          value={captain ? <span className="text-h3">{displayName(captain.name, captain.web_name)}</span> : '–'}
          detail={vice ? `Vice: ${displayName(vice.name, vice.web_name)}` : undefined}
        />
      </div>

      <Card padded={false} className="[container-type:inline-size]">
        <div className="px-4 pt-4">
          <SectionHeading
            as="h3"
            title={plan.transfers.length > 0 ? `Suggested moves for GW${plan.gameweek}` : 'Keep your team'}
            subtitle={
              plan.transfers.length > 0
                ? `${plural(plan.transfers.length, 'transfer')}${plan.hits > 0 ? `, ${plural(plan.hits, 'hit')} (−${plan.hit_cost} points)` : ', no points deducted'}`
                : 'No transfer beats keeping the squad you have'
            }
            action={plan.chip ? <Chip tone="info">{CHIP_NAMES[plan.chip]}</Chip> : undefined}
          />
        </div>
        {plan.transfers.length > 0 ? (
          <ol aria-label="Suggested transfers">
            {plan.transfers.map((t) => (
              <TransferRow
                key={`${t.out.element}-${t.in.element}`}
                transfer={{
                  out: { ...t.out, photo: photos.get(t.out.element) ?? null },
                  in: { ...t.in, photo: photos.get(t.in.element) ?? null },
                }}
              />
            ))}
          </ol>
        ) : (
          <p className="border-t border-line px-4 py-3 text-data text-fg-muted">
            Keep your team{plan.free_transfers > 0 ? ` and save your ${plural(plan.free_transfers, 'free transfer')}.` : '.'}
          </p>
        )}
        {plan.reason && (
          <p className="border-t border-line px-4 py-3 text-data text-fg">
            <span className="font-semibold text-fg-muted">Why: </span>
            {plan.reason}
          </p>
        )}
        {plan.horizon.length > 1 && (
          <div className="border-t border-line px-4 py-3">
            <p className="text-micro font-semibold uppercase text-fg-subtle">Projected points ahead</p>
            <ul className="mt-1.5 flex flex-wrap gap-1.5">
              {plan.horizon.map((h) => (
                <li key={h.gameweek}>
                  <Chip tone={h.gameweek === plan.gameweek ? 'brand' : 'neutral'} className="tabular-nums">
                    GW{h.gameweek} · {h.projected.toFixed(1)}
                    {h.transfers > 0 && ` · ${plural(h.transfers, 'move')}`}
                  </Chip>
                </li>
              ))}
            </ul>
          </div>
        )}
        {!plan.optimal && (
          <p className="border-t border-line px-4 py-3 text-data text-fg-subtle">
            The planner ran out of time before proving this is the best plan, so it may not be the very best one.
          </p>
        )}
      </Card>
    </div>
  );
}

/** Options, a button and the planner's answer. Key it by team so a new team starts clean. */
export function TransferPlanner({
  teamId,
  squad,
  chips,
  freeTransfers,
}: {
  teamId: number;
  squad: MyTeamPlayer[];
  chips: MyTeamChip[];
  freeTransfers: number;
}) {
  const hasChip = chips.some((c) => c.available && c.used_gameweek == null);
  const [free, setFree] = useState(Math.min(Math.max(freeTransfers, 0), 5));
  const [hits, setHits] = useState(0);
  const [useChips, setUseChips] = useState(false);
  const [horizon, setHorizon] = useState(4);
  const chipId = useId();

  const plan = useMutation({
    mutationFn: () => apiService.planMyTeam(teamId, { free_transfers: free, max_hits: hits, horizon, chips: hasChip && useChips }),
  });

  return (
    <section aria-labelledby="my-planner" className="space-y-4">
      <SectionHeading
        as="h2"
        size="md"
        id="my-planner"
        title="Transfer planner"
        subtitle="The model’s best moves for your squad, planned over the next few gameweeks"
      />
      <Card>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            plan.mutate();
          }}
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
        >
          <Field label="Free transfers">
            {(id) => (
              <Select id={id} value={free} onChange={(e) => setFree(Number(e.target.value))}>
                {[0, 1, 2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n}
                    {n === freeTransfers ? ' (from your history)' : ''}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Transfer hits">
            {(id) => (
              <Select id={id} value={hits} onChange={(e) => setHits(Number(e.target.value))}>
                {HIT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Look ahead">
            {(id) => (
              <Select id={id} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
                {[1, 2, 3, 4].map((n) => (
                  <option key={n} value={n}>
                    {plural(n, 'gameweek')}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <div className="flex flex-col justify-end gap-1.5">
            <label htmlFor={chipId} className={cn('flex items-center gap-2 text-sm text-fg', hasChip ? 'cursor-pointer' : 'opacity-60')}>
              <input
                id={chipId}
                type="checkbox"
                checked={hasChip && useChips}
                disabled={!hasChip}
                onChange={(e) => setUseChips(e.target.checked)}
                className="h-4 w-4 shrink-0 accent-accent"
              />
              Consider chips
            </label>
            <span className="text-micro text-fg-subtle">{hasChip ? 'Only chips you still have' : 'You have no chips left'}</span>
          </div>
          <div className="sm:col-span-2 lg:col-span-4">
            <Button type="submit" loading={plan.isPending}>
              Plan my transfers
            </Button>
          </div>
        </form>
      </Card>

      <div aria-live="polite">
        {plan.isPending && (
          <Card>
            <p role="status" className="text-data text-fg-muted">
              Working out your best moves… this takes about 5–15 seconds.
            </p>
          </Card>
        )}
        {plan.isError && (
          <Card>
            <ErrorState {...planError(plan.error)} onRetry={() => plan.mutate()} />
          </Card>
        )}
        {plan.isSuccess && !plan.isPending && <PlanResult plan={plan.data} squad={squad} />}
        {plan.isIdle && (
          <Card>
            <EmptyState
              title="No plan yet"
              description="Choose your options and press Plan my transfers to see the moves the model likes best."
              className="py-6"
            />
          </Card>
        )}
      </div>
    </section>
  );
}
