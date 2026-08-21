#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--print-backend" ]]; then
    printf 'cog\n'
    exit 0
fi

printf '[PiStick] Starting the ARMv6-compatible Cog/WPE kiosk.\n'
exec "$SCRIPT_DIR/kiosk-cog.sh"
