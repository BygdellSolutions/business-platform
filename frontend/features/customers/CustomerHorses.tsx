import Link from "next/link";

import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import { SECONDARY_LINK } from "@/components/ui/Button";
import type { Horse } from "@/lib/api/types";

/** How many horses of each kind (owned, stabled) the customer page asks for; one more than shown tells there are more. */
export const HORSES_SHOWN = 25;

export interface HorseOfCustomer {
  horse: Horse;
  owner: boolean;
  stable: boolean;
}

/** The customer's horses from the two lists the backend gives (owned, stabled): one row per horse, by name, saying in what way it is theirs. */
export function horsesOfCustomer(owned: Horse[], stabled: Horse[]): HorseOfCustomer[] {
  const found = new Map<string, HorseOfCustomer>();
  for (const horse of owned) found.set(horse.id, { horse, owner: true, stable: false });
  for (const horse of stabled) found.set(horse.id, { horse, owner: found.has(horse.id), stable: true });
  return [...found.values()].sort((a, b) => a.horse.name.localeCompare(b.horse.name, "sv") || a.horse.number - b.horse.number);
}

const roleText = ({ owner, stable }: HorseOfCustomer) => (owner && stable ? "Owner and stable" : owner ? "Owner" : "Stable");

/**
 * The horses that belong to this customer: those they own and those that stand at their stable (a person mostly owns, a company mostly
 * keeps horses). A link leads to each horse and, when there are more than shown, to the horse list filtered to this customer.
 */
export function CustomerHorses({
  orgId,
  customerId,
  owned,
  stabled,
  canWrite,
  bare = false,
}: {
  orgId: string;
  customerId: string;
  owned: Horse[];
  stabled: Horse[];
  canWrite: boolean;
  bare?: boolean;
}) {
  const rows = horsesOfCustomer(owned.slice(0, HORSES_SHOWN), stabled.slice(0, HORSES_SHOWN));
  const moreOwned = owned.length > HORSES_SHOWN;
  const moreStabled = stabled.length > HORSES_SHOWN;
  const base = `/o/${orgId}/horses`;
  return (
    <CollapsibleSection title="Horses" count={rows.length} testId="customer-horses" toggleTestId="horses-toggle" bare={bare}>
      {rows.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-horses">
          No horses owned by or stabled at this customer.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th scope="col" className="py-1 pr-4 text-right">No.</th>
                <th scope="col" className="py-1 pr-4">Horse</th>
                <th scope="col" className="py-1 pr-4">Role</th>
                <th scope="col" className="py-1 pr-4">Sex</th>
                <th scope="col" className="py-1 pr-4">Breed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.horse.id} data-testid="customer-horse-row" className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4 text-right" data-testid="customer-horse-number">{row.horse.number}</td>
                  <td className="py-1 pr-4 whitespace-nowrap">
                    <Link href={`${base}/${row.horse.id}`} className="underline" data-testid="customer-horse-link">
                      {row.horse.name}
                    </Link>
                    {!row.horse.active && <span className="ml-1 text-xs text-zinc-500">(inactive)</span>}
                  </td>
                  <td className="py-1 pr-4 whitespace-nowrap" data-testid="customer-horse-role">{roleText(row)}</td>
                  <td className="py-1 pr-4 whitespace-nowrap">{row.horse.sex ?? ""}</td>
                  <td className="py-1 pr-4 whitespace-nowrap">{row.horse.breed ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(moreOwned || moreStabled) && (
        <p className="flex flex-wrap gap-3 text-sm" data-testid="more-horses">
          {moreOwned && (
            <Link href={`${base}?owner_customer_id=${customerId}`} className="underline" data-testid="all-owned-horses">
              All horses owned by this customer
            </Link>
          )}
          {moreStabled && (
            <Link href={`${base}?stable_customer_id=${customerId}`} className="underline" data-testid="all-stabled-horses">
              All horses stabled here
            </Link>
          )}
        </p>
      )}
      {canWrite && (
        <div>
          <Link href={`${base}/new?owner_customer_id=${customerId}`} className={SECONDARY_LINK} data-testid="new-customer-horse">
            New horse
          </Link>
        </div>
      )}
    </CollapsibleSection>
  );
}
