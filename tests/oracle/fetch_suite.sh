#!/bin/sh
# Re-fetch the pinned JSON-Schema-Test-Suite snapshot (see pin.json).
# The snapshot is gitignored (tests/oracle/external/) and not committed.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PIN="$HERE/pin.json"
read -r REPO COMMIT <<EOF
$(python3 -c "
import json
p = json.load(open('$PIN'))['suite']
print(p['repository'], p['commit'])
")
EOF
DEST="$HERE/external/JSON-Schema-Test-Suite"
TMP="$HERE/external/.tmp-suite"
rm -rf "$DEST" "$TMP"
mkdir -p "$HERE/external"
git clone -q --no-checkout "$REPO" "$TMP"
git -C "$TMP" checkout -q "$COMMIT" -- tests remotes LICENSE
mkdir -p "$DEST"
mv "$TMP/tests" "$DEST/tests"
mv "$TMP/remotes" "$DEST/remotes"
mv "$TMP/LICENSE" "$DEST/LICENSE"
rm -rf "$TMP"
echo "pinned snapshot at $COMMIT -> $DEST"
