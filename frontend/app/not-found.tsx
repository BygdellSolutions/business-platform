/**
 * One generic 404 for everything: a malformed id, a nonexistent organization and an
 * organization that belongs to someone else are indistinguishable by design.
 */
export default function NotFound() {
  return (
    <main className="mx-auto flex max-w-xl flex-col gap-3 p-8">
      <h1 className="text-2xl font-semibold" data-testid="not-found">Not found</h1>
      <p>This page does not exist, or you do not have access to it.</p>
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a href="/" className="underline">Go to your organizations</a>
    </main>
  );
}
