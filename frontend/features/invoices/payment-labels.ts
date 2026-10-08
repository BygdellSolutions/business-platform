import type { Invoice } from "@/lib/api/types";

/** Payment words, in a plain module (not a client component) so server pages can use them too. */
export const PAYMENT_STATES: Record<NonNullable<Invoice["payment_status"]>, string> = {
  unpaid: "Unpaid",
  partially_paid: "Partially paid",
  paid: "Paid",
};

export const PAYMENT_METHODS = [
  { value: "bankgiro", label: "Bankgiro" },
  { value: "plusgiro", label: "Plusgiro" },
  { value: "bank_transfer", label: "Bank transfer" },
  { value: "swish", label: "Swish" },
  { value: "card", label: "Card" },
  { value: "cash", label: "Cash" },
  { value: "other", label: "Other" },
];
