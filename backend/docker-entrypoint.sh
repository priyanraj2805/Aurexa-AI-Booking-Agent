#!/bin/sh
set -e

echo "Ensuring the Database is up to date..."
python init_db.py

echo "Starting FastAPI Backend application..."
exec python run.py --skip-preflight --no-browser
