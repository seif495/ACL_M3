import { useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Card, Container, PageHeader, SectionHeading } from '../components/ui';
import { usePageMeta } from '../hooks/usePageMeta';

interface Entry {
  id: string;
  term: string;
  body: string;
}

const FPL_TERMS: Entry[] = [
  { id: 'xpts', term: 'xPts / predicted points', body: "The points the site's model expects a player to score in the next gameweek. A player with a double gameweek gets the sum of his fixtures. Players ruled out get 0, and doubtful ones are scaled down. It is an average expectation, not a promise." },
  { id: 'fdr', term: 'FDR (fixture difficulty rating)', body: "FPL's own 1 to 5 score for how hard a match looks for a team, where 1 is easiest. The site shows it next to upcoming opponents." },
  { id: 'ownership', term: 'Ownership', body: 'The share of FPL managers who have a player in their squad.' },
  { id: 'differential', term: 'Differential', body: 'A player few managers own. If he scores well, you gain on everyone who does not have him; if not, you lose ground.' },
  { id: 'template', term: 'Template', body: 'The group of highly owned players that many squads share. Having the template means your team looks like most others.' },
  { id: 'hits', term: 'Hits', body: 'Each transfer beyond your free ones costs 4 points. A hit pays off only if the new player scores more than the old one by more than that.' },
  { id: 'free-transfers', term: 'Free transfers', body: 'You get 1 free transfer a gameweek. Unused ones are banked, up to 5.' },
  { id: 'chips', term: 'Chips', body: 'One-off boosts, played one per gameweek. There are two of each, one for each half of the season.' },
  { id: 'wildcard', term: 'Wildcard', body: 'Make any number of transfers in one gameweek with no point cost.' },
  { id: 'free-hit', term: 'Free hit', body: 'Make unlimited transfers for a single gameweek only. Your squad returns to how it was afterwards.' },
  { id: 'bench-boost', term: 'Bench boost', body: 'The points of your bench players count too, for one gameweek.' },
  { id: 'triple-captain', term: 'Triple captain', body: "Your captain's points are tripled instead of doubled, for one gameweek." },
  { id: 'bps', term: 'BPS and bonus', body: "The Bonus Points System scores every player's match actions. In each match the three highest BPS earn 3, 2 and 1 bonus points." },
  { id: 'defensive-contributions', term: 'Defensive contributions', body: 'Points for defenders and others who reach a set number of defensive actions in a match, such as tackles and clearances. They have been part of the scoring since 2025-26.' },
  { id: 'xg', term: 'xG, xA and xGI', body: 'Expected goals, expected assists and the two added together. They measure the quality of the chances a player gets or creates, which shows his threat more steadily than goals do.' },
  { id: 'clean-sheet', term: 'Clean sheet', body: 'A team concedes no goals while a player is on the pitch for 60 minutes or more. It earns points for goalkeepers and defenders, and a smaller amount for midfielders.' },
  { id: 'double-blank', term: 'Double and blank gameweeks', body: "A double gameweek is one where a team plays twice, so its players can score twice. In a blank gameweek a team has no match and its players score nothing." },
];

const SITE_TERMS: Entry[] = [
  { id: 'model-predicts', term: 'What the model predicts', body: "For every player and every upcoming fixture it predicts the FPL points he will score in that match. A player's gameweek number is the sum over his fixtures that week. It is retrained weekly, and the new version is used only if it holds up against the old one." },
  { id: 'model-inputs', term: 'What goes in', body: "Only information from before the match. This covers the player's recent form and season to date, how many goals and chances his team and the opponent have been creating and allowing, and home or away, rest days, price and ownership. It also uses how strong each side looks after adjusting for who they have played, how many managers are buying or selling him, bookmakers' pre-match odds, and FPL's availability flags before the deadline." },
  { id: 'availability', term: 'Availability scaling', body: "Players FPL lists as out get 0 points. For a doubtful player (25, 50 or 75 percent chance of playing) the prediction is multiplied by a factor fitted on past seasons, a little milder than the stated chance. The captain choice uses FPL's chance of playing directly." },
  { id: 'projections', term: 'The 4-week projections', body: "Beyond the next gameweek, the site projects the following three. They reuse the same player state as at the deadline with those gameweeks' fixtures, and the chance of playing fades the further ahead you look. They are less accurate than the next-gameweek numbers, so later weeks count for less in plans." },
  { id: 'ensemble', term: 'The ensemble', body: 'The live model is the average of 5 XGBoost models trained with different random seeds. Averaging them smooths out the quirks of any single one.' },
  { id: 'ml-manager', term: 'ML manager', body: 'A season-long FPL team run by the model under the real rules, from gameweek 9 of 2026-27. It plans transfers and chips ahead, locks each team at the deadline and is ranked against managers who started that week.' },
  { id: 'my-team', term: 'My team', body: "Enter your FPL team ID to see your squad with the model's predictions and a suggested transfer plan. Nothing is stored on the site; your ID stays in your browser." },
];

const ALL = [...FPL_TERMS, ...SITE_TERMS];
const SORTED = [...ALL].sort((a, b) => a.term.localeCompare(b.term));

function EntryList({ entries }: { entries: Entry[] }) {
  return (
    <dl className="divide-y divide-line">
      {entries.map((e) => (
        <div key={e.id} id={e.id} className="scroll-mt-24 py-3 first:pt-0 last:pb-0">
          <dt className="font-display text-h3 font-bold text-fg">
            <a href={`#${e.id}`} className="hover:text-accent">{e.term}</a>
          </dt>
          <dd className="mt-1 text-body text-fg-muted">{e.body}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Glossary() {
  usePageMeta({
    title: 'Glossary',
    description: 'Plain-language explanations of Fantasy Premier League terms and how the Pitch Me prediction model works.',
  });
  const { hash } = useLocation();

  // The page is lazy-loaded, so the browser's own jump to #anchor can come too early.
  useEffect(() => {
    if (!hash) return;
    const el = document.getElementById(decodeURIComponent(hash.slice(1)));
    el?.scrollIntoView({ block: 'start' });
  }, [hash]);

  return (
    <Container className="max-w-3xl pb-12">
      <PageHeader
        eyebrow="Help"
        title="Glossary"
        description="FPL terms and how the model behind the predictions works, in plain language."
      />

      <div className="space-y-4">
        <Card>
          <SectionHeading as="h2" size="md" title="Index" subtitle="Jump to a term" />
          <ul className="flex flex-wrap gap-x-4 gap-y-2 text-data">
            {SORTED.map((e) => (
              <li key={e.id}>
                <a href={`#${e.id}`} className="text-accent underline-offset-2 hover:underline">{e.term}</a>
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <SectionHeading as="h2" size="md" title="FPL terms" />
          <EntryList entries={FPL_TERMS} />
        </Card>

        <Card>
          <SectionHeading as="h2" size="md" title="How the site works" subtitle="The model, its limits and the tools built on it" />
          <EntryList entries={SITE_TERMS} />
          <p className="mt-4 text-data text-fg-muted">
            See the model&apos;s inputs explained for each player on <Link to="/model" className="text-accent hover:underline">Model insights</Link>,
            and how its past forecasts compared with what happened on the{' '}
            <Link to="/model?tab=record" className="text-accent hover:underline">track record</Link> tab. With only a few gameweeks
            of results, treat that record as anecdote, not proof.
          </p>
        </Card>
      </div>
    </Container>
  );
}
