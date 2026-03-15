#!/usr/bin/env bash
# E2E tests for Titanic services
# Requires: docker compose -f docker-compose.test.yml up --build -d
set -euo pipefail

UMBREL_URL="http://localhost:3029"
FLASK_URL="http://localhost:6969"
PASS=0
FAIL=0
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

pass() { echo "  ✅ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ❌ $1: $2"; FAIL=$((FAIL + 1)); }

# ── Wait for services ───────────────────────────────────────────────
echo "⏳ Waiting for services..."

wait_for() {
    local url="$1" name="$2" max=30 i=0
    while ! curl -sf "$url" > /dev/null 2>&1; do
        i=$((i + 1))
        if [ "$i" -ge "$max" ]; then
            echo "  ❌ $name did not become healthy after ${max}s"
            exit 1
        fi
        sleep 1
    done
    echo "  ✅ $name is healthy"
}

wait_for "$UMBREL_URL/health" "Umbrel (Rust)"
wait_for "$FLASK_URL/health"  "Flask (Python)"

# ── Umbrel (Rust) tests ────────────────────────────────────────────
echo ""
echo "🦀 Umbrel (Rust) service tests"

# Health check
status=$(curl -s -o /dev/null -w "%{http_code}" "$UMBREL_URL/health")
[ "$status" = "200" ] && pass "GET /health → 200" || fail "GET /health" "got $status"

body=$(curl -sf "$UMBREL_URL/health")
echo "$body" | grep -q '"healthy"' && pass "GET /health body contains 'healthy'" || fail "GET /health body" "missing 'healthy'"

# Folders
status=$(curl -s -o /dev/null -w "%{http_code}" "$UMBREL_URL/api/folders")
[ "$status" = "200" ] && pass "GET /api/folders → 200" || fail "GET /api/folders" "got $status"

body=$(curl -sf "$UMBREL_URL/api/folders")
echo "$body" | grep -q '"folders"' && pass "GET /api/folders has 'folders' key" || fail "GET /api/folders body" "missing 'folders'"

# Settings GET
status=$(curl -s -o /dev/null -w "%{http_code}" "$UMBREL_URL/api/settings")
[ "$status" = "200" ] && pass "GET /api/settings → 200" || fail "GET /api/settings" "got $status"

# Settings PUT — valid
status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$UMBREL_URL/api/settings" \
    -H "Content-Type: application/json" \
    -d '{"sentry_traces_sample_rate": 0.5, "default_folder": "TestFolder"}')
[ "$status" = "200" ] && pass "PUT /api/settings (valid) → 200" || fail "PUT /api/settings" "got $status"

# Settings PUT — invalid rate
status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$UMBREL_URL/api/settings" \
    -H "Content-Type: application/json" \
    -d '{"sentry_traces_sample_rate": 99.0}')
[ "$status" = "400" ] && pass "PUT /api/settings (invalid rate) → 400" || fail "PUT /api/settings invalid" "got $status"

# Settings round-trip: verify the value we PUT was persisted
body=$(curl -sf "$UMBREL_URL/api/settings")
echo "$body" | grep -q '"TestFolder"' && pass "Settings round-trip (default_folder persisted)" || fail "Settings round-trip" "default_folder not found"

# Settings page HTML
status=$(curl -s -o /dev/null -w "%{http_code}" "$UMBREL_URL/settings")
[ "$status" = "200" ] && pass "GET /settings → 200 (HTML)" || fail "GET /settings" "got $status"

# Upload a test video
echo ""
echo "📤 Upload test"

# Create a minimal valid MP4 if there isn't one already
TEST_FILE="$SCRIPT_DIR/test.mp4"
if [ ! -f "$TEST_FILE" ]; then
    # Create a tiny valid MP4 using dd (ftyp box header)
    printf '\x00\x00\x00\x14ftypmp42\x00\x00\x00\x00mp42' > "$TEST_FILE"
fi

status=$(curl -s -o /tmp/upload_response.json -w "%{http_code}" \
    -X POST "$UMBREL_URL/api/upload" \
    -F "file=@$TEST_FILE;filename=test_upload.mp4" \
    -F "folder=E2ETestFolder")
[ "$status" = "200" ] && pass "POST /api/upload → 200" || fail "POST /api/upload" "got $status ($(cat /tmp/upload_response.json))"

# Verify the file landed on disk inside the container
if [ "$status" = "200" ]; then
    docker exec titanic_test_umbrel ls /downloads/Clips/E2ETestFolder/test_upload.mp4 > /dev/null 2>&1 \
        && pass "Uploaded file exists on disk" \
        || fail "File on disk" "test_upload.mp4 not found in /downloads/Clips/E2ETestFolder/"
fi

# Space endpoint (requires auth in prod, but IS_DEV=true bypasses)
status=$(curl -s -o /dev/null -w "%{http_code}" "$UMBREL_URL/api/space")
[ "$status" = "200" ] && pass "GET /api/space → 200" || fail "GET /api/space" "got $status"

# ── Flask (Python) tests ───────────────────────────────────────────
echo ""
echo "🐍 Flask (Python) service tests"

status=$(curl -s -o /dev/null -w "%{http_code}" "$FLASK_URL/health")
[ "$status" = "200" ] && pass "GET /health → 200" || fail "GET /health" "got $status"

# /api/health (authenticated — IS_DEV bypasses token check)
status=$(curl -s -o /dev/null -w "%{http_code}" "$FLASK_URL/api/health")
[ "$status" = "200" ] && pass "GET /api/health → 200" || fail "GET /api/health" "got $status"

# /api/space — proxies to Umbrel
status=$(curl -s -o /dev/null -w "%{http_code}" "$FLASK_URL/api/space")
[ "$status" = "200" ] && pass "GET /api/space → 200" || fail "GET /api/space" "got $status"

body=$(curl -sf "$FLASK_URL/api/space")
echo "$body" | grep -q '"total"' && pass "GET /api/space has disk fields" || fail "GET /api/space body" "missing 'total'"

# /api/folders — proxies to Umbrel
status=$(curl -s -o /dev/null -w "%{http_code}" "$FLASK_URL/api/folders")
[ "$status" = "200" ] && pass "GET /api/folders → 200" || fail "GET /api/folders" "got $status"

body=$(curl -sf "$FLASK_URL/api/folders")
echo "$body" | grep -q '"folders"' && pass "GET /api/folders has 'folders' key" || fail "GET /api/folders body" "missing 'folders'"

# /api/config — proxies Umbrel settings, extracts default_folder
status=$(curl -s -o /dev/null -w "%{http_code}" "$FLASK_URL/api/config")
[ "$status" = "200" ] && pass "GET /api/config → 200" || fail "GET /api/config" "got $status"

body=$(curl -sf "$FLASK_URL/api/config")
echo "$body" | grep -q '"default_folder"' && pass "GET /api/config has 'default_folder' key" || fail "GET /api/config body" "missing 'default_folder'"

# ── Summary ────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results: $PASS passed, $FAIL failed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
