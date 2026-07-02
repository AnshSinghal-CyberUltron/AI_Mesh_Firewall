/**
 * HowToUse — per-submodule interactive getting-started card.
 *
 * Props:
 *   moduleId   – "1.1" … "1.7"  (looks up content from howToUseContent.js)
 *   defaultOpen – boolean, default false
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ClipboardCheck,
  Copy,
  Info,
  Shield,
  AlertTriangle,
} from "lucide-react";
import { HOW_TO_USE } from "./howToUseContent";

// ── helpers ──────────────────────────────────────────────────────────────────

function cn(...args) {
  return args.filter(Boolean).join(" ");
}

const NOTE_ICON = {
  shield: Shield,
  info: Info,
  warning: AlertTriangle,
};

const LANG_LABEL_COLOR = {
  bash: "text-amber-400",
  python: "text-sky-400",
  javascript: "text-yellow-400",
  json: "text-emerald-400",
};

// Very lightweight tokeniser — highlights strings, comments, and keywords
// without a heavy dependency.
function tokenise(code, language) {
  if (!code) return [];
  const lines = code.split("\n");
  return lines.map((line, li) => {
    const tokens = [];

    if (language === "bash") {
      if (line.trimStart().startsWith("#")) {
        tokens.push({ type: "comment", text: line });
      } else {
        // flag-like params: --foo
        let rest = line;
        const parts = rest.split(/(\s+|(?=--[a-z])|(?<=--[a-z\-]+))/g);
        tokens.push({ type: "plain", text: rest });
      }
    } else if (language === "json") {
      // comments (// …) or JSON string properties
      if (line.trimStart().startsWith("//")) {
        tokens.push({ type: "comment", text: line });
      } else {
        tokens.push({ type: "plain", text: line });
      }
    } else {
      // python / javascript — detect comments and strings at line level
      const match = line.match(/^(.*?)(#.*|\/\/.*)$/);
      if (match) {
        tokens.push({ type: "plain", text: match[1] });
        tokens.push({ type: "comment", text: match[2] });
      } else {
        tokens.push({ type: "plain", text: line });
      }
    }
    return { line: li, tokens };
  });
}

// ── CopyButton ────────────────────────────────────────────────────────────────

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef(null);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      timerRef.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard may not be available
    }
  }, [text]);

  useEffect(() => () => clearTimeout(timerRef.current), []);

  return (
    <button
      onClick={handleCopy}
      title="Copy to clipboard"
      className={cn(
        "group inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-all duration-200",
        copied
          ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-400"
          : "border-slate-600 bg-slate-700/60 text-slate-400 hover:border-slate-500 hover:bg-slate-700 hover:text-slate-200",
      )}
    >
      {copied ? (
        <>
          <ClipboardCheck className="h-3.5 w-3.5" />
          Copied!
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" />
          Copy
        </>
      )}
    </button>
  );
}

// ── CodeBlock ─────────────────────────────────────────────────────────────────

function CodeBlock({ code, language }) {
  const lines = tokenise(code, language);

  return (
    <div className="relative overflow-hidden rounded-2xl bg-slate-950 border border-slate-700/60 shadow-xl">
      {/* Lang pill + copy */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-slate-800">
        <span className={cn("text-xs font-mono font-semibold", LANG_LABEL_COLOR[language] ?? "text-slate-400")}>
          {language}
        </span>
        <CopyButton text={code} />
      </div>

      {/* Code area */}
      <div className="overflow-x-auto p-5">
        <pre className="text-sm font-mono leading-6 text-slate-300 whitespace-pre">
          {lines.map(({ line, tokens }) => (
            <span key={line} className="block">
              {/* Line number */}
              <span className="select-none text-slate-600 text-xs mr-4 w-6 inline-block text-right">
                {line + 1}
              </span>
              {tokens.map((tok, ti) => (
                <span
                  key={ti}
                  className={
                    tok.type === "comment"
                      ? "text-slate-500 italic"
                      : "text-slate-200"
                  }
                >
                  {tok.text}
                </span>
              ))}
            </span>
          ))}
        </pre>
      </div>
    </div>
  );
}

// ── StepList ──────────────────────────────────────────────────────────────────

function StepList({ steps }) {
  return (
    <ol className="space-y-4">
      {steps.map((step, idx) => (
        <li key={step.id} className="flex gap-4">
          <div className="flex-shrink-0 flex h-8 w-8 items-center justify-center rounded-full bg-teal-500/15 border border-teal-500/30 text-teal-600 dark:text-teal-400 text-sm font-bold">
            {idx + 1}
          </div>
          <div className="min-w-0 pt-0.5">
            <div className="text-sm font-semibold text-slate-800 dark:text-slate-100 leading-5 break-words">
              {step.label}
            </div>
            <div className="mt-1 text-sm leading-6 text-slate-600 dark:text-slate-400 break-words">
              {step.text}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

// ── CodeTabs ─────────────────────────────────────────────────────────────────

function CodeTabs({ tabs }) {
  const [active, setActive] = useState(tabs[0]?.id ?? "");
  const current = tabs.find((t) => t.id === active) ?? tabs[0];

  return (
    <div className="space-y-3">
      {/* Tab strip */}
      <div className="flex flex-wrap gap-1.5">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActive(tab.id)}
            className={cn(
              "rounded-xl px-3.5 py-1.5 text-xs font-medium transition-colors",
              active === tab.id
                ? "bg-teal-600 dark:bg-teal-500 text-white"
                : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Code block */}
      {current && <CodeBlock code={current.code} language={current.language} />}
    </div>
  );
}

// ── NoteRow ───────────────────────────────────────────────────────────────────

function NoteRow({ notes }) {
  if (!notes || notes.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
      {notes.map((note, i) => {
        const Icon = NOTE_ICON[note.icon] ?? Info;
        const colorMap = {
          shield: "bg-teal-50 dark:bg-teal-900/20 border-teal-200 dark:border-teal-700/50 text-teal-700 dark:text-teal-300",
          info: "bg-sky-50 dark:bg-sky-900/20 border-sky-200 dark:border-sky-700/50 text-sky-700 dark:text-sky-300",
          warning: "bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-700/50 text-amber-700 dark:text-amber-300",
        };
        const cls = colorMap[note.icon] ?? colorMap.info;
        return (
          <div
            key={i}
            className={cn(
              "flex items-start gap-2.5 rounded-2xl border px-4 py-3 text-xs leading-5 flex-1",
              cls,
            )}
          >
            <Icon className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
            <span>{note.text}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function HowToUse({ moduleId, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const content = HOW_TO_USE[moduleId];

  if (!content) return null;

  return (
    <div
      className={cn(
        "ai-mesh-card rounded-[30px] border overflow-hidden transition-all duration-300",
        "border-teal-200/60 dark:border-teal-700/30",
        "bg-gradient-to-br from-white/90 via-teal-50/20 to-white/90",
        "dark:from-slate-900/90 dark:via-teal-900/10 dark:to-slate-900/90",
      )}
    >
      {/* ── Header (always visible) ── */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-4 px-7 py-5 text-left hover:bg-teal-50/40 dark:hover:bg-teal-900/10 transition-colors"
      >
        <div className="flex items-center gap-4 min-w-0">
          <div className="flex h-10 w-10 items-center justify-center rounded-[14px] bg-gradient-to-br from-teal-500 to-cyan-500 text-white shadow-lg shadow-teal-500/20 flex-shrink-0">
            <BookOpen className="h-5 w-5" strokeWidth={2.2} />
          </div>
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-400">
              Getting Started
            </div>
            <h3 className="mt-0.5 text-lg font-semibold text-slate-900 dark:text-slate-50 leading-snug">
              {content.title}
            </h3>
            <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              {content.audience}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {!open && (
            <span className="hidden sm:inline-flex items-center gap-1.5 rounded-full border border-teal-200 dark:border-teal-700 bg-teal-50 dark:bg-teal-900/30 px-3 py-1 text-xs font-medium text-teal-700 dark:text-teal-300">
              <CheckCircle2 className="h-3 w-3" />
              View guide
            </span>
          )}
          <div className="rounded-full border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-1.5 text-slate-500 dark:text-slate-400">
            {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </div>
        </div>
      </button>

      {/* ── Body (collapsible) ── */}
      {open && (
        <div className="px-7 pb-7 pt-2 space-y-8 border-t border-teal-100/50 dark:border-teal-800/30">
          {/* Intro paragraph */}
          <p className="text-sm leading-7 text-slate-600 dark:text-slate-300 max-w-3xl">
            {content.intro}
          </p>

          {/* Steps + Code side-by-side on lg screens. grid-cols-1 base = minmax(0,1fr)
              and min-w-0 on each column so a long step line / code line cannot
              blow the column out to max-content (grid items default to min-width:auto,
              which the card's overflow-hidden then clips at narrow widths). */}
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
            {/* Left: numbered steps */}
            <div className="min-w-0">
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-400 mb-4">
                Step-by-step
              </div>
              <StepList steps={content.steps} />
            </div>

            {/* Right: code tabs */}
            {content.codeTabs && content.codeTabs.length > 0 && (
              <div className="min-w-0">
                <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-400 mb-4">
                  Code examples
                </div>
                <CodeTabs tabs={content.codeTabs} />
              </div>
            )}
          </div>

          {/* Notes row */}
          {content.notes && content.notes.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-400 mb-3">
                Notes
              </div>
              <NoteRow notes={content.notes} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
