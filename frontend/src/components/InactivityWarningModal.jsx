import { Button } from "./ui/Button";

export function InactivityWarningModal({
  open,
  onStayLoggedIn,
  message = "You've been inactive. You'll be logged out in 1 minute.",
  buttonText = "Stay logged in",
}) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="inactivity-warning-title"
    >
      <div className="mx-4 w-full max-w-md rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-6 shadow-xl">
        <h2
          id="inactivity-warning-title"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100"
        >
          Session expiring
        </h2>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400 dark:text-slate-300">{message}</p>
        <div className="mt-6 flex justify-end">
          <Button onClick={onStayLoggedIn} variant="default" size="default">
            {buttonText}
          </Button>
        </div>
      </div>
    </div>
  );
}
