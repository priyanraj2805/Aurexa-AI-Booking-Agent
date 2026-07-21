#!/bin/bash
# =============================================================================
# Booking Agent - Project Transfer Script
# =============================================================================
# Creates a clean ZIP file for sharing with colleagues
# Excludes: node_modules, venv, .git, .planning, secrets, build artifacts
# =============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ZIP_NAME="booking_agent_transfer_${TIMESTAMP}.zip"
TEMP_DIR="/tmp/booking_agent_transfer_$$"

echo "================================================"
echo "  Booking Agent - Transfer Package Creator"
echo "================================================"
echo ""
echo "Project: $PROJECT_DIR"
echo "Output:  $PROJECT_DIR/$ZIP_NAME"
echo ""

# Create temp directory for clean copy
echo "[1/4] Creating clean copy of project..."
rm -rf "$TEMP_DIR"
mkdir -p "$TEMP_DIR"

# Use rsync to copy with exclusions
rsync -a --exclude='.git/' \
         --exclude='.planning/' \
         --exclude='node_modules/' \
         --exclude='venv/' \
         --exclude='.venv/' \
         --exclude='__pycache__/' \
         --exclude='*.pyc' \
         --exclude='.next/' \
         --exclude='dist/' \
         --exclude='out/' \
         --exclude='.pytest_cache/' \
         --exclude='.ruff_cache/' \
         --exclude='playwright-report/' \
         --exclude='test-results/' \
         --exclude='coverage/' \
         --exclude='htmlcov/' \
         --exclude='.vscode/' \
         --exclude='.idea/' \
         --exclude='scratch/' \
         --exclude='debug/' \
         --exclude='.DS_Store' \
         --exclude='Thumbs.db' \
         --exclude='*.log' \
         --exclude='*.db' \
         --exclude='*.session' \
         --exclude='*.debug' \
         --exclude='*.pem' \
         --exclude='*.key' \
         --exclude='deployment.zip' \
         --exclude='.env' \
         --exclude='backend/.env' \
         --exclude='backend/.env.docker' \
         --exclude='frontend/admin-panel/.env.local' \
         "$PROJECT_DIR/" "$TEMP_DIR/booking_agent/"

echo "[2/4] Creating ZIP archive..."
cd "$TEMP_DIR"
zip -r "$PROJECT_DIR/$ZIP_NAME" booking_agent/

echo ""
echo "[2/3] Calculating size..."
echo ""

# Get file count and size
FILE_COUNT=$(unzip -l "$ZIP_NAME" | tail -1 | awk '{print $1}')
ZIP_SIZE=$(du -h "$ZIP_NAME" | cut -f1)

echo "      Files included: $FILE_COUNT"
echo "      ZIP size:       $ZIP_SIZE"

echo ""
echo "[3/4] Calculating size..."
echo ""

# Get file count and size
FILE_COUNT=$(unzip -l "$ZIP_NAME" | tail -1 | awk '{print $1}')
ZIP_SIZE=$(du -h "$PROJECT_DIR/$ZIP_NAME" | cut -f1)

echo "      Files included: $FILE_COUNT"
echo "      ZIP size:       $ZIP_SIZE"

echo ""
echo "[4/4] Verifying contents..."
echo ""

# Verify key files exist in the zip
echo "      Checking critical files..."
REQUIRED_FILES=(
    "booking_agent/docker-compose.yml"
    "booking_agent/backend/requirements.txt"
    "booking_agent/backend/Dockerfile"
    "booking_agent/frontend/admin-panel/package.json"
    "booking_agent/frontend/admin-panel/Dockerfile"
    "booking_agent/frontend/chat-widget/package.json"
    "booking_agent/SETUP.md"
    "booking_agent/README.md"
    "booking_agent/.env.example"
    "booking_agent/backend/.env.example"
    "booking_agent/backend/.env.docker.example"
)

MISSING=0
for file in "${REQUIRED_FILES[@]}"; do
    if unzip -l "$PROJECT_DIR/$ZIP_NAME" 2>/dev/null | grep -q "$file"; then
        echo "        [OK] $file"
    else
        echo "        [MISSING] $file"
        MISSING=1
    fi
done

# Verify excluded files are NOT in the zip
echo ""
echo "      Verifying exclusions..."
EXCLUDED_PATTERNS=(
    "node_modules"
    "venv"
    ".git/"
    ".planning/"
    ".env"
)

for pattern in "${EXCLUDED_PATTERNS[@]}"; do
    if unzip -l "$PROJECT_DIR/$ZIP_NAME" 2>/dev/null | grep -q "$pattern"; then
        echo "        [WARNING] Found excluded pattern: $pattern"
    else
        echo "        [OK] Excluded: $pattern"
    fi
done

# Cleanup temp directory
echo ""
echo "      Cleaning up temp files..."
rm -rf "$TEMP_DIR"

echo ""
echo "================================================"
echo "  TRANSFER PACKAGE READY"
echo "================================================"
echo ""
echo "  File: $PROJECT_DIR/$ZIP_NAME"
echo "  Size: $ZIP_SIZE"
echo ""
echo "  Send this file to your colleague along with"
echo "  these instructions:"
echo ""
echo "  1. Unzip the file"
echo "  2. Copy .env.example to .env"
echo "  3. Copy backend/.env.docker.example to backend/.env.docker"
echo "  4. Fill in API keys in backend/.env.docker"
echo "  5. Run: docker compose up --build"
echo ""
echo "  See SETUP.md for detailed instructions."
echo "================================================"
