import { lazy, Suspense, useEffect, type ReactNode } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from './components/shell/AppShell';
import { NotFound } from './pages/NotFound';
import { PageSkeleton } from './components/shell/PageSkeleton';

// Route-level code splitting: recharts (Search/Compare), react-markdown + framer-motion (Q&A/Trivia)
// are only downloaded with the page that needs them.
const loadQAAssistant = () => import('./pages/QAAssistant').then((m) => ({ default: m.QAAssistant }));
const loadTrivia = () => import('./pages/Trivia').then((m) => ({ default: m.Trivia }));
const loadPlayerSearch = () => import('./pages/PlayerSearch').then((m) => ({ default: m.PlayerSearch }));
const loadPlayerComparison = () => import('./pages/PlayerComparison').then((m) => ({ default: m.PlayerComparison }));
const loadPredictions = () => import('./pages/Predictions').then((m) => ({ default: m.Predictions }));
const loadSettings = () => import('./pages/Settings').then((m) => ({ default: m.Settings }));
const loadHome = () => import('./pages/Home').then((m) => ({ default: m.Home }));
const loadFixtures = () => import('./pages/Fixtures').then((m) => ({ default: m.Fixtures }));
const loadLeagueTable = () => import('./pages/LeagueTable').then((m) => ({ default: m.LeagueTable }));
const loadTeamOfTheWeek = () => import('./pages/TeamOfTheWeek').then((m) => ({ default: m.TeamOfTheWeek }));
const loadModelInsights = () => import('./pages/ModelInsights').then((m) => ({ default: m.ModelInsights }));
const loadPrivacy = () => import('./pages/Privacy').then((m) => ({ default: m.Privacy }));
const loadTerms = () => import('./pages/Terms').then((m) => ({ default: m.Terms }));
const loadContact = () => import('./pages/Contact').then((m) => ({ default: m.Contact }));
const loadTeams = () => import('./pages/Teams').then((m) => ({ default: m.Teams }));
const loadTeamPage = () => import('./pages/TeamPage').then((m) => ({ default: m.TeamPage }));
const loadMatchPage = () => import('./pages/MatchPage').then((m) => ({ default: m.MatchPage }));
const loadSiteStats = () => import('./pages/SiteStats').then((m) => ({ default: m.SiteStats }));
const loadDesignSystem = () => import('./pages/DesignSystem').then((m) => ({ default: m.DesignSystem }));

const QAAssistant = lazy(loadQAAssistant);
const Trivia = lazy(loadTrivia);
const PlayerSearch = lazy(loadPlayerSearch);
const PlayerComparison = lazy(loadPlayerComparison);
const Predictions = lazy(loadPredictions);
const Settings = lazy(loadSettings);
const Home = lazy(loadHome);
const Fixtures = lazy(loadFixtures);
const LeagueTable = lazy(loadLeagueTable);
const TeamOfTheWeek = lazy(loadTeamOfTheWeek);
const ModelInsights = lazy(loadModelInsights);
const Privacy = lazy(loadPrivacy);
const Terms = lazy(loadTerms);
const Contact = lazy(loadContact);
const SiteStats = lazy(loadSiteStats);
const Teams = lazy(loadTeams);
const TeamPage = lazy(loadTeamPage);
const MatchPage = lazy(loadMatchPage);
const DesignSystem = lazy(loadDesignSystem);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 5 * 60 * 1000, // 5 minutes
    },
  },
});

// Old URLs (/qa, /search) keep working; router state (e.g. a selected player) is carried over.
function Redirect({ to }: { to: string }) {
  const location = useLocation();
  return <Navigate to={to} replace state={location.state} />;
}

// Suspense boundary per route so the Layout (sidebar/header) stays mounted while a page chunk loads.
function Page({ children }: { children: ReactNode }) {
  return <Suspense fallback={<PageSkeleton />}>{children}</Suspense>;
}

function App() {
  // Warm the remaining route chunks once the browser is idle so later navigation is instant.
  useEffect(() => {
    const preload = () => {
      [loadHome, loadFixtures, loadLeagueTable, loadTeams, loadTeamPage, loadMatchPage, loadTeamOfTheWeek, loadQAAssistant, loadPredictions, loadModelInsights, loadPlayerSearch, loadPlayerComparison, loadTrivia, loadSettings].forEach((load) => {
        void load().catch(() => undefined);
      });
    };
    const w = window as Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(preload, { timeout: 4000 });
      return () => w.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(preload, 2000);
    return () => window.clearTimeout(id);
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <Routes>
          {/* Hidden design-system reference (not linked from the nav). */}
          <Route path="/design" element={<Page><DesignSystem /></Page>} />
          <Route path="/" element={<AppShell />}>
            <Route index element={<Page><Home /></Page>} />
            <Route path="fixtures" element={<Page><Fixtures /></Page>} />
            <Route path="table" element={<Page><LeagueTable /></Page>} />
            <Route path="teams" element={<Page><Teams /></Page>} />
            <Route path="teams/:slug" element={<Page><TeamPage /></Page>} />
            <Route path="matches/:id" element={<Page><MatchPage /></Page>} />
            <Route path="team-of-the-week" element={<Page><TeamOfTheWeek /></Page>} />
            <Route path="assistant" element={<Page><QAAssistant /></Page>} />
            <Route path="players" element={<Page><PlayerSearch /></Page>} />
            <Route path="predictions" element={<Page><Predictions /></Page>} />
            <Route path="model" element={<Page><ModelInsights /></Page>} />
            <Route path="compare" element={<Page><PlayerComparison /></Page>} />
            <Route path="trivia" element={<Page><Trivia /></Page>} />
            <Route path="settings" element={<Page><Settings /></Page>} />
            <Route path="privacy" element={<Page><Privacy /></Page>} />
            <Route path="terms" element={<Page><Terms /></Page>} />
            <Route path="contact" element={<Page><Contact /></Page>} />
            <Route path="stats" element={<Page><SiteStats /></Page>} />
            <Route path="qa" element={<Redirect to="/assistant" />} />
            <Route path="search" element={<Redirect to="/players" />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </Router>
    </QueryClientProvider>
  );
}

export default App;
