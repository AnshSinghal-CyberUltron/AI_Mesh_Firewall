import { useState } from "react";
import { Database, Upload, Search, Shield, ChevronDown, ChevronUp, CheckCircle, Circle } from "lucide-react";

const STEPS = [
  {
    id: 1,
    icon: Database,
    title: "Configure Provider",
    panel: "Vector Provider Config",
    description: "Set up your BYOK vector database (Pinecone, Milvus, Chroma, or Custom), embedding key, and optional reranker.",
    tab: "control",
  },
  {
    id: 2,
    icon: Database,
    title: "Create Collection",
    panel: "Collection Manager",
    description: "Create a named collection in your chosen vector DB provider.",
    tab: "control",
  },
  {
    id: 3,
    icon: Upload,
    title: "Ingest Documents",
    panel: "RAG Document Ingestion",
    description: "Upload documents or load samples into your collection.",
    tab: "control",
  },
  {
    id: 4,
    icon: Search,
    title: "Query & Test",
    panel: "Free Query / Attack & Trust Simulator",
    description: "Run free-form queries, feature tests, and attack/trust scenarios through the security pipeline.",
    tab: "simulator",
  },
];

/**
 * A collapsible quick-start guide shown at the top of the RAG & Vector DB Firewall page.
 * Helps users understand the ingest → query workflow.
 */
export function RAGSetupGuide() {
  const [expanded, setExpanded] = useState(true);
  const [completedSteps, setCompletedSteps] = useState(new Set());

  const toggleStep = (id) => {
    setCompletedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-indigo-900/10 dark:to-purple-900/10 border border-indigo-200 dark:border-indigo-800/40 rounded-xl shadow-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-5 py-3"
      >
        <div className="flex items-center gap-3">
          <Shield className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
          <div className="text-left">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              RAG Pipeline Quick Start
            </h3>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              {completedSteps.size}/{STEPS.length} steps completed — Follow this flow to see real documents in simulators
            </p>
          </div>
        </div>
        {expanded ? (
          <ChevronUp className="w-4 h-4 text-slate-400" />
        ) : (
          <ChevronDown className="w-4 h-4 text-slate-400" />
        )}
      </button>

      {expanded && (
        <div className="px-5 pb-4">
          <div className="flex items-start gap-0">
            {STEPS.map((step, i) => {
              const done = completedSteps.has(step.id);
              const Icon = step.icon;
              return (
                <div key={step.id} className="flex-1 flex flex-col items-center text-center relative">
                  {/* Connector line */}
                  {i < STEPS.length - 1 && (
                    <div className="absolute top-5 left-1/2 w-full h-px bg-slate-300 dark:bg-slate-600 z-0" />
                  )}
                  {/* Step circle */}
                  <button
                    onClick={() => toggleStep(step.id)}
                    className={`relative z-10 w-10 h-10 rounded-full flex items-center justify-center border-2 transition-all ${
                      done
                        ? "bg-emerald-100 dark:bg-emerald-900/30 border-emerald-500 text-emerald-600 dark:text-emerald-400"
                        : "bg-white dark:bg-slate-800 border-slate-300 dark:border-slate-600 text-slate-500 dark:text-slate-400 hover:border-indigo-400"
                    }`}
                    aria-label={done ? `Mark step "${step.title}" as not done` : `Mark step "${step.title}" as done`}
                    aria-pressed={done}
                    title={done ? "Mark undone" : "Mark as done"}
                  >
                    {done ? <CheckCircle className="w-5 h-5" /> : <Icon className="w-4 h-4" />}
                  </button>
                  <div className="mt-2 px-1">
                    <p className={`text-xs font-medium ${done ? "text-emerald-600 dark:text-emerald-400" : "text-slate-800 dark:text-slate-200"}`}>
                      {step.title}
                    </p>
                    <p className="text-[10px] text-slate-500 dark:text-slate-400 mt-0.5 leading-relaxed">
                      {step.description}
                    </p>
                    <span className="inline-block mt-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                      {step.tab} tab → {step.panel}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
