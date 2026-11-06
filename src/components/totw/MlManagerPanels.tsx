import { ArrowDown, ArrowRight, ArrowUp } from 'lucide-react';
import type {
  MlManagerChipState,
  MlManagerGameweek,
  MlManagerPlayerRef,
  MlManagerTransfer,
} from '../../services/api';
import { cn } from '../../utils/cn';
import { displayName } from '../../utils/playerNames';
import { PlayerNameButton } from '../players/PlayerNameButton';
import { Card, Chip, ClubCrest, PlayerAvatar, PositionBadge, SectionHeading } from '../ui';
import {
  CHIP_NAMES,
  benchSlot,
  chipStatus,
  freeTransfersUsed,
  money,
  nextFreeTransfers,
  plural,
  shortName,
  shownProjection,
  unlimitedTransfers,
  type ChipPhase,
  type Lineup,
} from './mlManagerFormat';

// The ML manager tab's side panels: this gameweek's transfers, the bench and the chips. Each card
// is a CSS size container, so its layout follows its own width (the aside on desktop, the page on
// phones) rather than the viewport. Container-query classes stay literal for Tailwind.

// --- Transfers -----------------------------------------------------------------------------------

function TransferSide({ direction, player, price }: { direction: 'out' | 'in'; player: MlManagerPlayerRef; price: number }) {
  const Icon = direction === 'out' ? ArrowDown : ArrowUp;
  return (
    <div className="flex min-w-0 items-center gap-2">
      <Chip
        tone={direction === 'out' ? 'danger' : 'accent'}
        icon={<Icon className="h-3 w-3" aria-hidden="true" />}
        className="w-12 shrink-0 justify-center"
      >
        {direction === 'out' ? 'Out' : 'In'}
      </Chip>
      <PlayerAvatar name={player.name} src={player.photo} size={28} />
      <span className="min-w-0 flex-1">
        <span title={player.name} className="block truncate text-sm font-semibold text-fg">
          {displayName(player.name, player.web_name)}
        </span>
        <span className="flex items-center gap-1.5 text-micro text-fg-subtle">
          <ClubCrest team={player.team} size={14} label="short" />
          <PositionBadge position={player.position} className="w-auto px-1" />
        </span>
      </span>
      <span className="shrink-0 text-data tabular-nums text-fg-muted">
        <span className="sr-only">{direction === 'out' ? 'sold for ' : 'bought for '}</span>
        {money(price)}
      </span>
    </div>
  );
}

function TransferRow({ transfer }: { transfer: MlManagerTransfer }) {
  return (
    <li
      className={cn(
        'grid gap-2 border-t border-line px-4 py-3',
        '[@container(min-width:30rem)]:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] [@container(min-width:30rem)]:items-center',
      )}
    >
      <TransferSide direction="out" player={transfer.out} price={transfer.out.selling_price} />
      <ArrowRight aria-hidden="true" className="hidden h-4 w-4 text-fg-subtle [@container(min-width:30rem)]:block" />
      <TransferSide direction="in" player={transfer.in} price={transfer.in.price} />
    </li>
  );
}

interface MovesCardProps {
  gw: MlManagerGameweek;
  maxFreeTransfers: number;
  /** The first gameweek: the squad itself was picked for free. */
  isStart: boolean;
}

/** "This gameweek's moves": transfers out → in, the free transfers and hits they cost, and why. */
export function MovesCard({ gw, maxFreeTransfers, isStart }: MovesCardProps) {
  const unlimited = unlimitedTransfers(gw);
  const usedFree = freeTransfersUsed(gw);
  const next = nextFreeTransfers(gw, maxFreeTransfers);
  const planned = gw.status === 'planned';

  let noMoves: string;
  if (isStart) {
    noMoves = `The starting squad: 15 players picked for free before the GW${gw.gameweek} deadline, with ${money(gw.bank_after)} left in the bank.`;
  } else if (gw.free_transfers > 0) {
    noMoves = `No transfers${planned ? ' planned' : ''}: ${plural(gw.free_transfers, 'free transfer')} saved, so ${next} next gameweek.`;
  } else {
    noMoves = `No transfers${planned ? ' planned' : ''}.`;
  }

  const costLine = unlimited
    ? `${gw.chip ? CHIP_NAMES[gw.chip] : 'Chip'}: unlimited free transfers this gameweek` +
      (gw.free_transfers > 0 ? `, and the ${plural(gw.free_transfers, 'banked free transfer')} ${gw.free_transfers === 1 ? 'is' : 'are'} kept.` : '.')
    : gw.hits > 0
      ? `${plural(usedFree, 'free transfer')} used, plus ${plural(gw.hits, 'extra transfer')} for −${gw.hit_cost} points.`
      : `${usedFree} of ${plural(gw.free_transfers, 'free transfer')} used, no points deducted.`;

  return (
    <Card padded={false} className="[container-type:inline-size]">
      <div className="px-4 pt-4">
        <SectionHeading
          as="h3"
          title="This gameweek’s moves"
          subtitle={planned ? `Planned for GW${gw.gameweek}; replanned until the deadline` : `GW${gw.gameweek}, as made before the deadline`}
        />
      </div>

      {gw.transfers.length > 0 ? (
        <>
          <ol aria-label={`${plural(gw.transfers.length, 'transfer')}${planned ? ' planned' : ''}`}>
            {gw.transfers.map((t) => (
              <TransferRow key={`${t.out.element}-${t.in.element}`} transfer={t} />
            ))}
          </ol>
          <p className="border-t border-line px-4 py-3 text-data text-fg-muted">{costLine}</p>
        </>
      ) : (
        <p className="border-t border-line px-4 py-3 text-data text-fg-muted">
          {unlimited && !isStart ? costLine : noMoves}
        </p>
      )}

      {gw.chip === 'freehit' && (
        <p className="border-t border-line px-4 py-3 text-data text-fg-muted">
          Free Hit: the squad goes back to the previous gameweek’s for the next one.
        </p>
      )}

      {gw.reason && (
        <p className="border-t border-line px-4 py-3 text-data text-fg">
          <span className="font-semibold text-fg-muted">Why: </span>
          {gw.reason}
        </p>
      )}

      {gw.status !== 'scored' && gw.horizon.length > 1 && (
        <div className="border-t border-line px-4 py-3">
          <p className="text-micro font-semibold uppercase text-fg-subtle">Projected points ahead</p>
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {gw.horizon.map((h) => (
              <li key={h.gameweek}>
                <Chip tone={h.gameweek === gw.gameweek ? 'brand' : 'neutral'} className="tabular-nums">
                  GW{h.gameweek} · {h.projected.toFixed(1)}
                </Chip>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

// --- Bench ---------------------------------------------------------------------------------------

/** The four substitutes in priority order, with what they're projected (or scored). */
export function BenchCard({ gw, lineup }: { gw: MlManagerGameweek; lineup: Lineup }) {
  const scored = gw.status === 'scored';
  const boost = gw.chip === 'bboost';
  const benchPoints = lineup.bench.reduce((sum, p) => sum + (lineup.results.get(p.element)?.points ?? 0), 0);
  const subtitle = boost
    ? 'Bench Boost: all four count this gameweek'
    : scored
      ? `In priority order; their ${benchPoints} ${benchPoints === 1 ? 'point doesn’t' : 'points don’t'} count`
      : 'In priority order: they come on for starters who don’t play';

  return (
    <Card padded={false}>
      <div className="px-4 pt-4">
        <SectionHeading as="h3" title="Bench" subtitle={subtitle} />
      </div>
      <ol>
        {lineup.bench.map((p, i) => {
          const slot = benchSlot(lineup.benchSlots[i] ?? p);
          const result = lineup.results.get(p.element);
          const projection = shownProjection(p, gw);
          const replacement = lineup.replacedBy.get(p.element);
          return (
            <li key={p.element} className="flex items-center gap-3 border-t border-line px-4 py-2.5">
              <span
                title={slot.long}
                className="inline-flex h-6 w-7 shrink-0 items-center justify-center rounded-sm bg-surface-2 text-micro font-bold text-fg-muted"
              >
                <span aria-hidden="true">{slot.short}</span>
                <span className="sr-only">{slot.long}:</span>
              </span>
              <PlayerAvatar name={p.name} src={p.photo} size={32} />
              <span className="min-w-0 flex-1">
                <PlayerNameButton
                  player={{ name: p.name, team: p.team, position: p.position, photo: p.photo }}
                  label={displayName(p.name, p.web_name)}
                  className="block text-sm font-semibold text-fg"
                />
                {replacement ? (
                  <span className="flex items-center gap-1.5 text-micro font-semibold text-warning">
                    <ClubCrest team={p.team} size={14} />
                    <span>Didn’t play · replaced by {shortName(replacement)}</span>
                  </span>
                ) : (
                  <ClubCrest team={p.team} size={14} label="full" className="text-data text-fg-subtle" />
                )}
              </span>
              <PositionBadge position={p.position} />
              <span className="w-14 shrink-0 text-right tabular-nums">
                {scored ? (
                  <>
                    <span className={cn('block text-sm font-bold', boost ? 'text-fg' : 'text-fg-muted')}>
                      {result?.points ?? '–'}
                      <span className="sr-only">{result?.points == null ? ' (no points recorded)' : boost ? ' points, counted' : ' points, not counted'}</span>
                    </span>
                    {projection != null && (
                      <span className="block text-micro text-fg-subtle">
                        <span aria-hidden="true">pred </span>
                        <span className="sr-only">predicted </span>
                        {projection.toFixed(1)}
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    <span className="block text-sm font-bold text-fg-muted">
                      {projection != null ? projection.toFixed(1) : '–'}
                      <span className="sr-only"> projected points</span>
                    </span>
                    <span className="block text-micro text-fg-subtle">{money(p.now_cost)}</span>
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

// --- Chips ---------------------------------------------------------------------------------------

const PHASE_TONE: Record<ChipPhase, 'accent' | 'info' | 'neutral'> = {
  planned: 'info',
  played: 'info',
  used: 'neutral',
  available: 'accent',
  upcoming: 'neutral',
  expired: 'neutral',
};

/** One of each chip per half of the season: available, planned, used or expired. */
export function ChipsPanel({ chips, gw }: { chips: MlManagerChipState[]; gw: MlManagerGameweek }) {
  // One group per half (chips sharing the last gameweek of their window), in season order.
  const halves = new Map<number, MlManagerChipState[]>();
  for (const c of chips) halves.set(c.window[1], [...(halves.get(c.window[1]) ?? []), c]);
  const ordered = [...halves.entries()].sort((a, b) => a[0] - b[0]);
  const names = ordered.length === 2 ? ['First half', 'Second half'] : ordered.map(() => '');

  return (
    <Card className="[container-type:inline-size]">
      <div className="space-y-5">
        {ordered.map(([end, items], gi) => {
          const start = Math.min(...items.map((c) => c.window[0]));
          const label = `${names[gi] ? `${names[gi]} · ` : ''}GW${start}–${end}`;
          return (
            <div key={end}>
              <p className="mb-2 text-micro font-semibold uppercase text-fg-subtle">{label}</p>
              <ul aria-label={`Chips, ${label}`} className="grid grid-cols-2 gap-2 [@container(min-width:34rem)]:grid-cols-4">
                {items.map((c) => {
                  const status = chipStatus(c, gw);
                  const faded = status.phase === 'used' || status.phase === 'expired' || status.phase === 'upcoming';
                  return (
                    <li
                      key={`${c.chip}-${end}`}
                      className={cn(
                        'rounded-md border p-3',
                        status.phase === 'planned' || status.phase === 'played' ? 'border-accent-2/60 bg-accent-2/10' : 'border-line bg-surface-2/40',
                      )}
                    >
                      <p className={cn('text-sm font-semibold', faded ? 'text-fg-muted' : 'text-fg')}>{c.label || CHIP_NAMES[c.chip]}</p>
                      <Chip tone={PHASE_TONE[status.phase]} className="mt-1.5">
                        {status.text}
                      </Chip>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
