import Link from "next/link";
import { SECONDARY_LINK } from "@/components/ui/Button";

/** Shown instead of a create form to someone whose role may read but not create. */
export function NotAllowed({ what, back, backLabel }: { what: string; back: string; backLabel: string }) {
  return (
    <div className="flex flex-col gap-2" data-testid="not-allowed">
      <p>Your role in this organization can view {what} but not create them.</p>
      <Link href={back} className={`${SECONDARY_LINK} self-start`}>
        {backLabel}
      </Link>
    </div>
  );
}
