"""Print the API's OpenAPI schema without starting the server (`health
openapi > openapi.json`), for the web app's generated types
(web/src/api/schema.d.ts) and the CI check that they are up to date."""

import json
import sys


def main(argv=None):
    from .app import app
    json.dump(app.openapi(), sys.stdout, indent=1)
    print()
