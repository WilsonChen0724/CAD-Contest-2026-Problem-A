#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_ROOT="${INSTALL_DIR:-${REPO_ROOT}/third_party/yosys}"
TOOL_ROOT="${INSTALL_ROOT}/oss-cad-suite"
YOSYS_BIN="${TOOL_ROOT}/bin/yosys"
FORCE=0

usage() {
    cat <<'EOF'
Usage: scripts/install_yosys.sh [--force] [--install-dir DIR]

Downloads the latest OSS CAD Suite release and installs it under:
  third_party/yosys/oss-cad-suite

Options:
  --force           Remove an existing local install before installing.
  --install-dir DIR Install under DIR instead of third_party/yosys.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            FORCE=1
            shift
            ;;
        --install-dir)
            INSTALL_ROOT="$2"
            TOOL_ROOT="${INSTALL_ROOT}/oss-cad-suite"
            YOSYS_BIN="${TOOL_ROOT}/bin/yosys"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -x "${YOSYS_BIN}" && "${FORCE}" -eq 0 ]]; then
    echo "Yosys is already installed at ${YOSYS_BIN}"
    "${YOSYS_BIN}" -V
    echo
    echo "For this shell session, run:"
    echo "export PATH=\"${TOOL_ROOT}/bin:\$PATH\""
    exit 0
fi

if [[ -d "${TOOL_ROOT}" && "${FORCE}" -eq 1 ]]; then
    rm -rf "${TOOL_ROOT}"
fi

mkdir -p "${INSTALL_ROOT}"

case "$(uname -m)" in
    x86_64|amd64)
        PLATFORM="linux-x64"
        ;;
    aarch64|arm64)
        PLATFORM="linux-arm64"
        ;;
    armv7l|armv6l)
        PLATFORM="linux-arm"
        ;;
    riscv64)
        PLATFORM="linux-riscv64"
        ;;
    *)
        echo "Unsupported Linux architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

API_URL="https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/latest"
METADATA_PATH="${INSTALL_ROOT}/oss-cad-suite-release.json"

echo "Fetching latest OSS CAD Suite release metadata..."
if command -v curl >/dev/null 2>&1; then
    curl -fsSL -H "User-Agent: cada-yosys-installer" "${API_URL}" -o "${METADATA_PATH}"
elif command -v wget >/dev/null 2>&1; then
    wget -q --header="User-Agent: cada-yosys-installer" -O "${METADATA_PATH}" "${API_URL}"
else
    echo "Please install curl or wget before running this script." >&2
    exit 1
fi

ASSET_INFO="$(
    python3 - "${METADATA_PATH}" "${PLATFORM}" <<'PY'
import json
import sys

metadata_path, platform = sys.argv[1], sys.argv[2]
with open(metadata_path, "r", encoding="utf-8") as f:
    release = json.load(f)

for asset in release.get("assets", []):
    name = asset.get("name", "")
    if platform in name and name.endswith(".tgz"):
        print(name)
        print(asset["browser_download_url"])
        break
else:
    raise SystemExit(f"Could not find a {platform} .tgz asset in the latest OSS CAD Suite release.")
PY
)"

ASSET_NAME="$(printf '%s\n' "${ASSET_INFO}" | sed -n '1p')"
ASSET_URL="$(printf '%s\n' "${ASSET_INFO}" | sed -n '2p')"
ARCHIVE_PATH="${INSTALL_ROOT}/${ASSET_NAME}"

echo "Downloading ${ASSET_NAME}..."
if command -v curl >/dev/null 2>&1; then
    curl -fL -H "User-Agent: cada-yosys-installer" "${ASSET_URL}" -o "${ARCHIVE_PATH}"
else
    wget -O "${ARCHIVE_PATH}" "${ASSET_URL}"
fi

echo "Extracting to ${INSTALL_ROOT}..."
tar -xzf "${ARCHIVE_PATH}" -C "${INSTALL_ROOT}"

if [[ ! -x "${YOSYS_BIN}" ]]; then
    echo "Install finished, but yosys was not found at ${YOSYS_BIN}" >&2
    exit 1
fi

rm -f "${ARCHIVE_PATH}" "${METADATA_PATH}"

echo
echo "Yosys installed successfully:"
"${YOSYS_BIN}" -V
echo
echo "For this shell session, run:"
echo "export PATH=\"${TOOL_ROOT}/bin:\$PATH\""
