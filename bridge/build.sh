#!/usr/bin/env bash
# Build cta2045-bridge against a checkout of EPRI's CTA-2045-UCM-CPP-Library.
#
# The library has to be built first (cmake + make in that tree) so that
# libcea2045.so exists. easylogging++.h ships only inside the library's sample
# directory, so that path is on the include list too.
set -euo pipefail

LIB_ROOT="${CTA2045_LIB_ROOT:-$HOME/CTA-2045-UCM-CPP-Library}"
LIB_BUILD="${CTA2045_LIB_BUILD:-$LIB_ROOT/build}"
OUT="${1:-cta2045-bridge}"

if [[ ! -f "$LIB_BUILD/libcea2045.so" ]]; then
  echo "libcea2045.so not found in $LIB_BUILD -- build the EPRI library first" >&2
  exit 1
fi

g++ -std=c++11 -O2 -Wall \
  -I "$LIB_ROOT/cea2045" \
  -I "$LIB_ROOT/sample2/sample2" \
  "$(dirname "$0")/cta2045-bridge.cpp" \
  -o "$OUT" \
  -L "$LIB_BUILD" -lcea2045 -lpthread \
  -Wl,-rpath,"$LIB_BUILD"

echo "built $OUT"
