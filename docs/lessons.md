# Lessons

Rules learned from corrections by the project owner. Review at session start; add an entry after every correction.

- **"Organization" always means the tenant.** A customer that is a business is a `company` (customer type `person` | `company`).
- **Module fields are normal domain attributes; UDFs extend, they do not replace.** Stable attributes of a domain entity (a horse's `birth_year`, `sex`, `breed`) are real, validated columns in that module, not UDFs. UDFs add organization-specific fields on top of the normal model.
- **A transaction's lifecycle status is not its invoicing state.** `draft` / `completed` / `cancelled` describe the transaction only; `completed` means finalized and ready for future invoicing, not "uninvoiced". Invoice state will be its own relationship, not another lifecycle status.
- **Totals and VAT breakdowns sum the stored line amounts.** Never recompute grouped VAT from grouped net totals; per-line half-up rounding is the single rounding rule.
- **Keep the header minimal.** The Transaction header has no note or other free text until a real need appears.
- **Boundary tests enforce imports and registrations, not vocabulary.** Generic code and docs may use ordinary domain words. What must hold is who imports whom: core, generic capabilities and modules meet only through the core registry.
- **Required custom fields participate in finalizing a record, without Sales importing Custom Fields.** Core exposes a generic lifecycle-validation seam: capabilities register validators and modules call the seam. Failures carry the record type, id, field and label so a client can locate the problem.
- **`show_on_invoice` means "eligible to be snapshotted".** Invoices store the rendered label and value at issuance and never resolve live custom-field or reference data for an existing invoice.
- **Role checks live in core, not in feature modules.** Use `require_role` / `roles_required`; the role is always the one in the active membership.
- **Automated tests never touch the development database.** pytest and Playwright use a separate PostgreSQL server (`postgres-test`) via `TEST_DATABASE_URL`, rebuilt from migrations. A guard refuses anything not named `*_test` or hosted on the development server, and there is no fallback to the development database.
- **Enforce the money rule where money lives, not as a global ban.** Decimals are strings via branded types, scoped lint rules in the money-handling folders and tests; `parseFloat`, `Number` and `Math` stay available elsewhere.
- **Organization context belongs in the URL.** A shared cookie would let one tab silently re-target another tab's writes; the BFF takes the organization from the URL and FastAPI verifies membership.
- **Compare `Origin` with the `Host` header, and redirect with a relative `Location`.** Next may normalize the request URL host (for example `localhost` for `127.0.0.1`), which would reject legitimate same-origin requests or move the browser to a host without its cookie.
