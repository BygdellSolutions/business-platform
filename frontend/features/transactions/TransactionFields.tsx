"use client";

import { useMemo } from "react";

import { CustomFieldsPanel } from "@/components/custom-fields/CustomFieldsPanel";
import { useOrgId } from "@/components/shell/org-context";
import { useEditor } from "@/features/transactions/editor-context";
import { classify } from "@/features/transactions/failures";
import type { ApiError } from "@/lib/api/errors";
import { writeValues } from "@/lib/custom-fields/api";

/**
 * Connects the generic Custom Fields panel to the transaction editor. This is the only place
 * that knows these fields belong to a Sales record; the panel itself is generic.
 *
 * What it adds, and nothing more:
 *  - editable only while the server says the transaction is a draft (the page's own state);
 *  - the save goes through the editor's one-change-at-a-time guard and is followed by a refresh
 *    of the whole transaction page (which re-reads the values);
 *  - it sends NO Sales version: custom-field writes are outside the Sales concurrency contract,
 *    and FastAPI locks the transaction, so a write and a completion cannot interleave;
 *  - a blocked completion's problems are shown at the controls they are about;
 *  - a failure that is not about one field (a locked record, a missing one, the network) goes
 *    through the same handling as every other change of this page.
 */
export function useFieldSaving(entityType: string, entityId: string) {
  const orgId = useOrgId();
  const { mutate, report, registerEditor, busy, readOnly, fieldErrors } = useEditor();
  return {
    readOnly,
    busy,
    registerEditor,
    externalErrors: fieldErrors(entityType, entityId),
    save: (values: Record<string, string | boolean | null>) => mutate(() => writeValues(orgId, entityType, entityId, values)),
    onFailure: (error: ApiError) => report(classify(error), "save"),
  };
}

/** The custom fields of the transaction itself. */
export function TransactionFields() {
  const { transaction, fields } = useEditor();
  const wiring = useFieldSaving("transaction", transaction.id);
  const record = useMemo(
    () => ({ entityType: "transaction", entityId: transaction.id, definitions: fields.transaction.definitions, values: fields.transaction.values }),
    [transaction.id, fields.transaction],
  );
  return <CustomFieldsPanel fields={record} title="Custom fields" anchorId="transaction-fields" {...wiring} />;
}
