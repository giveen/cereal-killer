#!/bin/bash
set -euo pipefail

CYAN="\033[96m"
AMBER="\033[33m"
RED="\033[91m"
RESET="\033[0m"

MODEL_DIR="${1:-~/models/gibson}"
if [[ "$MODEL_DIR" == ~* ]]; then
	MODEL_DIR="${MODEL_DIR/#\~/$HOME}"
fi

mkdir -p logs
ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo -e "${CYAN}[gibson_check] Running environment checks...${RESET}"
if output=$(python3 scripts/setup/check_env.py --model-dir "$MODEL_DIR" 2>&1); then
	rc=0
else
	rc=$?
fi

printf "%s\n" "$output"

{
	echo "[$ts] model_dir=$MODEL_DIR exit=$rc"
	printf "%s\n" "$output"
	echo
} >> logs/setup.log

if [[ $rc -eq 0 ]]; then
	echo -e "${CYAN}[gibson_check] PASS${RESET}"
else
	echo -e "${RED}[gibson_check] FAIL${RESET}"
fi

exit $rc
