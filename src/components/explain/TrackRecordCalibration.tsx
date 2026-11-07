import { useMemo } from 'react';
import type { TrackRecordBin } from '../../services/trackRecordApi';
import { TrackRecordBars } from './TrackRecordBars';
import { points } from './format';

// Predicted vs actual mean points by size of prediction, pooled over the season: when the model
// says "about 5 points", do players average about 5?

export function TrackRecordCalibration({ bins }: { bins: TrackRecordBin[] }) {
  const shown = useMemo(() => bins.filter((b) => b.n > 0), [bins]);
  const summary = useMemo(() => {
    if (!shown.length) return 'No predictions to compare yet.';
    const parts = shown.map((b) => `predicted ${b.label} points: ${points(b.predicted ?? 0, 2)} on average, ${points(b.actual ?? 0, 2)} actually scored, over ${b.n} player-gameweeks`);
    return `Predicted against actual average points in ${shown.length} groups. ${parts.join('; ')}.`;
  }, [shown]);

  return (
    <TrackRecordBars
      categories={shown.map((b) => ({ key: b.label, label: b.label.replace('-', '–'), sublabel: `${b.n.toLocaleString('en-GB')} players` }))}
      series={[
        { key: 'predicted', label: 'Predicted', tone: 'muted', values: shown.map((b) => b.predicted) },
        { key: 'actual', label: 'Actually scored', tone: 'accent', values: shown.map((b) => b.actual) },
      ]}
      summary={summary}
      digits={2}
      showValues
      yCaption="Average points"
      categoryHeader="Predicted points"
      tableCaption="Average predicted and actual points for each size of prediction"
    />
  );
}
