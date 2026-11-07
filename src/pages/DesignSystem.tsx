import { useState, type ReactNode } from 'react';
import { Trophy, TrendingUp, Users } from 'lucide-react';
import { SiteHeader } from '../components/shell/SiteHeader';
import { DeadlineBar } from '../components/shell/DeadlineBar';
import {
  Button,
  Card,
  Chip,
  type Column,
  Container,
  DataTable,
  EmptyState,
  ErrorState,
  FdrBadge,
  Field,
  FixtureRow,
  Input,
  Logo,
  PageHeader,
  Pitch,
  PitchPlayer,
  PitchRow,
  PositionBadge,
  SearchInput,
  SectionHeading,
  Select,
  SkeletonRows,
  StatCard,
  StatusBadge,
  TabPanel,
  Tabs,
  ClubCrest,
  PlayerAvatar,
} from '../components/ui';
import { AnswerCards, AnswerCardSkeleton, FollowUps } from '../components/assistant/cards/AnswerCards';
import { SAMPLE_CARDS, SAMPLE_FOLLOW_UPS } from '../components/assistant/cards/samples';
import { ModelReview } from '../components/totw/ModelReview';
import { MlManager } from '../components/totw/MlManager';
import { SAMPLE_ML_MANAGER_PLANNED, SAMPLE_ML_MANAGER_SCORED, SAMPLE_MODEL_REVIEW } from '../components/totw/samples';
import { ExplainOverview } from '../components/explain/ExplainOverview';
import { PlayerExplainer } from '../components/explain/PlayerExplainer';
import { FeatureEffects } from '../components/explain/FeatureEffects';
import { PlayerChips, PlayerSearchBox } from '../components/explain/PlayerPicker';
import { ExplainError, ExplainUnavailable } from '../components/explain/ExplainStates';
import { TrackRecordEmpty, TrackRecordView } from '../components/explain/TrackRecord';
import { SAMPLE_DEPENDENCE, SAMPLE_NOT_FOUND_ERROR, SAMPLE_PLAYER_EXPLANATION, SAMPLE_SUMMARY, SAMPLE_TRACK_RECORD } from '../components/explain/samples';

/*
 * Hidden /design route: a living reference for the Pitch Me design system.
 * Everything below is SAMPLE DATA for showing components; no page in the app
 * uses these values. The one exception to "made-up" is the model review, which
 * replays the model's real GW7 pick (components/totw/samples.ts).
 */

interface SampleRow {
  name: string;
  team: string;
  position: string;
  price: number;
  points: number;
  form: number;
  selected: number;
  status: string;
  chance?: number;
  news?: string;
}

const SAMPLE_ROWS: SampleRow[] = [
  { name: 'Sample Striker', team: 'Arsenal', position: 'FWD', price: 9.5, points: 64, form: 7.2, selected: 31.4, status: 'a' },
  { name: 'Example Winger', team: 'Liverpool', position: 'MID', price: 12.8, points: 71, form: 8.1, selected: 54.9, status: 'd', chance: 75, news: 'Sample news: hamstring, 75% chance' },
  { name: 'Demo Defender', team: 'Chelsea', position: 'DEF', price: 5.6, points: 48, form: 5.4, selected: 12.2, status: 'a' },
  { name: 'Placeholder Keeper', team: 'Brighton', position: 'GKP', price: 4.9, points: 39, form: 4.0, selected: 8.7, status: 'i', news: 'Sample news: knee injury' },
  { name: 'Mock Midfielder', team: 'Newcastle', position: 'MID', price: 7.1, points: 55, form: 6.3, selected: 19.0, status: 's', news: 'Sample news: suspended for one match' },
];

const COLUMNS: Column<SampleRow>[] = [
  {
    key: 'name',
    header: 'Player',
    sticky: true,
    sortValue: (r) => r.name,
    render: (r) => (
      <span className="flex items-center gap-2">
        <PlayerAvatar name={r.name} size={28} />
        <span className="min-w-0">
          <span className="block font-semibold">{r.name}</span>
          <span className="flex items-center gap-1.5 text-micro text-fg-subtle">
            <ClubCrest team={r.team} size={14} label="short" />
            <PositionBadge position={r.position} className="w-auto px-1" />
          </span>
        </span>
        <StatusBadge status={r.status} chance={r.chance} news={r.news} />
      </span>
    ),
  },
  { key: 'price', header: '£', description: 'Price (£m)', align: 'right', sortValue: (r) => r.price, render: (r) => r.price.toFixed(1) },
  { key: 'selected', header: 'Sel %', description: 'Selected by', align: 'right', hideBelow: 'md', sortValue: (r) => r.selected, render: (r) => `${r.selected.toFixed(1)}%` },
  { key: 'form', header: 'Form', align: 'right', hideBelow: 'sm', sortValue: (r) => r.form, render: (r) => r.form.toFixed(1) },
  { key: 'points', header: 'Pts', description: 'Total points', align: 'right', sortValue: (r) => r.points, render: (r) => <span className="font-bold">{r.points}</span> },
];

const SWATCHES: [string, string][] = [
  ['bg', 'bg-bg'],
  ['surface', 'bg-surface'],
  ['surface-2', 'bg-surface-2'],
  ['brand', 'bg-brand'],
  ['brand-2', 'bg-brand-2'],
  ['line', 'bg-line'],
  ['line-strong', 'bg-line-strong'],
  ['accent', 'bg-accent'],
  ['accent-2', 'bg-accent-2'],
  ['warning', 'bg-warning'],
  ['danger', 'bg-danger'],
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="py-6">
      <SectionHeading title={title} />
      {children}
    </section>
  );
}

// Sample deadline two days and five hours out, fixed per page load.
const SAMPLE_DEADLINE = new Date(Date.now() + 2 * 86400000 + 5 * 3600000);

export function DesignSystem() {
  const [tab, setTab] = useState<'overview' | 'stats' | 'fixtures'>('overview');
  const [query, setQuery] = useState('');
  const [explainFeature, setExplainFeature] = useState(SAMPLE_DEPENDENCE.feature);

  return (
    <div data-theme="dark" className="pm-root min-h-screen">
      <SiteHeader activePath="/" />
      <DeadlineBar gameweek={8} deadline={SAMPLE_DEADLINE} />
      <Container>
        <PageHeader
          eyebrow="Internal · sample data"
          title="Design system"
          description="Every component the redesigned pages are built from. All names, numbers and news on this page are made-up sample data, except the model review, which replays the model’s real GW7 pick."
          actions={
            <>
              <Button>Primary</Button>
              <Button variant="secondary">Secondary</Button>
            </>
          }
        />

        <Section title="Logo">
          <div className="flex flex-wrap items-center gap-8">
            <Logo size={44} />
            <Logo size={28} />
            <Logo size={28} iconOnly />
          </div>
        </Section>

        <Section title="Colour tokens">
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-6 lg:grid-cols-11">
            {SWATCHES.map(([name, cls]) => (
              <div key={name}>
                <div className={`h-12 rounded-md border border-line ${cls}`} />
                <p className="mt-1 text-micro text-fg-subtle">{name}</p>
              </div>
            ))}
          </div>
        </Section>

        <Section title="Typography">
          <div className="space-y-2">
            <p className="font-display text-display font-extrabold">Display 40 · Outfit</p>
            <p className="font-display text-h1 font-bold">Heading 1 · 32</p>
            <p className="font-display text-h2 font-bold">Heading 2 · 24</p>
            <p className="font-display text-h3 font-semibold">Heading 3 · 18</p>
            <p className="text-body text-fg-muted">Body 15 · Inter. The quick brown fox jumps over the lazy dog.</p>
            <p className="text-data text-fg-subtle">Data 13 · used in dense tables · 1,234.5</p>
            <p className="text-micro font-semibold uppercase text-fg-subtle">Micro 11 · labels</p>
          </div>
        </Section>

        <Section title="Buttons">
          <div className="flex flex-wrap items-center gap-3">
            <Button>Primary</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Danger</Button>
            <Button loading>Loading</Button>
            <Button disabled>Disabled</Button>
            <Button size="sm">Small</Button>
            <Button size="lg">Large</Button>
          </div>
        </Section>

        <Section title="Badges and chips">
          <div className="flex flex-wrap items-center gap-2">
            <PositionBadge position="GKP" />
            <PositionBadge position="DEF" />
            <PositionBadge position="MID" />
            <PositionBadge position="FWD" />
            <span className="mx-2 h-5 w-px bg-line" />
            <StatusBadge status="a" showAvailable />
            <StatusBadge status="d" chance={50} news="Sample: knock" />
            <StatusBadge status="i" news="Sample: injury" />
            <StatusBadge status="s" />
            <StatusBadge status="u" />
            <span className="mx-2 h-5 w-px bg-line" />
            {[1, 2, 3, 4, 5].map((d) => (
              <FdrBadge key={d} difficulty={d}>
                ARS (H)
              </FdrBadge>
            ))}
            <span className="mx-2 h-5 w-px bg-line" />
            <Chip>Neutral</Chip>
            <Chip tone="accent">Accent</Chip>
            <Chip tone="info">Info</Chip>
            <Chip tone="brand">Brand</Chip>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-4">
            <ClubCrest team="Arsenal" size={32} label="full" />
            <ClubCrest team="Ipswich" size={32} label="short" />
            <ClubCrest team="Unknown FC" size={32} label="full" />
            <PlayerAvatar name="Sample Player" size={48} />
          </div>
        </Section>

        <Section title="Stat cards">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Top scorer" value="14" detail="Sample Striker · ARS" icon={<Trophy className="h-4 w-4" />} highlight />
            <StatCard label="Most selected" value="54.9%" detail="Example Winger · LIV" icon={<Users className="h-4 w-4" />} />
            <StatCard label="Best form" value="8.1" detail="Example Winger · LIV" icon={<TrendingUp className="h-4 w-4" />} />
            <StatCard label="Average points" value="52" detail="Sample gameweek" />
          </div>
        </Section>

        <Section title="Tabs">
          <Tabs
            label="Sample tabs"
            idPrefix="demo"
            value={tab}
            onChange={setTab}
            items={[
              { value: 'overview', label: 'Overview' },
              { value: 'stats', label: 'Statistics' },
              { value: 'fixtures', label: 'Fixtures' },
            ]}
          />
          <TabPanel idPrefix="demo" value={tab} className="py-4 text-body text-fg-muted">
            Panel for “{tab}”. Use the arrow keys to move between tabs.
          </TabPanel>
        </Section>

        <Section title="Form controls">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Search players">
              {(id) => <SearchInput id={id} value={query} onChange={setQuery} placeholder="Search by name…" />}
            </Field>
            <Field label="Position">
              {(id) => (
                <Select id={id} defaultValue="all">
                  <option value="all">All positions</option>
                  <option value="GK">Goalkeepers</option>
                  <option value="DEF">Defenders</option>
                </Select>
              )}
            </Field>
            <Field label="Max price" hint="In £m">
              {(id) => <Input id={id} type="number" placeholder="15.0" />}
            </Field>
          </div>
        </Section>

        <Section title="Data table">
          <DataTable
            caption="Sample player statistics"
            showCaption
            columns={COLUMNS}
            rows={SAMPLE_ROWS}
            rowKey={(r) => r.name}
            initialSort={{ key: 'points', direction: 'desc' }}
          />
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <DataTable caption="Loading state" showCaption columns={COLUMNS.slice(0, 3)} rows={[]} rowKey={(r) => r.name} loading skeletonRows={3} />
            <DataTable caption="Empty state" showCaption columns={COLUMNS.slice(0, 3)} rows={[]} rowKey={(r) => r.name} empty="No players match these filters" />
          </div>
        </Section>

        <Section title="Fixtures">
          <Card padded={false}>
            <p className="border-b border-line px-4 py-2 text-micro font-semibold uppercase text-fg-subtle">Sample matchday</p>
            <FixtureRow home="Arsenal" away="Chelsea" homeScore={2} awayScore={1} status="finished" />
            <FixtureRow home="Liverpool" away="Ipswich" homeScore={1} awayScore={1} status="live" />
            <FixtureRow home="Brighton" away="Newcastle" kickoff={new Date(Date.now() + 86400000)} />
            <FixtureRow home="Everton" away="Fulham" status="postponed" />
          </Card>
        </Section>

        <Section title="Pitch">
          <div className="mx-auto max-w-2xl">
            <Pitch label="Sample team of the week">
              <PitchRow label="Goalkeeper">
                <PitchPlayer name="Placeholder Keeper" team="Brighton" opponents={[{ team: 'Everton', home: true }]} value="6 pts" />
              </PitchRow>
              <PitchRow label="Defenders">
                <PitchPlayer name="Demo Defender" team="Chelsea" opponents={[{ team: 'Fulham', home: false }]} value="8 pts" />
                <PitchPlayer name="Another Defender" team="Arsenal" opponents={[{ team: 'Sunderland', home: true }]} value="7 pts" />
                <PitchPlayer name="Third Defender" team="Liverpool" opponents={[{ team: 'Ipswich', home: true }]} value="6 pts" status="d" statusNote="Sample: knock, 75% chance" />
                <PitchPlayer name="Fourth Defender" team="Newcastle" opponents={[{ team: 'Brentford', home: false }]} value="6 pts" />
              </PitchRow>
              <PitchRow label="Midfielders">
                <PitchPlayer name="Example Winger" team="Liverpool" opponents={[{ team: 'Ipswich', home: true }]} value="15 pts" captain />
                <PitchPlayer name="Mock Midfielder" team="Man City" opponents={[{ team: 'Spurs', home: false }]} value="9 pts" viceCaptain />
                <PitchPlayer name="Sample Playmaker" team="Aston Villa" opponents={[{ team: 'Bournemouth', home: true }, { team: 'Leeds', home: false }]} value="8 pts" />
                <PitchPlayer name="Test Midfielder" team="Brighton" opponents={[{ team: 'Everton', home: true }]} value="7 pts" status="i" statusNote="Sample: hamstring" />
              </PitchRow>
              <PitchRow label="Forwards">
                <PitchPlayer name="Sample Striker" team="Arsenal" opponents={[{ team: 'Sunderland', home: true }]} value="12 pts" />
                <PitchPlayer name="Second Striker" team="Chelsea" opponents={[{ team: 'Fulham', home: false }]} value="9 pts" />
              </PitchRow>
            </Pitch>
          </div>
        </Section>

        <Section title="Model vs actual">
          <p className="mb-4 max-w-3xl text-body text-fg-muted">
            The Team of the Week review tab: the squad the model picked before a deadline and what it scored, with
            FPL&rsquo;s auto-subs applied (Greaves came on for Cherki, who didn&rsquo;t play). Points are counted, so
            the captain&rsquo;s are doubled; the prediction sits under them.
          </p>
          <ModelReview review={SAMPLE_MODEL_REVIEW} onGameweekChange={() => undefined} />
        </Section>

        <Section title="ML manager">
          <p className="mb-4 max-w-3xl text-body text-fg-muted">
            The Team of the Week&rsquo;s ML manager tab, with the model&rsquo;s real GW9 squad and fixtures but made-up
            points, ranks and later transfers: a season-long team the model runs under
            the game&rsquo;s rules from GW9. First the plan for the next gameweek before its deadline (one transfer, no
            hit), then a scored gameweek with Triple Captain, a 4-point hit and an automatic substitution.
          </p>
          <p className="mb-2 text-micro font-semibold uppercase text-fg-subtle">Planned, before the deadline</p>
          <MlManager view={SAMPLE_ML_MANAGER_PLANNED} />

          <p className="mb-2 mt-10 text-micro font-semibold uppercase text-fg-subtle">Scored</p>
          <MlManager view={SAMPLE_ML_MANAGER_SCORED} />

          <p className="mb-2 mt-10 text-micro font-semibold uppercase text-fg-subtle">States</p>
          <div className="grid gap-4 md:grid-cols-2">
            <MlManager error={SAMPLE_NOT_FOUND_ERROR} />
            <MlManager error={new Error('Sample error: the server could not be reached.')} onRetry={() => undefined} />
          </div>
        </Section>

        <Section title="Model insights">
          <p className="mb-4 max-w-3xl text-body text-fg-muted">
            The /model page&rsquo;s three tabs with made-up explanations: global SHAP importance and the beeswarm, one
            player&rsquo;s SHAP waterfall and LIME weights per fixture (a double gameweek for a doubtful player, so every
            adjustment step shows), and a SHAP dependence plot.
          </p>
          <p className="mb-2 text-micro font-semibold uppercase text-fg-subtle">Overview</p>
          <ExplainOverview summary={SAMPLE_SUMMARY} onPickPlayer={() => undefined} />

          <p className="mb-2 mt-10 text-micro font-semibold uppercase text-fg-subtle">Explain a player</p>
          <Card className="mb-6">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
              <PlayerSearchBox onSelect={() => undefined} />
              <div className="min-w-0">
                <p className="mb-1.5 text-micro font-semibold uppercase text-fg-muted">Top predicted</p>
                <PlayerChips players={SAMPLE_SUMMARY.top_players} selected={SAMPLE_PLAYER_EXPLANATION.player.element} onSelect={() => undefined} />
              </div>
            </div>
          </Card>
          <PlayerExplainer
            explanation={SAMPLE_PLAYER_EXPLANATION}
            descriptions={Object.fromEntries(SAMPLE_SUMMARY.features.map((f) => [f.feature, f.description]))}
            lookupPhotos={false}
          />

          <p className="mb-2 mt-10 text-micro font-semibold uppercase text-fg-subtle">Feature effects</p>
          <FeatureEffects
            features={SAMPLE_SUMMARY.features}
            feature={explainFeature}
            onFeatureChange={setExplainFeature}
            dependence={SAMPLE_DEPENDENCE}
            problem={
              explainFeature !== SAMPLE_DEPENDENCE.feature ? (
                <EmptyState title="Sample data covers one feature" description={`Pick “${SAMPLE_DEPENDENCE.label}” to see the plot.`} />
              ) : undefined
            }
          />

          <p className="mb-2 mt-10 text-micro font-semibold uppercase text-fg-subtle">Track record</p>
          <TrackRecordView data={SAMPLE_TRACK_RECORD} />
          <div className="mt-4">
            <TrackRecordEmpty />
          </div>

          <p className="mb-2 mt-10 text-micro font-semibold uppercase text-fg-subtle">States</p>
          <div className="grid gap-4 md:grid-cols-2">
            <ExplainUnavailable />
            <ExplainError
              error={SAMPLE_NOT_FOUND_ERROR}
              what="this player’s explanation"
              notFound={{
                title: 'Not in this gameweek’s predictions',
                description: 'There’s no prediction to explain for this player in gameweek 9, usually because he isn’t in the FPL game any more. Try another player.',
              }}
            />
          </div>
        </Section>

        <Section title="Assistant answer cards">
          <p className="mb-4 max-w-3xl text-body text-fg-muted">
            Shown under an assistant answer, one or more per answer, built from the query&rsquo;s structured data. Each
            column is about as wide as the chat. Sample players have no photo, so they show the blank silhouette.
          </p>
          <div className="grid items-start gap-x-6 gap-y-3 lg:grid-cols-2">
            <AnswerCards cards={SAMPLE_CARDS.filter((_, i) => i % 2 === 0)} lookupPhotos={false} className="mt-0" />
            <AnswerCards cards={SAMPLE_CARDS.filter((_, i) => i % 2 === 1)} lookupPhotos={false} className="mt-0" />
          </div>
          <div className="mt-6 grid items-start gap-6 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-micro font-semibold uppercase text-fg-subtle">Loading (while the answer is on its way)</p>
              <AnswerCardSkeleton />
            </div>
            <FollowUps questions={SAMPLE_FOLLOW_UPS} onAsk={() => undefined} className="mt-0" />
          </div>
        </Section>

        <Section title="States">
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <SkeletonRows rows={4} />
            </Card>
            <Card>
              <EmptyState title="No fixtures" description="Sample empty state for a gameweek without matches." />
            </Card>
            <Card>
              <ErrorState description="Sample error: the server could not be reached." onRetry={() => undefined} />
            </Card>
          </div>
        </Section>
      </Container>
      <footer className="mt-10 border-t border-line py-6">
        <Container className="text-micro text-fg-subtle">
          Pitch Me is a fan-made project and is not affiliated with the Premier League or Fantasy Premier League.
        </Container>
      </footer>
    </div>
  );
}
