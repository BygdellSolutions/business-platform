# Lessons

Rules learned from corrections by the project owner. Review at session start; add an entry after every correction.

- **"Organization" always means the tenant.** A customer that is a business is a `company` (customer type `person` | `company`).
- **Module fields are normal domain attributes; UDFs extend, they do not replace.** Stable attributes of a domain entity (a horse's `birth_year`, `sex`, `breed`) are real, validated columns in that module, not UDFs. UDFs add organization-specific fields on top of the normal model.
- **A transaction's lifecycle status is not its invoicing state.** `draft` / `completed` / `cancelled` describe the transaction only; `completed` means finalized and ready for future invoicing, not "uninvoiced". Invoice state will be its own relationship, not another lifecycle status.
- **Totals and VAT breakdowns sum the stored line amounts.** Never recompute grouped VAT from grouped net totals; per-line half-up rounding is the single rounding rule.
- **Keep the header minimal.** The Transaction header has no note or other free text until a real need appears.
