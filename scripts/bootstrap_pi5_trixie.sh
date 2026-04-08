#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

include_dev=0
skip_verify=0
require_desktop_plotting=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-dev)
      include_dev=1
      shift
      ;;
    --skip-verify)
      skip_verify=1
      shift
      ;;
    --require-desktop-plotting)
      require_desktop_plotting=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      echo "usage: $0 [--include-dev] [--skip-verify] [--require-desktop-plotting]" >&2
      exit 2
      ;;
  esac
done

manifest_args=()
if [[ "${include_dev}" -eq 1 ]]; then
  manifest_args+=(--include-dev)
fi

mapfile -t apt_packages < <(
  python3 -m environment.rpi5_trixie apt-packages "${manifest_args[@]}"
)

if [[ "${#apt_packages[@]}" -gt 0 ]]; then
  sudo apt-get update
  sudo apt-get install -y "${apt_packages[@]}"
fi

if [[ "${include_dev}" -eq 1 ]] && ! command -v uv >/dev/null 2>&1; then
  tmp_dir="$(mktemp -d)"
  installer_path="${tmp_dir}/uv-installer.sh"
  installer_url="$(python3 -m environment.rpi5_trixie uv-install-script-url)"
  python3 - "${installer_path}" "${installer_url}" <<'PY'
import sys
import urllib.request
from pathlib import Path

target_path = Path(sys.argv[1])
installer_url = sys.argv[2]
with urllib.request.urlopen(installer_url) as response:
    target_path.write_bytes(response.read())
PY
  sh "${installer_path}"
  rm -rf "${tmp_dir}"
fi

if [[ "${skip_verify}" -eq 0 ]]; then
  python3 -m environment.rpi5_trixie_verifier \
    --repo-root "${REPO_ROOT}" \
    "${manifest_args[@]}" \
    $([[ "${require_desktop_plotting}" -eq 1 ]] && printf '%s' '--require-desktop-plotting')
fi
