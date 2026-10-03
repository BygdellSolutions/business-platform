import Link from "next/link";

/** Previous / next links. `hrefFor(page)` builds the address (see listHref). */
export function Pagination({ page, hasNext, hrefFor }: { page: number; hasNext: boolean; hrefFor: (page: number) => string }) {
  if (page === 1 && !hasNext) return null;
  return (
    <nav aria-label="Pages" className="flex items-center gap-4 text-sm">
      {page > 1 ? (
        <Link href={hrefFor(page - 1)} rel="prev" className="underline">
          Previous
        </Link>
      ) : (
        <span className="text-zinc-400">Previous</span>
      )}
      <span data-testid="page-number">Page {page}</span>
      {hasNext ? (
        <Link href={hrefFor(page + 1)} rel="next" className="underline">
          Next
        </Link>
      ) : (
        <span className="text-zinc-400">Next</span>
      )}
    </nav>
  );
}
