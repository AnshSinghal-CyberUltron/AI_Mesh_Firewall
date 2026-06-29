import { cn } from "../../../lib/utils";

function Skeleton({ className }) {
  return (
    <div className={cn("fw-shimmer-block relative rounded-md", className)}>
      <div className="absolute inset-0" />
    </div>
  );
}

export function SectionSkeleton({ rows = 3 }) {
  return (
    <div className="rounded-xl border border-border bg-card p-5 sm:p-6">
      <div className="flex items-start gap-3 border-b border-border/60 pb-4">
        <Skeleton className="h-9 w-9" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-3 w-72 max-w-full" />
        </div>
      </div>
      <div className="space-y-3 pt-5">
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    </div>
  );
}
