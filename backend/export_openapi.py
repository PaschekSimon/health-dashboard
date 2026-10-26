"""Print the API's OpenAPI schema without starting the server, for the web
app's generated types (web/src/api/schema.d.ts) and the CI check that they
are up to date:

    python backend/export_openapi.py > openapi.json
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("APPDATA", tempfile.gettempdir())  # paths.py reads it at import
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app  # noqa: E402

json.dump(app.app.openapi(), sys.stdout, indent=1)
print()
