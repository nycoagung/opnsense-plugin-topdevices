#!/bin/sh
# Dry-run the installer twice into a scratch root and check what it leaves.
# Run from the repository root:  sh tests/test_install.sh
set -eu
R2=
CP=
R=$(mktemp -d "${TMPDIR:-/tmp}/tdroot.XXXXXX")
trap 'rm -rf "$R" "$R2" "$CP"' EXIT
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
src/opnsense/scripts/topdevices/uninstall.sh|/usr/local/opnsense/scripts/topdevices/uninstall.sh|755
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
# the uninstall action has no description on purpose: the GUI's cron command list
# offers only actions whose description matches /(.){1,255}/ (core's Cron.xml)
block=$(awk '$0 == "[uninstall]" {on = 1; next} /^\[/ {on = 0} on' "$A")
echo "$block" | grep -qx 'command:/usr/local/opnsense/scripts/topdevices/uninstall.sh' || fail "[uninstall] command wrong"
echo "$block" | grep -qx 'type:script_output' || fail "[uninstall] is not script_output"
echo "$block" | grep -q '^description:' && fail "[uninstall] has a description, so the GUI's cron list would offer it"
grep -q '<pattern>api/topdevices/flows/\*</pattern>' "$R/usr/local/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml" \
    || fail "the ACL does not cover the flows endpoints"
[ -z "$(find "$R" -name '*.tdnew' -o -name '.actions_topdevices.new')" ] || fail "staging files left behind"
# the keep job: every 10 minutes as root; a dry run never runs it - keep.py uses
# the absolute path, so nothing under $R could prove that either way, and the
# message below (from ROOT skipping the run) is the real check
grep -qxF "$(printf '*/10\t*\t*\t*\t*\troot\t/usr/local/opnsense/scripts/topdevices/keep.py >/dev/null 2>&1')" \
    "$R/usr/local/etc/cron.d/topdevices" || fail "the cron line is wrong"
echo "$first" | grep -q 'keep.py not run (ROOT=' || fail "the dry run did not say it skipped keep.py"
# A download that fails must not leave the installer's scratch directory behind.
F="$R/fetchfail"; mkdir -p "$F"
cp install.sh "$F/install.sh"                      # no source tree beside it: the fetch path runs
printf '#!/bin/sh\nexit 1\n' > "$F/fetch"; chmod 755 "$F/fetch"
before=$(ls -d /tmp/tdinst.* 2>/dev/null | wc -l)
PATH="$F:$PATH" ROOT="$R" sh "$F/install.sh" >/dev/null 2>&1 && fail "install succeeded without a source"
after=$(ls -d /tmp/tdinst.* 2>/dev/null | wc -l)
[ "$before" = "$after" ] || fail "a failed fetch left /tmp/tdinst.* behind"

# The staging names are dot-prefixed (so cron never reads one mid-copy): a cp
# that fails partway through must leave only dotted *.tdnew files behind, never
# an undotted one. A fake cp fails only on the cron file's staging name and
# copies through to the real cp for everything else.
CP=$(mktemp -d "${TMPDIR:-/tmp}/tdcp.XXXXXX")
cat > "$CP/cp" <<'SH'
#!/bin/sh
case "$2" in
    */.topdevices.tdnew) exit 1 ;;
esac
exec /bin/cp "$@"
SH
chmod 755 "$CP/cp"
R2=$(mktemp -d "${TMPDIR:-/tmp}/tdroot2.XXXXXX")
mkdir -p "$R2/usr/local/opnsense/www/js/widgets/Metadata" "$R2/usr/local/opnsense/service/conf/actions.d"
PATH="$CP:$PATH" ROOT="$R2" sh install.sh >/dev/null 2>&1 && fail "install succeeded despite cp failing on the cron file"
[ -n "$(find "$R2" -name '.*.tdnew')" ] || fail "no staged files found after the partial failure"
[ -z "$(find "$R2" -name '[!.]*.tdnew')" ] || fail "an undotted staging file was created"
rm -rf "$R2" "$CP"

echo "install dry run: OK"
