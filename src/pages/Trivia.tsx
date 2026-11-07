import { useState, useCallback, useEffect, useRef } from "react";
import { AlertCircle, ArrowLeft, ArrowRight, CheckCircle2, Loader2, Play, RotateCcw, Trophy, XCircle } from "lucide-react";
import { motion, AnimatePresence, MotionConfig } from "framer-motion";
import { useAppStore } from "../store/appStore";
import { apiService, handleApiError } from "../services/api";
import type { TriviaQuestion } from "../services/api";
import {
  Button,
  Card,
  Chip,
  Container,
  ErrorState,
  PageHeader,
  SectionHeading,
  Skeleton,
  SkeletonRows,
  StatCard,
} from "../components/ui";
import { SegmentedControl } from "../components/trivia/DifficultyPicker";
import { cn } from "../utils/cn";
import { usePageMeta } from '../hooks/usePageMeta';
import { DailyChallenge } from "../components/trivia/DailyChallenge";

// ─── Types ────────────────────────────────────────────────────────────────────

type Difficulty = "easy" | "medium" | "hard";
type View = "select" | "quiz" | "results";
type AnswerState = "idle" | "correct" | "wrong";

interface QuizAnswer {
  questionText: string;
  selected: string;
  correct: string;
  isCorrect: boolean;
}

const QUESTIONS_PER_TEST = 15;

const DIFFICULTY_CONFIG: Record<Difficulty, { label: string; description: string }> = {
  easy: { label: "Easy", description: "Basic FPL facts: positions, top scorers, simple stats." },
  medium: { label: "Medium", description: "Season stats, assists, comparisons between players." },
  hard: { label: "Hard", description: "Records, gameweek scores, goalkeeper saves, tricky facts." },
};

const DIFFICULTIES: Difficulty[] = ["easy", "medium", "hard"];

const CATEGORY_LABELS: Record<string, string> = {
  top_scorers: "Top scorers",
  player_stats: "Player stats",
  team_facts: "Team facts",
  records: "Records",
  comparisons: "Comparisons",
  true_false: "True or false",
  multiple_choice: "Multiple choice",
};

const LETTERS = "ABCDEFGH";

/** The API prefixes feedback with an emoji and "Correct!"/"Wrong!"; the UI shows its own verdict. */
function feedbackDetail(feedback: string): string {
  return feedback
    // ✖️ is ✖ plus the U+FE0F emoji variation selector, so the selector is matched after the class.
    .replace(/^(?:\s|[✅❌✔✖]\uFE0F?)+/u, "")
    .replace(/^(correct|wrong)!?\s*/i, "")
    .trim();
}

function accuracy(score: number, total: number): string {
  return total > 0 ? `${Math.round((score / total) * 100)}%` : "–";
}

// ─── Start screen ─────────────────────────────────────────────────────────────

function StartScreen({
  difficulty,
  onDifficultyChange,
  onStart,
  neo4jConnected,
}: {
  difficulty: Difficulty;
  onDifficultyChange: (d: Difficulty) => void;
  onStart: (d: Difficulty) => void;
  neo4jConnected: boolean;
}) {
  return (
    <Card className="space-y-5">
      <SectionHeading
        as="h2"
        size="md"
        title="Start a quiz"
        subtitle={`${QUESTIONS_PER_TEST} questions per test, randomly drawn from the question bank.`}
        className="mb-0"
      />

      {!neo4jConnected && (
        <p role="status" className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-data text-fg-muted">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
          Connecting to the database, please wait.
        </p>
      )}

      <SegmentedControl
        label="Difficulty"
        value={difficulty}
        onChange={onDifficultyChange}
        options={DIFFICULTIES.map((d) => ({ value: d, ...DIFFICULTY_CONFIG[d] }))}
      />

      <Button
        size="lg"
        className="w-full sm:w-auto"
        onClick={() => onStart(difficulty)}
        disabled={!neo4jConnected}
        icon={<Play className="h-4 w-4" aria-hidden="true" />}
      >
        Start {DIFFICULTY_CONFIG[difficulty].label.toLowerCase()} quiz
      </Button>
    </Card>
  );
}

// ─── Answer option ────────────────────────────────────────────────────────────

function AnswerOption({
  letter,
  option,
  state,
  checking,
  disabled,
  onSelect,
}: {
  letter: string;
  option: string;
  /** Result for this option once the question has been answered. */
  state: "idle" | "correct" | "wrong" | "dimmed";
  /** This option was picked and the answer is being checked. */
  checking: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  const label =
    state === "correct" ? "Correct answer" : state === "wrong" ? "Your answer, incorrect" : checking ? "Checking" : null;

  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      className={cn(
        "flex min-h-[3.5rem] w-full items-center gap-3 rounded-md border-2 px-3 py-2.5 text-left text-body font-medium text-fg transition-colors sm:px-4",
        state === "idle" && "border-line-strong bg-surface enabled:hover:border-accent enabled:hover:bg-surface-2",
        state === "correct" && "border-accent bg-accent/10",
        state === "wrong" && "border-danger bg-danger/10",
        state === "dimmed" && "border-line bg-surface opacity-60",
        checking && "border-accent-2",
        "disabled:cursor-default",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-micro font-bold",
          state === "correct" ? "bg-accent text-accent-on" : state === "wrong" ? "bg-danger text-accent-on" : "bg-surface-2 text-fg-muted",
        )}
      >
        {letter}
      </span>
      <span className="min-w-0 flex-1">{option}</span>
      {label && (
        <span
          className={cn(
            "flex shrink-0 items-center gap-1.5 text-data font-semibold",
            state === "correct" ? "text-accent" : state === "wrong" ? "text-danger" : "text-fg-subtle",
          )}
        >
          {state === "correct" && <CheckCircle2 className="h-5 w-5" aria-hidden="true" />}
          {state === "wrong" && <XCircle className="h-5 w-5" aria-hidden="true" />}
          {checking && state === "idle" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
          <span className="hidden sm:inline">{label}</span>
          <span className="sr-only sm:hidden">{label}</span>
        </span>
      )}
    </button>
  );
}

// ─── Quiz Screen ──────────────────────────────────────────────────────────────

function QuizScreen({
  difficulty,
  onAnswered,
  onFinish,
}: {
  difficulty: Difficulty;
  onAnswered: (isCorrect: boolean) => void;
  onFinish: (answers: QuizAnswer[], score: number) => void;
}) {
  const [questionNumber, setQuestionNumber] = useState(1);
  const [currentQuestion, setCurrentQuestion] = useState<TriviaQuestion | null>(null);
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null);
  const [answerState, setAnswerState] = useState<AnswerState>("idle");
  const [feedback, setFeedback] = useState("");
  const [correctAnswer, setCorrectAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [answers, setAnswers] = useState<QuizAnswer[]>([]);
  const [score, setScore] = useState(0);
  const [answering, setAnswering] = useState(false);
  const nextRef = useRef<HTMLButtonElement | null>(null);
  const fetchSeq = useRef(0);

  const cfg = DIFFICULTY_CONFIG[difficulty];
  const isMounted = useRef(true);
  useEffect(() => {
    isMounted.current = true;
    return () => { isMounted.current = false; };
  }, []);

  const fetchQuestion = useCallback(async () => {
    setLoading(true);
    setError("");
    setSelectedAnswer(null);
    setAnswerState("idle");
    setFeedback("");
    setCorrectAnswer("");
    // Only the latest request may set the question (StrictMode's double mount fires two).
    const seq = ++fetchSeq.current;
    const isLatest = () => isMounted.current && seq === fetchSeq.current;
    try {
      const q = await apiService.getNewTriviaQuestion(difficulty);
      if (isLatest()) setCurrentQuestion(q);
    } catch (e) {
      if (isLatest()) setError(handleApiError(e));
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [difficulty]);

  // Load first question on mount
  useEffect(() => { fetchQuestion(); }, [fetchQuestion]);

  // The answer buttons disable once answered; move keyboard focus to "Next question".
  useEffect(() => {
    if (answerState !== "idle") nextRef.current?.focus();
  }, [answerState]);

  const handleAnswer = async (option: string) => {
    if (answerState !== "idle" || !currentQuestion || answering) return;
    setSelectedAnswer(option);
    setAnswering(true);
    try {
      const result = await apiService.checkTriviaAnswer(currentQuestion.question_id, option);
      if (!isMounted.current) return;
      const isCorrect = result.correct;
      setAnswerState(isCorrect ? "correct" : "wrong");
      setFeedback(result.feedback || (isCorrect ? "Correct!" : "Wrong!"));
      setCorrectAnswer(result.correct_answer || "");
      const newAnswer: QuizAnswer = {
        questionText: currentQuestion.question,
        selected: option,
        correct: result.correct_answer || option,
        isCorrect,
      };
      const newAnswers = [...answers, newAnswer];
      const newScore = score + (isCorrect ? 1 : 0);
      setAnswers(newAnswers);
      setScore(newScore);
      onAnswered(isCorrect);

      // If last question, finish after short delay
      if (questionNumber >= QUESTIONS_PER_TEST) {
        setTimeout(() => { if (isMounted.current) onFinish(newAnswers, newScore); }, 1800);
      }
    } catch (e) {
      if (isMounted.current) setError(handleApiError(e));
    } finally {
      if (isMounted.current) setAnswering(false);
    }
  };

  const handleNext = () => {
    if (questionNumber >= QUESTIONS_PER_TEST) return;
    setQuestionNumber((n) => n + 1);
    fetchQuestion();
  };

  const progress = (answers.length / QUESTIONS_PER_TEST) * 100;
  const detail = feedbackDetail(feedback);

  const optionState = (option: string): "idle" | "correct" | "wrong" | "dimmed" => {
    if (answerState === "idle") return "idle";
    const isSelected = option === selectedAnswer;
    if ((isSelected && answerState === "correct") || (answerState === "wrong" && option === correctAnswer)) return "correct";
    if (isSelected && answerState === "wrong") return "wrong";
    return "dimmed";
  };

  return (
    <div className="space-y-4">
      <Card padded={false} className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
          <p className="text-body font-semibold text-fg">
            Question {questionNumber} <span className="font-normal text-fg-subtle">of {QUESTIONS_PER_TEST}</span>
          </p>
          <Chip tone="brand">{cfg.label}</Chip>
        </div>
        <div
          role="progressbar"
          aria-label="Questions answered"
          aria-valuemin={0}
          aria-valuemax={QUESTIONS_PER_TEST}
          aria-valuenow={answers.length}
          className="h-1.5 bg-surface-2"
        >
          <div className="h-full bg-accent transition-[width] duration-300" style={{ width: `${progress}%` }} />
        </div>
      </Card>

      {error && (
        <Card>
          <ErrorState title="Something went wrong" description={error} onRetry={fetchQuestion} className="py-6" />
        </Card>
      )}

      {loading && (
        <div role="status" className="space-y-3">
          <span className="sr-only">Loading question…</span>
          <Card tone="brand" className="space-y-3">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-6 w-full" />
          </Card>
          <SkeletonRows rows={4} className="[&>div]:h-14" />
        </div>
      )}

      <AnimatePresence mode="wait">
        {currentQuestion && !loading && (
          <motion.section
            key={currentQuestion.question_id}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            aria-labelledby="trivia-question"
            className="space-y-3"
          >
            <Card tone="brand">
              <Chip tone="accent">{CATEGORY_LABELS[currentQuestion.category] ?? currentQuestion.category}</Chip>
              <h2 id="trivia-question" className="mt-2 font-display text-h3 font-bold leading-snug text-fg sm:text-h2">
                {currentQuestion.question}
              </h2>
            </Card>

            <ul className="grid gap-2" aria-label="Answers">
              {currentQuestion.options.map((option, i) => (
                <li key={`${i}-${option}`}>
                  <AnswerOption
                    letter={LETTERS[i] ?? String(i + 1)}
                    option={option}
                    state={optionState(option)}
                    checking={answering && option === selectedAnswer}
                    disabled={answerState !== "idle" || answering}
                    onSelect={() => handleAnswer(option)}
                  />
                </li>
              ))}
            </ul>
          </motion.section>
        )}
      </AnimatePresence>

      {/* Live region stays mounted so the verdict is announced when it appears. */}
      <div aria-live="polite" role="status">
        {answerState !== "idle" && !loading && (
          <div
            className={cn(
              "flex items-start gap-3 rounded-md border p-3 sm:p-4",
              answerState === "correct" ? "border-accent/40 bg-accent/10" : "border-danger/40 bg-danger/10",
            )}
          >
            {answerState === "correct" ? (
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-accent" aria-hidden="true" />
            ) : (
              <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-danger" aria-hidden="true" />
            )}
            <div className="min-w-0">
              <p className={cn("font-display text-h3 font-bold", answerState === "correct" ? "text-accent" : "text-danger")}>
                {answerState === "correct" ? "Correct" : "Not quite"}
              </p>
              {answerState === "wrong" && correctAnswer && !detail.toLowerCase().includes(correctAnswer.toLowerCase()) && (
                <p className="mt-0.5 text-data text-fg">
                  The answer was <strong>{correctAnswer}</strong>.
                </p>
              )}
              {detail && <p className="mt-0.5 text-data text-fg-muted">{detail}</p>}
            </div>
          </div>
        )}
      </div>

      {/* Next button — only show if NOT the last question */}
      {answerState !== "idle" && !loading && questionNumber < QUESTIONS_PER_TEST && (
        <Button ref={nextRef} size="lg" className="w-full" onClick={handleNext}>
          Next question
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      )}

      {/* Last question — show finishing message */}
      {answerState !== "idle" && questionNumber >= QUESTIONS_PER_TEST && (
        <p className="flex items-center justify-center gap-2 py-2 text-data text-fg-muted">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading your results…
        </p>
      )}
    </div>
  );
}

// ─── Results Screen ───────────────────────────────────────────────────────────

function ResultsScreen({
  difficulty,
  score,
  answers,
  onFinish,
}: {
  difficulty: Difficulty;
  score: number;
  answers: QuizAnswer[];
  onFinish: () => void;
}) {
  const total = QUESTIONS_PER_TEST;
  const pct = Math.round((score / total) * 100);
  const cfg = DIFFICULTY_CONFIG[difficulty];

  const grade =
    pct >= 90 ? "Outstanding" :
    pct >= 70 ? "Great job" :
    pct >= 50 ? "Not bad" :
    "Keep practising";

  return (
    <div className="space-y-4">
      <Card tone="brand" className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-md bg-accent text-accent-on">
            <Trophy className="h-6 w-6" aria-hidden="true" />
          </span>
          <div>
            <h2 className="font-display text-h2 font-bold text-fg">{grade}</h2>
            <p className="text-data text-fg-muted">
              {score} of {total} correct on {cfg.label.toLowerCase()} ({pct}%)
            </p>
          </div>
        </div>
        <Button onClick={onFinish} icon={<RotateCcw className="h-4 w-4" aria-hidden="true" />} className="w-full sm:w-auto">
          Finish
        </Button>
      </Card>

      <Card padded={false}>
        <div className="px-4 pt-4 sm:px-5">
          <SectionHeading as="h2" size="md" title="Review" subtitle="Every question from this quiz" />
        </div>
        <ol className="divide-y divide-line border-t border-line">
          {answers.map((a, i) => (
            <li key={i} className="flex items-start gap-3 px-4 py-3 sm:px-5">
              {a.isCorrect ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
              ) : (
                <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
              )}
              <div className="min-w-0 flex-1">
                <p className="text-data font-medium text-fg">
                  <span className="text-fg-subtle">{i + 1}.</span> {a.questionText}
                </p>
                {a.isCorrect ? (
                  <p className="mt-0.5 text-data text-fg-muted">
                    <span className="font-semibold text-accent">Correct:</span> {a.selected}
                  </p>
                ) : (
                  <p className="mt-0.5 text-data text-fg-muted">
                    <span className="font-semibold text-danger">Your answer:</span> {a.selected}
                    <span className="text-fg-subtle"> · </span>
                    <span className="font-semibold text-accent">Correct:</span> {a.correct}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}

// ─── Main Trivia Page ─────────────────────────────────────────────────────────

export function Trivia() {
  usePageMeta({ title: "Trivia", description: "Test your Fantasy Premier League knowledge with quiz questions built from real FPL data." });
  const { neo4jConnected, triviaScore, triviaTotal, incrementScore, incrementTotal, resetTriviaScore } = useAppStore();

  const [view, setView] = useState<View>("select");
  const [difficulty, setDifficulty] = useState<Difficulty>("easy");
  const [quizAnswers, setQuizAnswers] = useState<QuizAnswer[]>([]);
  const [quizScore, setQuizScore] = useState(0);
  // Running totals for the current quiz (the score cards update after every answer).
  const [liveScore, setLiveScore] = useState(0);
  const [liveAnswered, setLiveAnswered] = useState(0);

  const handleSelectDifficulty = (d: Difficulty) => {
    setDifficulty(d);
    setQuizAnswers([]);
    setQuizScore(0);
    setLiveScore(0);
    setLiveAnswered(0);
    setView("quiz");
  };

  const handleAnswered = (isCorrect: boolean) => {
    setLiveAnswered((n) => n + 1);
    incrementTotal();
    if (isCorrect) {
      setLiveScore((n) => n + 1);
      incrementScore();
    }
  };

  const handleQuizFinish = (answers: QuizAnswer[], score: number) => {
    setQuizAnswers(answers);
    setQuizScore(score);
    setView("results");
  };

  const handleReturnToSelect = () => {
    setView("select");
  };

  const inQuiz = view !== "select";
  const stats = inQuiz
    ? [
        { label: "Score", value: liveScore, detail: "This quiz", highlight: true },
        { label: "Answered", value: `${liveAnswered}/${QUESTIONS_PER_TEST}`, detail: DIFFICULTY_CONFIG[difficulty].label },
        { label: "Accuracy", value: accuracy(liveScore, liveAnswered), detail: "This quiz" },
      ]
    : [
        { label: "Score", value: triviaScore, detail: "All time", highlight: true },
        { label: "Answered", value: triviaTotal, detail: "All time" },
        { label: "Accuracy", value: accuracy(triviaScore, triviaTotal), detail: "All time" },
      ];

  return (
    <MotionConfig reducedMotion="user">
      {/* One centred column: header, score and quiz share the same width */}
      <Container className="max-w-3xl pb-10">
        <PageHeader
          eyebrow="Quiz"
          title="Trivia"
          description="Test your Fantasy Premier League knowledge against questions built from real FPL data."
          actions={
            inQuiz ? (
              <Button variant="secondary" size="sm" onClick={handleReturnToSelect} icon={<ArrowLeft className="h-4 w-4" aria-hidden="true" />}>
                Back
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                onClick={resetTriviaScore}
                disabled={triviaTotal === 0}
                icon={<RotateCcw className="h-4 w-4" aria-hidden="true" />}
              >
                Reset score
              </Button>
            )
          }
        />

        <div className="space-y-4">
          <section aria-label={inQuiz ? "Quiz score" : "All-time score"} className="grid grid-cols-3 gap-2 sm:gap-3">
            {stats.map((s) => (
              <StatCard
                key={s.label}
                label={s.label}
                value={<span className="text-h2 sm:text-h1">{s.value}</span>}
                detail={s.detail}
                highlight={s.highlight}
                className="p-3 sm:p-4"
              />
            ))}
          </section>

          {view === "select" && <DailyChallenge />}

          {view === "select" && (
            <StartScreen
              difficulty={difficulty}
              onDifficultyChange={setDifficulty}
              onStart={handleSelectDifficulty}
              neo4jConnected={neo4jConnected}
            />
          )}

          {view === "quiz" && (
            <QuizScreen difficulty={difficulty} onAnswered={handleAnswered} onFinish={handleQuizFinish} />
          )}

          {view === "results" && (
            <ResultsScreen
              difficulty={difficulty}
              score={quizScore}
              answers={quizAnswers}
              onFinish={handleReturnToSelect}
            />
          )}
        </div>
      </Container>
    </MotionConfig>
  );
}
