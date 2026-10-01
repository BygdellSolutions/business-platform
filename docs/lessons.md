# Lessons

Rules learned from corrections by the project owner. Review at session start; add an entry after every correction.

- **"Organization" always means the tenant.** A customer that is a business is a `company` (customer type `person` | `company`).
- **Module fields are normal domain attributes; UDFs extend, they do not replace.** Stable attributes of a domain entity (a horse's `birth_year`, `sex`, `breed`) are real, validated columns in that module, not UDFs. UDFs add organization-specific fields on top of the normal model.
