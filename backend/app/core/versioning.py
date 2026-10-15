"""Optimistic concurrency for Sales: a change is accepted only if it is based on the
version the caller last saw.

Why this exists: a browser tab shows a snapshot of a transaction. Without a precondition,
a stale tab silently overwrites whatever someone else saved in the meantime. With one,
the server refuses a change that was based on an older version and changes nothing.

The token is an integer `version` on the record (not `updated_at`: a timestamp can tie,
and the transaction's own timestamp does not move when one of its lines changes).

  record              token              changes when
  ------------------  -----------------  -------------------------------------------------
  transaction         `version`          anything: header, a line, or the status
  transaction header  `header_version`   billing customer or date only
  line                `version`          that line is edited

Which token guards which request:

  PATCH  /transactions/{id}                          header_version  (editing the header)
  DELETE /transactions/{id}                          version         (a draft, as a whole)
  POST   /transactions/{id}/complete|reopen|cancel   version         (a decision about everything it shows)
  PATCH|DELETE /transactions/{id}/lines/{line_id}    the line's version
  POST   /transactions/{id}/lines                    none: adding a line commutes with other
                                                     edits (it still moves `version`)

The token travels in the HTTP `If-Match` header, as a quoted integer (`"3"`; a bare `3` is
accepted too). A header, not a body field, because DELETE and the lifecycle POSTs have no
body. It is REQUIRED on the requests above (428 if missing, 400 if malformed). A mismatch is
a 409 with `{"code": "stale_record", ...}` (RFC 9110 would say 412; 409 keeps one family of
"your view is out of date" answers next to the status conflicts), and NOTHING is changed.

Order of checks in every guarded request, which is what keeps tenancy intact:
  1. the record is found in the caller's organization (else 404, whatever the header says);
  2. the state allows the change (a completed transaction answers its own 409 first);
  3. the precondition is compared, under the same row lock the mutation takes;
  4. only then does anything change.
A foreign or random id therefore never reaches step 3.
"""

import re
import uuid

from fastapi import HTTPException, status

_TOKEN = re.compile(r'^"?(\d{1,9})"?$')


def expected_version(if_match: str | None) -> int:
    """The version the caller says its change is based on."""
    if if_match is None:
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED,
            detail='This change must say which version it is based on (If-Match: "<version>")',
        )
    found = _TOKEN.match(if_match.strip())
    if found is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid If-Match header")
    return int(found.group(1))


def ensure_current(if_match: str | None, actual: int, entity_type: str, entity_id: uuid.UUID) -> None:
    """Raise a 409 `stale_record` unless the caller's version is the record's current one."""
    expected = expected_version(if_match)
    if expected != actual:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "stale_record",
                "message": "This record was changed by someone else since you loaded it; reload it and try again",
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "current_version": actual,
            },
        )
