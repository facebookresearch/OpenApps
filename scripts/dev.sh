#!/usr/bin/env bash
#
# Hot-reloading dev server. Save anything under src/ or config/ and the server
# restarts and the browser refreshes itself.
#
#   ./scripts/dev.sh
#   OPENAPPS_DEV_PORT=5005 ./scripts/dev.sh
#   OPENAPPS_DEV_OVERRIDES="apps/theme=meta_dark" ./scripts/dev.sh
#
# See dev.py for why this cannot just be `serve(reload=True)`.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PORT="${OPENAPPS_DEV_PORT:-5001}"
echo "  http://localhost:${PORT}"

# The import string, not the app object: the reloader restarts the worker and
# has to be able to re-import it.
exec uv run uvicorn dev:app \
  --reload \
  --reload-dir src \
  --reload-dir config \
  --host localhost \
  --port "${PORT}"
