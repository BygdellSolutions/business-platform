import type { ReactNode } from "react";

export interface Detail {
  label: string;
  /** Null or an empty string is shown as "Not set". */
  value: ReactNode;
  testId?: string;
}

/** A record shown read-only: label and value pairs, for someone who may look but not change. */
export function DetailList({ details, testId }: { details: Detail[]; testId?: string }) {
  return (
    <dl data-testid={testId} className="grid max-w-xl grid-cols-[minmax(8rem,auto)_1fr] gap-x-6 gap-y-2 text-sm">
      {details.map((detail) => (
        <div key={detail.label} className="contents">
          <dt className="text-zinc-500">{detail.label}</dt>
          <dd data-testid={detail.testId}>{detail.value === null || detail.value === "" ? <span className="text-zinc-500">Not set</span> : detail.value}</dd>
        </div>
      ))}
    </dl>
  );
}
