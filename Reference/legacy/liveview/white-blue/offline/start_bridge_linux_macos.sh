#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -f node_modules/ws/package.json ]; then
  npm install
fi
npm start
