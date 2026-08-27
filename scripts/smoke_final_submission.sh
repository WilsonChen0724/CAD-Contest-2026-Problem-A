#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 <submission.tar.gz> <config.yaml> <input.v>" >&2
    exit 2
fi

ARCHIVE="$(realpath "$1")"
CONFIG="$(realpath "$2")"
INPUT_NETLIST="$(realpath "$3")"
SMOKE_DIR="$(mktemp -d /tmp/cada1070-final-smoke.XXXXXX)"

cleanup() {
    case "${SMOKE_DIR}" in
        /tmp/cada1070-final-smoke.*) rm -rf -- "${SMOKE_DIR}" ;;
        *) echo "Refusing to remove unexpected smoke directory: ${SMOKE_DIR}" >&2 ;;
    esac
}
trap cleanup EXIT

tar -xzf "${ARCHIVE}" -C "${SMOKE_DIR}"
cd "${SMOKE_DIR}"

printf '%s\n' \
    'This is the beginning of a new testcase. The case name is test01.' \
    "Please load the design from the file \"${INPUT_NETLIST}\"." \
    'Please write the current design to the output file "test01_out.v".' \
    | ./cada1070_final -config "${CONFIG}"

test -s test01.log
test -s test01_out.v
grep -q '^#RESPONSE 1$' test01.log
grep -q '^#END 3$' test01.log

PYTHONPATH="${SMOKE_DIR}/vendor/python" \
    ./third_party/python/python/bin/python3 -c \
    'import z3; from parser.verilog_parser import parse_verilog; d = parse_verilog("test01_out.v"); print("BUNDLED_RUNTIME_OK", z3.get_version_string(), len(d.gates), len(d.dffs))'

printf 'LAUNCHER_MODE %s\n' "$(stat -c '%a' ./cada1070_final)"
echo "FINAL_SUBMISSION_SMOKE_OK"
