"""Test client helpers for Sales optimistic concurrency (see app/modules/sales/versioning.py).

The Sales mutations now REQUIRE an `If-Match` version. Most older tests are about something
else (amounts, snapshots, isolation, lifecycle rules) and must keep testing that, so
`FreshVersionClient` looks up the CURRENT version and sends it for them. A test that is about
versions passes its own `If-Match` (the client then leaves it alone), or uses a plain
`TestClient` to prove what happens without one.
"""

import re

from fastapi.testclient import TestClient

_UUID = r"[0-9a-fA-F-]{36}"
_MUTATION = re.compile(
    rf"^/api/transactions/(?P<tx>{_UUID})(?:/(?P<action>complete|reopen|cancel)|/lines/(?P<line>{_UUID}))?$"
)


class FreshVersionClient(TestClient):
    def request(self, method, url, **kwargs):  # type: ignore[override]
        headers = dict(kwargs.get("headers") or {})
        if not any(name.lower() == "if-match" for name in headers) and method.upper() in {"PATCH", "DELETE", "POST"}:
            token = self._current_token(method.upper(), str(url).split("?")[0], headers)
            if token is not None:
                kwargs["headers"] = {**headers, "If-Match": token}
        return super().request(method, url, **kwargs)

    def _current_token(self, method: str, path: str, headers: dict) -> str | None:
        found = _MUTATION.match(path)
        if found is None:
            return None
        tx_id, action, line_id = found.group("tx", "action", "line")
        if method == "POST" and action is None:
            return None  # adding a line takes no precondition
        read = super().request("GET", f"/api/transactions/{tx_id}", headers=headers)
        if read.status_code != 200:
            return '"1"'  # not found (or not allowed): the request itself will say so
        body = read.json()
        if line_id is not None:
            line = next((item for item in body["lines"] if item["id"].lower() == line_id.lower()), None)
            return f'"{line["version"]}"' if line else '"1"'
        if method == "PATCH":
            return f'"{body["header_version"]}"'
        return f'"{body["version"]}"'  # lifecycle actions and deleting a draft
