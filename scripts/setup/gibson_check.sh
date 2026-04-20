#!/bin/bash
set -euo pipefail

CYAN="\033[96m"
AMBER="\033[33m"
RED="\033[91m"
RESET="\033[0m"

is_truthy() {
	case "${1:-}" in
		1|true|TRUE|yes|YES|on|ON) return 0 ;;
		*) return 1 ;;
	esac
}

expand_path() {
	local path="$1"
	if [[ "$path" == ~* ]]; then
		path="${path/#\~/$HOME}"
	fi
	printf "%s" "$path"
}

if is_truthy "${GIBSON_SKIP_ENV_CHECK:-0}"; then
	echo -e "${AMBER}[gibson_check] Skipping environment validation (GIBSON_SKIP_ENV_CHECK enabled).${RESET}"
	mkdir -p logs
	ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
	{
		echo "[$ts] environment validation skipped via GIBSON_SKIP_ENV_CHECK"
		echo
	} >> logs/setup.log
	echo -e "${CYAN}[gibson_check] PASS (skipped)${RESET}"
	exit 0
fi

MODEL_DIR="${1:-${GIBSON_MODEL_DIR:-${MODEL_DIR:-~/models/gibson}}}"
MODEL_DIR="$(expand_path "$MODEL_DIR")"

if [[ ! -d "$MODEL_DIR" ]]; then
	echo -e "${AMBER}[gibson_check] Model directory not found: ${MODEL_DIR}${RESET}"
	echo -e "${AMBER}[gibson_check] Tip: pass a path or set GIBSON_MODEL_DIR/MODEL_DIR.${RESET}"

	if [[ -t 0 ]]; then
		while true; do
			read -r -p "Enter model directory path: " user_model_dir
			user_model_dir="$(expand_path "${user_model_dir}")"
			if [[ -d "$user_model_dir" ]]; then
				MODEL_DIR="$user_model_dir"
				break
			fi
			echo -e "${RED}[gibson_check] Directory not found: ${user_model_dir}${RESET}"
			echo -e "${AMBER}[gibson_check] Try again or Ctrl+C to abort.${RESET}"
		done
	fi
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
