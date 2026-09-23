#!/bin/sh
# Dry-run the installer twice into a scratch root and check what it leaves.
# Run from the repository root:  sh tests/test_install.sh
set -eu
R=$(mktemp -d "${TMPDIR:-/tmp}/tdroot.XXXXXX")
trap 'rm -rf "$R"' EXIT
mkdir -p "$R/usr/local/opnsense/www/js/widgets/Metadata" "$R/usr/local/opnsense/service/conf/actions.d"
fail() { echo "FAIL: $*" >&2; exit 1; }

first=$(ROOT="$R" sh install.sh)
second=$(ROOT="$R" sh install.sh)
echo "$first" | grep -q 'configd actions changed - restart skipped' || fail "first run did not register the actions"
echo "$second" | grep -q 'configd actions already current' || fail "second run changed the actions"

while IFS='|' read -r src dst mode; do
    [ -n "$src" ] || continue
    [ -f "$R$dst" ] || fail "not installed: $dst"
    cmp -s "$src" "$R$dst" || fail "differs from source: $dst"
    [ "$(stat -f '%Lp' "$R$dst" 2>/dev/null || stat -c '%a' "$R$dst")" = "$mode" ] || fail "mode of $dst is not $mode"
done <<LIST
src/opnsense/www/js/widgets/TopDevices.js|/usr/local/opnsense/www/js/widgets/TopDevices.js|644
src/opnsense/www/js/widgets/Metadata/TopDevices.xml|/usr/local/opnsense/www/js/widgets/Metadata/TopDevices.xml|644
src/opnsense/scripts/topdevices/live.py|/usr/local/opnsense/scripts/topdevices/live.py|755
src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php|/usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php|644
src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml|/usr/local/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml|644
install.sh|/usr/local/opnsense/scripts/topdevices/install.sh|755
LIST

A="$R/usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf"
grep -qx '\[live\]' "$A" || fail "no [live] action"
grep -qx 'command:/usr/local/opnsense/scripts/topdevices/live.py' "$A" || fail "live action path wrong (ROOT leaked?)"
grep -qx 'type:stream_output' "$A" || fail "live action is not a stream"
[ -z "$(find "$R" -name '*.tdnew' -o -name '.actions_topdevices.new')" ] || fail "staging files left behind"
echo "install dry run: OK"
