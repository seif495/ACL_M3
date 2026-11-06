import { Link } from 'react-router-dom';
import { Button } from '../components/ui';
import { ProsePage, ProseSection } from '../components/legal/Prose';
import { usePageMeta } from '../hooks/usePageMeta';
import { setConsent, useConsent } from '../lib/consent';

const ISSUES_URL = 'https://github.com/youssef20031/ACL_M3/issues';

function ConsentControls() {
  const consent = useConsent();
  const label =
    consent === 'accepted'
      ? 'You allowed visitor counting: your visits add your timezone and whether you visited before.'
      : consent === 'declined'
        ? 'You declined visitor counting, so your visits only add to the anonymous page-view count.'
        : "You haven't chosen yet, so your visits only add to the anonymous page-view count.";
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-data text-fg" role="status">{label}</p>
      <div className="flex shrink-0 gap-2">
        <Button variant="secondary" size="sm" onClick={() => setConsent('declined')} disabled={consent === 'declined'}>
          Decline
        </Button>
        <Button variant="secondary" size="sm" onClick={() => setConsent('accepted')} disabled={consent === 'accepted'}>
          Allow
        </Button>
      </div>
    </div>
  );
}

export function Privacy() {
  usePageMeta({
    title: 'Privacy policy',
    description: 'What Pitch Me stores (very little), what is sent to third parties, and how to change your choices.',
  });

  return (
    <ProsePage
      eyebrow="Legal"
      title="Privacy policy"
      description="Pitch Me is a fan-made Fantasy Premier League project. It has no accounts and sets no cookies. Here is everything it does with data."
      updated="1 November 2026"
    >
      <ProseSection title="Your choices">
        <p>
          Page views are counted anonymously for everyone. Visitor counting, which adds your timezone and two dates kept
          in your browser, only runs if you allow it. You can change your answer here at any time, or from{' '}
          <strong>Privacy choices</strong> in the footer. If your browser sends a Global Privacy Control signal, we treat
          it as a no.
        </p>
        <ConsentControls />
      </ProseSection>

      <ProseSection title="What we store">
        <ul>
          <li>
            <strong>In your browser (local storage), not on our servers:</strong> your assistant settings (language
            model, retrieval method), your trivia score and your privacy choice, and, only if you allow visitor
            counting, the dates of your first visit and of your latest visit. Clearing site data removes them, and
            declining visitor counting deletes the two dates.
          </li>
          <li>
            <strong>Your assistant conversation, in your browser (session storage):</strong> so it survives reloading
            the page. Your browser deletes it when you close the tab, and <strong>Clear chat</strong> deletes it
            straight away.
          </li>
          <li>
            <strong>Page views, for everyone:</strong> a count of views per page per day and the domain of the website
            that sent you (for example google.com). No IP address, device details, cookies or identifiers are stored,
            so these counts can't be linked to you. They're kept for up to 400 days.
          </li>
          <li>
            <strong>Visitors, only if you allow it:</strong> with each page view your browser also says whether it's
            your first visit and your first view that day (from the two dates above), and its timezone (for example
            Europe/London). We count visitors per day and per timezone, which tells us roughly which countries visits
            come from. Your IP address is never used to find your location. These counts are also anonymous and kept
            for up to 400 days.
          </li>
          <li>
            <strong>Technical logs:</strong> like most websites, our server's access logs record your IP address, the
            address requested and the time. We use them for security and troubleshooting only. They're capped at a
            few megabytes, so older entries are deleted automatically. IP addresses are also used briefly in memory
            to rate-limit the assistant.
          </li>
        </ul>
      </ProseSection>

      <ProseSection title="What is sent to other services">
        <ul>
          <li>
            <strong>Assistant questions</strong> (and the last few messages of the conversation, for context) are sent
            to <a href="https://groq.com/privacy-policy/" target="_blank" rel="noopener noreferrer">Groq</a>, which runs
            the language model that writes the answer. Please don't include personal information in questions. We
            don't save your questions.
          </li>
          <li>
            <strong>Player photos</strong> load straight from the Premier League's image server, which, like any
            website, can see your IP address when your browser fetches them.
          </li>
          <li>
            <strong>Saving or sharing an answer card as an image:</strong> the picture is made in your browser and
            isn't uploaded anywhere; it only goes where you share it. To include player photos, your browser fetches
            them through our server.
          </li>
          <li>
            <strong>News headlines</strong> come from The Guardian. Our server fetches the headlines; the pictures
            load straight from The Guardian&rsquo;s image server, which can see your IP address, and a headline you
            open takes you to theguardian.com, which has its own privacy policy.
          </li>
          <li>
            <strong>Your FPL team, if you import it on My team:</strong> when you enter a team ID, our server fetches
            that team's public data from the Fantasy Premier League site, the same data anyone can see there. We use it
            to show predictions and plan transfers. It isn't stored, apart from a cache of a few minutes. Your browser
            remembers the ID so you don't have to type it again, and <strong>Forget this ID</strong> on the page clears
            it. Our server's access logs record the address requested, which contains the ID.
          </li>
          <li>
            <strong>Links to GitHub</strong> take you to GitHub, which has its own privacy policy.
          </li>
        </ul>
        <p>
          Football data comes from the public Fantasy Premier League API, and goal times from ESPN, both fetched
          by our server; nothing about you is sent to either.
        </p>
      </ProseSection>

      <ProseSection title="Contact">
        <p>
          Questions about this policy? Open an issue on <a href={ISSUES_URL} target="_blank" rel="noopener noreferrer">GitHub</a>{' '}
          or see the <Link to="/contact">contact page</Link>. If this policy changes, the date at the top changes too.
        </p>
      </ProseSection>
    </ProsePage>
  );
}
