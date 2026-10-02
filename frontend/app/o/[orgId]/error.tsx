"use client";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";

/** Errors thrown by a page inside the organization shell (the shell itself stays visible). */
export default function PageError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="flex flex-col items-start gap-3">
      <Notice tone="error" testId="page-error">Something went wrong while loading this page.</Notice>
      <Button onClick={() => reset()}>Try again</Button>
    </div>
  );
}
