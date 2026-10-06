"""A stand-in for pg_dump and pg_restore, to test the backup and restore TOOLS' safety logic (guards, atomic publishing, scrubbing,
exit codes) without the PostgreSQL clients. The real programs run in the container drill (deploy/tests/test_restore_drill.py).

    python fake_pg.py dump    [pg_dump arguments]      FAKE_PG_DUMP_MODE:    ok | fail | partial_fail | version(prints 17.0)
    python fake_pg.py restore [pg_restore arguments]   FAKE_PG_RESTORE_MODE: ok | fail | unreadable

It records what it was given (arguments, whether PGPASSWORD was in the environment, never the value) to FAKE_PG_LOG when set.
Failure modes print an error that quotes the password and a URL with credentials, as a real client sometimes does.
"""

import json
import os
import sys

DUMP_BYTES = b"PGDMP\x01fake-custom-format-archive-for-tests\n" * 4


def record(role: str, arguments: list[str]) -> None:
    path = os.environ.get("FAKE_PG_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"role": role, "argv": arguments, "has_pgpassword": "PGPASSWORD" in os.environ, "pgdatabase": os.environ.get("PGDATABASE"), "pguser": os.environ.get("PGUSER"), "ambient_database_url": "DATABASE_URL" in os.environ}) + "\n")


def leak() -> None:
    password = os.environ.get("PGPASSWORD", "")
    print(f'pg: error: connection to server at "10.9.8.7", port 5432 failed: FATAL: password authentication failed for user "x" (password: {password})', file=sys.stderr)
    print(f"pg: detail: postgresql://someone:{password}@10.9.8.7:5432/db", file=sys.stderr)


def main() -> int:
    role, arguments = sys.argv[1], sys.argv[2:]
    record(role, arguments)
    if "--version" in arguments:
        print(f"pg_dump (PostgreSQL) {os.environ.get('FAKE_PG_VERSION', '17.0')}")
        return 0
    if role == "dump":
        mode = os.environ.get("FAKE_PG_DUMP_MODE", "ok")
        target = next((argument.split("=", 1)[1] for argument in arguments if argument.startswith("--file=")), None)
        if mode == "fail":
            leak()
            return 1
        if target:
            with open(target, "wb") as handle:
                handle.write(DUMP_BYTES)
        if mode == "partial_fail":
            leak()
            return 1
        return 0
    mode = os.environ.get("FAKE_PG_RESTORE_MODE", "ok")
    if "--list" in arguments:
        if mode == "unreadable":
            print("pg_restore: error: file is not a valid archive", file=sys.stderr)
            return 1
        print("; Archive created at fake\n1; 1259 16384 TABLE public customers owner\n2; 1259 16390 TABLE public invoices owner")
        return 0
    if mode == "fail":
        leak()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
