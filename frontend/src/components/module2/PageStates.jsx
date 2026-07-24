import { Component } from "react";

export class Module2PageErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, errorMessage: "" };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, errorMessage: error?.message || String(error) };
  }

  componentDidCatch(error, info) {
    console.error("[Module2PageErrorBoundary]", error, info?.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, errorMessage: "" });
  };

  render() {
    if (this.state.hasError) {
      const title = this.props.title || "This analyst view failed to render";
      return (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">
          <h2 className="text-base font-semibold">{title}</h2>
          <p className="mt-2">
            A chart or triage panel threw an error while loading. The rest of the application remains usable,
            and this screen no longer blanks out during incident review.
          </p>
          {import.meta.env.DEV && this.state.errorMessage && (
            <p className="mt-3 rounded-lg bg-red-100/80 px-3 py-2 font-mono text-xs text-red-900 dark:bg-red-950/50 dark:text-red-100">
              {this.state.errorMessage}
            </p>
          )}
          <button
            type="button"
            onClick={this.handleRetry}
            className="mt-4 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export function Module2PageSkeleton({ rows = 4 }) {
  return (
    <div className="animate-pulse space-y-4">
      <div className="h-10 w-64 rounded-lg bg-slate-200 dark:bg-slate-700" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-24 rounded-xl bg-slate-200 dark:bg-slate-700" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="h-64 rounded-xl bg-slate-200 dark:bg-slate-700" />
        <div className="h-64 rounded-xl bg-slate-200 dark:bg-slate-700" />
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={`row-${i}`} className="h-8 rounded bg-slate-100 dark:bg-slate-800" />
      ))}
    </div>
  );
}

export function Module2EmptyState({ title, message, hint, action }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center dark:border-slate-600 dark:bg-slate-900/40">
      <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{title || "No data yet"}</p>
      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{message}</p>
      {hint && <p className="mt-3 text-xs text-teal-700 dark:text-teal-400">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Module2ErrorState({ message, onRetry }) {
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 px-6 py-10 text-center dark:border-red-900 dark:bg-red-950/40">
      <p className="text-sm font-medium text-red-700 dark:text-red-300">{message || "Failed to load data."}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-lg bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700"
        >
          Retry
        </button>
      )}
    </div>
  );
}
