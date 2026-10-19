"""Common-password check for NEW passwords (never applied to a login, so existing passwords keep working).

The list is the UK National Cyber Security Centre's "100k most used passwords" (published under the Open Government Licence v3.0,
as redistributed in danielmiessler/SecLists, MIT), lower-cased and de-duplicated, shipped as `data/common_passwords.txt`. It is
read once, on first use, into memory; there is NO network call (sign-up and password changes must never wait for an outside service).

Two checks, both case-insensitive:
  * the whole password is on the list;
  * the password's STEM (what is left after removing trailing digits and symbols) is on the list and is at least four letters.
    The 12-character minimum alone lets "Password123456" through: most list entries are shorter than 12, so a plain whole-word
    match would rarely fire. A stem that is the whole password (no trailing digits) is the first check, so a long passphrase that
    merely ENDS in a common word is not affected.

The caller shows one fixed message that never says which check matched.
"""

import re
import threading
from functools import lru_cache
from pathlib import Path

_FILE = Path(__file__).with_name("data") / "common_passwords.txt"
_TRAILING_NON_LETTERS = re.compile(r"[^a-z]+$")
_MIN_STEM = 4
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load() -> frozenset[str]:
    with _lock:
        return frozenset(line for line in _FILE.read_text(encoding="utf-8").split("\n") if line)


def is_common(password: str) -> bool:
    lowered = password.strip().casefold()
    common = _load()
    if lowered in common:
        return True
    stem = _TRAILING_NON_LETTERS.sub("", lowered)
    return stem != lowered and len(stem) >= _MIN_STEM and stem in common
