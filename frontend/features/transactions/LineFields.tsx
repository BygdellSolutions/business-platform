"use client";

import { useMemo } from "react";

import { CustomFieldsPanel } from "@/components/custom-fields/CustomFieldsPanel";
import { useEditor } from "@/features/transactions/editor-context";
import { useFieldSaving } from "@/features/transactions/TransactionFields";
import { formDefinitions } from "@/lib/custom-fields/model";
import type { TransactionLine } from "@/lib/api/types";

/**
 * The custom fields of ONE line, shown with that line. Rendered only if the organization has
 * custom fields for lines; it reads nothing but the definitions and values the page passed.
 */
export function LineFields({ line, ordinal }: { line: TransactionLine; ordinal: number }) {
  const { fields } = useEditor();
  const wiring = useFieldSaving("transaction_line", line.id);
  const record = useMemo(
    () => ({ entityType: "transaction_line", entityId: line.id, definitions: fields.line.definitions, values: fields.line.values[line.id] ?? [] }),
    [line.id, fields.line],
  );
  if (formDefinitions(fields.line.definitions).length === 0) return null;

  return (
    <tr data-testid="line-fields-row" data-line-id={line.id} className="border-b border-zinc-200 dark:border-zinc-800">
      <td />
      <td colSpan={10} className="pb-3">
        <CustomFieldsPanel fields={record} title={`Custom fields, line ${ordinal}`} anchorId={`line-${line.id}-fields`} {...wiring} />
      </td>
    </tr>
  );
}
