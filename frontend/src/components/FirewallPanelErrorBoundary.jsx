import { Component } from "react";

/**
 * Isolates a single Module 1.x panel so one render throw does not collapse the whole page.
 */
export class FirewallPanelErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, errorMessage: "" };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, errorMessage: error?.message || String(error) };
  }

  componentDidCatch(error, info) {
    console.error(`[FirewallPanelErrorBoundary:${this.props.title || "panel"}]`, error, info?.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, errorMessage: "" });
  };

  render() {
    if (this.state.hasError) {
      const title = this.props.title || "This panel";
      return (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-100">
          <h3 className="text-sm font-semibold">{title} could not load</h3>
          <p className="mt-1 text-xs leading-5 opacity-90">
            Other panels on this page remain available. Retry this panel or refresh the page if the issue persists.
          </p>
          {import.meta.env.DEV && this.state.errorMessage ? (
            <p className="mt-2 rounded-lg bg-amber-100/80 px-2 py-1 font-mono text-[10px] break-all dark:bg-amber-950/40">
              {this.state.errorMessage}
            </p>
          ) : null}
          <button
            type="button"
            onClick={this.handleRetry}
            className="mt-3 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700"
          >
            Retry panel
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
