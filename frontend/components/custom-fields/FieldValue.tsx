import { DecimalText } from "@/components/ui/DecimalText";
import type { Shown } from "@/lib/custom-fields/model";

/**
 * A saved value, printed as the backend sent it. Absence ("not set") is shown as such and is
 * never confused with `false` ("No"). A number is a decimal string and is printed unchanged.
 * An inactive target is still shown (marked); a target that no longer exists is shown safely.
 */
export function FieldValue({ shown }: { shown: Shown }) {
  switch (shown.kind) {
    case "unset":
      return <span className="text-zinc-500">Not set</span>;
    case "number":
      return <DecimalText value={shown.text} />;
    case "text":
    case "boolean":
      return <span>{shown.text}</span>;
    case "target":
      return (
        <span>
          {shown.text}
          {shown.inactive && !shown.missing && <span className="ml-1 text-xs text-zinc-500">(inactive)</span>}
        </span>
      );
  }
}
