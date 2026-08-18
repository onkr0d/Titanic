#!/usr/bin/env bash
# E2E tests for Titanic services
# Requires: docker compose -f docker-compose.test.yml up --build -d
set -euo pipefail

UMBREL_URL="http://localhost:3029"
# The settings listener. In production this port has no host mapping — it is
# reachable only through Umbrel's authenticated app_proxy — so these two URLs
# represent two different trust levels, not two paths to the same thing.
SETTINGS_URL="http://localhost:3031"
QUART_URL="http://localhost:6969"
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
wait_for "$QUART_URL/health"  "Quart (Python)"

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
status=$(curl -s -o /dev/null -w "%{http_code}" "$SETTINGS_URL/api/settings")
[ "$status" = "200" ] && pass "GET /api/settings (settings port) → 200" || fail "GET /api/settings" "got $status"

# Valid settings write
status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$SETTINGS_URL/api/settings" \
    -H "Content-Type: application/json" \
    -d '{"sentry_traces_sample_rate":0.5,"default_folder":"E2ETestFolder","sentry_dsn":"https://e2e@sentry.io/1"}')
[ "$status" = "200" ] && pass "PUT /api/settings (valid) → 200" || fail "PUT /api/settings" "got $status"

# Invalid sample rate is still rejected
status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$SETTINGS_URL/api/settings" \
    -H "Content-Type: application/json" \
    -d '{"sentry_traces_sample_rate":5.0}')
[ "$status" = "400" ] && pass "PUT /api/settings (invalid rate) → 400" || fail "PUT /api/settings invalid" "got $status"

body=$(curl -sf "$SETTINGS_URL/api/settings")
echo "$body" | grep -q '"default_folder"' && pass "GET /api/settings persisted default_folder" || fail "GET /api/settings body" "missing 'default_folder'"

# Settings page is served on the settings port
status=$(curl -s -o /dev/null -w "%{http_code}" "$SETTINGS_URL/settings")
[ "$status" = "200" ] && pass "GET /settings (settings port) → 200 (HTML)" || fail "GET /settings" "got $status"

# ── Listener split ─────────────────────────────────────────────────
# The whole point of the two-listener design: the settings page and its write
# API must not be reachable from the port that is published to the tailnet.
echo ""
echo "🔒 Listener split (settings must not be on the published port)"

status=$(curl -s -o /dev/null -w "%{http_code}" "$UMBREL_URL/settings")
[ "$status" = "404" ] && pass "GET /settings on published port → 404" || fail "GET /settings on published port" "expected 404, got $status"

status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$UMBREL_URL/api/settings" \
    -H "Content-Type: application/json" \
    -d '{"sentry_dsn":"https://attacker@evil.example/1"}')
[ "$status" = "405" ] && pass "PUT /api/settings on published port → 405" || fail "PUT /api/settings on published port" "expected 405, got $status"

# ...and the rejected write must not have landed.
body=$(curl -sf "$SETTINGS_URL/api/settings")
echo "$body" | grep -q 'attacker@evil.example' && fail "published-port PUT mutated settings" "DSN was overwritten" || pass "published-port PUT did not mutate settings"

# The published port exposes only the redacted projection: folder yes, DSN no.
body=$(curl -sf "$UMBREL_URL/api/settings")
echo "$body" | grep -q '"default_folder"' && pass "published /api/settings exposes default_folder" || fail "published /api/settings" "missing 'default_folder'"
echo "$body" | grep -q 'sentry_dsn' && fail "published /api/settings leaks sentry_dsn" "DSN present in response" || pass "published /api/settings omits sentry_dsn"

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

# ── Quart (Python) tests ───────────────────────────────────────────
echo ""
echo "🐍 Quart (Python) service tests"

status=$(curl -s -o /dev/null -w "%{http_code}" "$QUART_URL/health")
[ "$status" = "200" ] && pass "GET /health → 200" || fail "GET /health" "got $status"

# /api/health (authenticated — IS_DEV bypasses token check)
status=$(curl -s -o /dev/null -w "%{http_code}" "$QUART_URL/api/health")
[ "$status" = "200" ] && pass "GET /api/health → 200" || fail "GET /api/health" "got $status"

# /api/space — proxies to Umbrel
status=$(curl -s -o /dev/null -w "%{http_code}" "$QUART_URL/api/space")
[ "$status" = "200" ] && pass "GET /api/space → 200" || fail "GET /api/space" "got $status"

body=$(curl -sf "$QUART_URL/api/space")
echo "$body" | grep -q '"total"' && pass "GET /api/space has disk fields" || fail "GET /api/space body" "missing 'total'"

# /api/folders — proxies to Umbrel
status=$(curl -s -o /dev/null -w "%{http_code}" "$QUART_URL/api/folders")
[ "$status" = "200" ] && pass "GET /api/folders → 200" || fail "GET /api/folders" "got $status"

body=$(curl -sf "$QUART_URL/api/folders")
echo "$body" | grep -q '"folders"' && pass "GET /api/folders has 'folders' key" || fail "GET /api/folders body" "missing 'folders'"

# /api/config — proxies Umbrel settings, extracts default_folder
status=$(curl -s -o /dev/null -w "%{http_code}" "$QUART_URL/api/config")
[ "$status" = "200" ] && pass "GET /api/config → 200" || fail "GET /api/config" "got $status"

body=$(curl -sf "$QUART_URL/api/config")
echo "$body" | grep -q '"default_folder"' && pass "GET /api/config has 'default_folder' key" || fail "GET /api/config body" "missing 'default_folder'"

# Shareable capability flags gate the frontend's size-cap UI
echo "$body" | grep -q '"skip_if_under"' && pass "GET /api/config advertises 'skip_if_under'" || fail "GET /api/config body" "missing 'skip_if_under'"
echo "$body" | grep -q '"supports_only"' && pass "GET /api/config advertises 'supports_only'" || fail "GET /api/config body" "missing 'supports_only'"

# ── Summary ────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results: $PASS passed, $FAIL failed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
