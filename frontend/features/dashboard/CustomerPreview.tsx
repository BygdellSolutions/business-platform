"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api/client";
import type { Customer } from "@/lib/api/types";
import { Notice } from "@/components/ui/Notice";
import { useOrgId } from "@/components/shell/org-context";

type State =
  | { status: "loading" }
  | { status: "ready"; customers: Customer[] }
  | { status: "error"; message: string };

/**
 * A small client-side read through the BFF, so the dashboard proves the whole browser path:
 * the organization comes from the URL scope, the request goes to /api/o/{orgId}/customers,
 * FastAPI decides what that organization may see. The filter box is deliberate client state:
 * the tenant-isolation tests use it to prove state never survives an organization switch.
 */
export function CustomerPreview() {
  const orgId = useOrgId();
  const [state, setState] = useState<State>({ status: "loading" });
  const [filter, setFilter] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<Customer[]>(orgId, "/customers?limit=10&active=true", { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return; // a late answer for a page we have left
        setState(
          result.ok
            ? { status: "ready", customers: result.data }
            : { status: "error", message: result.error.message },
        );
      })
      .catch(() => {
        /* aborted: nothing to show */
      });
    return () => controller.abort();
  }, [orgId]);

  if (state.status === "loading") return <p data-testid="customer-preview-loading">Loading customers…</p>;
  if (state.status === "error") return <Notice tone="error" testId="customer-preview-error">{state.message}</Notice>;

  const shown = state.customers.filter((customer) => customer.name.toLowerCase().includes(filter.toLowerCase()));
  return (
    <section data-testid="customer-preview" aria-label="Customers preview" className="flex flex-col gap-2">
      <h2 className="text-lg font-medium">Customers (loaded in the browser)</h2>
      <label className="flex items-center gap-2 text-sm">
        Filter preview
        <input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900"
        />
      </label>
      {state.customers.length === 0 ? (
        <p>No customers yet.</p>
      ) : shown.length === 0 ? (
        <p>No customers match the filter.</p>
      ) : (
        <ul className="list-disc pl-6">
          {shown.map((customer) => (
            <li key={customer.id} data-testid="customer-preview-item">
              {customer.name}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
