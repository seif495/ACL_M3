import { useState, type FormEvent } from 'react';
import { Button, Card, Field, Input } from '../ui';
import { parseTeamId } from './format';

/** The team ID box. Help text says where to find the number on the FPL website. */
export function TeamIdForm({
  initial,
  loading,
  onSubmit,
}: {
  initial?: number | null;
  loading?: boolean;
  onSubmit: (id: number) => void;
}) {
  const [text, setText] = useState(initial ? String(initial) : '');
  const [touched, setTouched] = useState(false);
  const id = parseTeamId(text);
  const invalid = touched && id == null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (id != null) onSubmit(id);
  };

  return (
    <Card>
      <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <Field
          label="FPL team ID"
          className="min-w-0 sm:w-72"
          error={invalid ? 'Enter your team ID: a number from 1 to 99,999,999.' : undefined}
        >
          {(inputId, a11y) => (
            <Input
              id={inputId}
              inputMode="numeric"
              autoComplete="off"
              placeholder="e.g. 1234567"
              value={text}
              onChange={(e) => setText(e.target.value.replace(/[^\d]/g, '').slice(0, 8))}
              {...a11y}
              aria-describedby={[a11y['aria-describedby'], 'team-id-help'].filter(Boolean).join(' ')}
            />
          )}
        </Field>
        <Button type="submit" loading={loading}>
          Load my team
        </Button>
      </form>
      <p id="team-id-help" className="mt-3 text-data text-fg-subtle">
        Find your team ID on the FPL website: open Points, and it’s the number after /entry/ in the address.
      </p>
    </Card>
  );
}
