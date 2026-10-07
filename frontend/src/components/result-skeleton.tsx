import { Skeleton } from "@/components/ui/skeleton";

export function ResultSkeleton({ caption }: { caption: string }) {
  return (
    <div role="status" aria-live="polite" aria-label="Loading the answer" className="flex flex-col gap-4">
      <Skeleton className="h-6 w-2/3" />
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="mt-2 h-80 w-full" />
      <span className="text-xs text-muted-foreground">{caption}</span>
    </div>
  );
}
