"""Filter that removes authentication material from text before it is stored as a CI artifact.

    some-command 2>&1 | python .github/scripts/redact.py > ci-logs/some-command.log

The only artifacts CI uploads are logs passed through this filter (no traces, screenshots, cookies, storage state, databases or
`.env`). Test credentials are disposable, but a failure log can still quote a session token, a one-time setup or invitation
link, a bearer header, a cookie or a database URL: those become `[redacted]`. Standard library only.
"""

import re
import sys

PATTERNS = [
    # database URLs: keep the shape, drop the credentials
    (re.compile(r"(postgres(?:ql)?(?:\+\w+)?://)[^/\s@]*@"), r"\1[redacted]@"),
    # headers and cookies, in any quoting
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?(?:bearer|basic)\s+)[^\s'\",]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(x-(?:bff-secret|csrf-token|pre-auth|dev-user-email)['\"]?\s*[:=]\s*['\"]?)[^\s'\",]+"), r"\1[redacted]"),
    (re.compile(r"(?i)((?:__Host-)?bp_(?:session|csrf|pre|dev_user)['\"]?\s*[=:]\s*['\"]?)[^\s;'\",]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(set-cookie['\"]?\s*:\s*)[^\n]+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(cookie['\"]?\s*:\s*)[^\n]+"), r"\1[redacted]"),
    # JSON/query fields that carry a secret
    (re.compile(r"(?i)(['\"]?(?:token|csrf_token|password|secret|security_key|bff_internal_secret|api_key)['\"]?\s*[:=]\s*['\"]?)[^\s'\",&}]{4,}"), r"\1[redacted]"),
    # one-time links: /setup#<token>, /invite#<token>
    (re.compile(r"(/(?:setup|invite)#)[A-Za-z0-9_-]{16,}"), r"\1[redacted]"),
    # any remaining bare 256-bit token (43 URL-safe base64 characters) or long hex secret
    (re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"), "[redacted]"),
    (re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])"), "[redacted]"),
]


def redact(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def main() -> int:
    for line in sys.stdin:
        sys.stdout.write(redact(line))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
