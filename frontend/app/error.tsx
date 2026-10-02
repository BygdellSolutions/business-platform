"use client";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";

/** Errors thrown outside a page: the shell/layout itself (for example the backend is down). */
export default function RootError({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="mx-auto flex max-w-xl flex-col items-start gap-3 p-8">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <Notice tone="error" testId="app-error">The application could not load. The backend may be unavailable.</Notice>
      <Button onClick={() => reset()}>Try again</Button>
    </main>
  );
}
