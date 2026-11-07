import { useId } from 'react';
import { Minus, Plus, RotateCcw } from 'lucide-react';
import { Button, Card, Select } from '../../ui';
import { BUILDER_FORMATIONS, BUILDER_LIMITS } from '../../../services/teamBuilderApi';
import { cn } from '../../../utils/cn';
import { MAX_BUDGET, MIN_BUDGET, type Constraints } from './builderModel';

interface Props {
  draft: Constraints;
  onChange: (next: Constraints) => void;
  /** The draft differs from what is shown. */
  dirty: boolean;
  loading: boolean;
  /** Something other than the defaults is set (applied or in the draft). */
  canReset: boolean;
  onRebuild: () => void;
  onReset: () => void;
}

const STEP = 1; // tenths of a million per stepper press

/** Budget, club limit and formation, plus Rebuild and Reset. Edits only change the draft. */
export function BuilderControls({ draft, onChange, dirty, loading, canReset, onRebuild, onReset }: Props) {
  const budgetId = useId();
  const formationId = useId();
  const setBudget = (tenths: number) => onChange({ ...draft, budget: Math.min(MAX_BUDGET, Math.max(MIN_BUDGET, tenths)) });

  return (
    <Card className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          <div className="mb-1.5 flex items-baseline justify-between gap-2">
            <label htmlFor={budgetId} className="text-micro font-semibold uppercase text-fg-muted">
              Budget
            </label>
            <output htmlFor={budgetId} className="font-display text-h3 font-bold tabular-nums text-fg">
              £{(draft.budget / 10).toFixed(1)}m
            </output>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              className="w-10 shrink-0 px-0"
              aria-label="Lower the budget by £0.1m"
              disabled={draft.budget <= MIN_BUDGET}
              onClick={() => setBudget(draft.budget - STEP)}
              icon={<Minus className="h-4 w-4" aria-hidden="true" />}
            />
            <input
              id={budgetId}
              type="range"
              min={MIN_BUDGET}
              max={MAX_BUDGET}
              step={1}
              value={draft.budget}
              onChange={(e) => setBudget(Number(e.target.value))}
              aria-valuetext={`£${(draft.budget / 10).toFixed(1)} million`}
              className="h-2 min-w-0 flex-1 cursor-pointer accent-[rgb(var(--accent))]"
            />
            <Button
              variant="secondary"
              className="w-10 shrink-0 px-0"
              aria-label="Raise the budget by £0.1m"
              disabled={draft.budget >= MAX_BUDGET}
              onClick={() => setBudget(draft.budget + STEP)}
              icon={<Plus className="h-4 w-4" aria-hidden="true" />}
            />
          </div>
          <p className="mt-1 text-micro text-fg-subtle">
            £{BUILDER_LIMITS.minBudget.toFixed(1)}m to £{BUILDER_LIMITS.maxBudget.toFixed(1)}m. FPL’s game is £100.0m.
          </p>
        </div>

        <div className="min-w-0">
          <span id={`${budgetId}-club`} className="mb-1.5 block text-micro font-semibold uppercase text-fg-muted">
            Max per club
          </span>
          <div role="group" aria-labelledby={`${budgetId}-club`} className="flex gap-1.5">
            {[1, 2, 3].map((n) => (
              <Button
                key={n}
                variant={draft.maxPerClub === n ? 'primary' : 'secondary'}
                size="md"
                aria-pressed={draft.maxPerClub === n}
                className={cn('flex-1', draft.maxPerClub !== n && 'text-fg-muted')}
                onClick={() => onChange({ ...draft, maxPerClub: n })}
              >
                {n}
              </Button>
            ))}
          </div>
          <p className="mt-1 text-micro text-fg-subtle">FPL allows 3.</p>
        </div>

        <div className="min-w-0">
          <label htmlFor={formationId} className="mb-1.5 block text-micro font-semibold uppercase text-fg-muted">
            Formation
          </label>
          <Select
            id={formationId}
            value={draft.formation ?? 'best'}
            onChange={(e) => onChange({ ...draft, formation: e.target.value === 'best' ? null : e.target.value })}
          >
            <option value="best">Best for the points</option>
            {BUILDER_FORMATIONS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </Select>
          <p className="mt-1 text-micro text-fg-subtle">The XI’s defenders, midfielders, forwards.</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={onRebuild} loading={loading} disabled={!dirty && !loading}>
          Rebuild
        </Button>
        <Button
          variant="ghost"
          onClick={onReset}
          disabled={!canReset}
          icon={<RotateCcw className="h-4 w-4" aria-hidden="true" />}
        >
          Reset
        </Button>
        <p role="status" className="text-data text-fg-subtle">
          {dirty ? 'You have changes that aren’t applied yet.' : ''}
        </p>
      </div>
    </Card>
  );
}
