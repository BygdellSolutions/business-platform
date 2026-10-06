"""The container test helpers of `deploy/tests/conftest.py` (docker, free ports, the backend image fixture), loaded by path so the drill
does not live in the merge-gating `deploy/tests` directory yet shares one implementation of them."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "tests" / "conftest.py"
_SPEC = importlib.util.spec_from_file_location("bp_container_helpers", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

BACKEND_DIR = _MODULE.BACKEND_DIR
BFF_SECRET = _MODULE.BFF_SECRET
RUN = _MODULE.RUN
SECURITY_KEY = _MODULE.SECURITY_KEY
docker = _MODULE.docker
host_port = _MODULE.host_port
backend_image = _MODULE.backend_image
