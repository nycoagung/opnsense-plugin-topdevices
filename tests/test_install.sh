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
src/opnsense/scripts/topdevices/flows.py|/usr/local/opnsense/scripts/topdevices/flows.py|755
src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php|/usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php|644
src/opnsense/scripts/topdevices/keep.py|/usr/local/opnsense/scripts/topdevices/keep.py|755
src/etc/cron.d/topdevices|/usr/local/etc/cron.d/topdevices|644
LIST

A="$R/usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf"
grep -qx '\[live\]' "$A" || fail "no [live] action"
grep -qx 'command:/usr/local/opnsense/scripts/topdevices/live.py' "$A" || fail "live action path wrong (ROOT leaked?)"
grep -qx 'type:stream_output' "$A" || fail "live action is not a stream"
for a in 'flows.totals|totals|%s %s' 'flows.device|device|%s %s %s'; do
    name=${a%%|*}; rest=${a#*|}; mode=${rest%%|*}; params=${rest#*|}
    block=$(awk -v h="[$name]" '$0 == h {on = 1; next} /^\[/ {on = 0} on' "$A")
    echo "$block" | grep -qx "command:/usr/local/opnsense/scripts/topdevices/flows.py $mode" || fail "[$name] command wrong"
    echo "$block" | grep -qx "parameters:$params" || fail "[$name] parameters wrong"
    echo "$block" | grep -qx 'type:script_output' || fail "[$name] is not script_output"
done
grep -q '<pattern>api/topdevices/flows/\*</pattern>' "$R/usr/local/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml" \
    || fail "the ACL does not cover the flows endpoints"
[ -z "$(find "$R" -name '*.tdnew' -o -name '.actions_topdevices.new')" ] || fail "staging files left behind"
# the keep job: every 10 minutes as root; a dry run never runs it, nor creates the kept log
grep -qxF "$(printf '*/10\t*\t*\t*\t*\troot\t/usr/local/opnsense/scripts/topdevices/keep.py >/dev/null 2>&1')" \
    "$R/usr/local/etc/cron.d/topdevices" || fail "the cron line is wrong"
echo "$first" | grep -q 'keep.py not run (ROOT=' || fail "the dry run did not say it skipped keep.py"
[ ! -e "$R/var/log/topdevices" ] || fail "the dry run created the kept log"
# A download that fails must not leave the installer's scratch directory behind.
F="$R/fetchfail"; mkdir -p "$F"
cp install.sh "$F/install.sh"                      # no source tree beside it: the fetch path runs
printf '#!/bin/sh\nexit 1\n' > "$F/fetch"; chmod 755 "$F/fetch"
before=$(ls -d /tmp/tdinst.* 2>/dev/null | wc -l)
PATH="$F:$PATH" ROOT="$R" sh "$F/install.sh" >/dev/null 2>&1 && fail "install succeeded without a source"
after=$(ls -d /tmp/tdinst.* 2>/dev/null | wc -l)
[ "$before" = "$after" ] || fail "a failed fetch left /tmp/tdinst.* behind"
echo "install dry run: OK"
