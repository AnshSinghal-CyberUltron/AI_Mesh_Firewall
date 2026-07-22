import { Component } from "react";

/**
 * Catches failed React.lazy() chunk loads so Suspense does not sit on a spinner forever.
 */
export class LazyRouteErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("[LazyRouteErrorBoundary]", error, info);
  }

  handleRetry = () => {
    this.setState({ error: null });
    if (typeof this.props.onRetry === "function") {
      this.props.onRetry();
    } else {
      window.location.reload();
    }
  };

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3 px-4 text-center">
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
            This page failed to load
          </p>
          <p className="max-w-md text-xs text-slate-500 dark:text-slate-400">
            {this.state.error?.message || "The module chunk could not be downloaded. Retry or refresh."}
          </p>
          <button
            type="button"
            onClick={this.handleRetry}
            className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700"
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
