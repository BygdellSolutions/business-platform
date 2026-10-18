/** Shown while a page loads: the shape of a page (title, a panel of rows) instead of a bare word, so nothing jumps when the data arrives. */
export default function Loading() {
  return (
    <div data-testid="loading" role="status" aria-busy="true" className="flex flex-col gap-4 motion-safe:animate-pulse">
      <span className="sr-only">Loading…</span>
      <div className="h-8 w-48 rounded-md bg-surface-hover" />
      <div className="panel flex flex-col gap-3">
        <div className="h-9 w-full max-w-md rounded-lg bg-surface-hover" />
        {Array.from({ length: 6 }, (_, row) => (
          <div key={row} className="h-6 rounded bg-surface-hover" style={{ width: `${95 - row * 6}%` }} />
        ))}
      </div>
    </div>
  );
}
