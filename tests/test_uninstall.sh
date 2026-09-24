#!/bin/sh
# Dry-run the installer into a scratch root, then the uninstaller three ways: as a
# rehearsal (--dry-run), for real from its installed copy (it removes itself), and
# once more when nothing is left. Run from the repository root:  sh tests/test_uninstall.sh
set -eu
R=$(mktemp -d "${TMPDIR:-/tmp}/tduroot.XXXXXX")
trap 'rm -rf "$R"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

U=/usr/local/opnsense
mkdir -p "$R$U/www/js/widgets/Metadata" "$R$U/service/conf/actions.d"
ROOT="$R" sh install.sh >/dev/null

# what the keeper and the dashboard leave on a real install, and some of core's
# neighbours that must survive
mkdir -p "$R/var/log/topdevices" "$R$U/scripts/topdevices/__pycache__"
: > "$R/var/log/topdevices/flowd.1000.2000"; : > "$R/var/log/topdevices/.current"
: > "$R$U/scripts/topdevices/__pycache__/flows.cpython-313.pyc"
: > "$R$U/www/js/widgets/Other.js"; : > "$R$U/www/js/widgets/Metadata/Other.xml"
: > "$R$U/service/conf/actions.d/actions_other.conf"
mkdir -p "$R/usr/local/etc/cron.d"; : > "$R/usr/local/etc/cron.d/other"
mkdir -p "$R$U/mvc/app/controllers/OPNsense/Other" "$R$U/mvc/app/models/OPNsense/Other"

OURS="
$U/www/js/widgets/TopDevices.js
$U/www/js/widgets/Metadata/TopDevices.xml
$U/scripts/topdevices
$U/mvc/app/controllers/OPNsense/TopDevices
$U/mvc/app/models/OPNsense/TopDevices
$U/service/conf/actions.d/actions_topdevices.conf
/usr/local/etc/cron.d/topdevices
/var/log/topdevices
"
THEIRS="
$U/www/js/widgets/Other.js
$U/www/js/widgets/Metadata/Other.xml
$U/service/conf/actions.d/actions_other.conf
/usr/local/etc/cron.d/other
$U/mvc/app/controllers/OPNsense/Other
$U/mvc/app/models/OPNsense/Other
$U/www/js/widgets/Metadata
$U/service/conf/actions.d
"
for p in $OURS $THEIRS; do [ -e "$R$p" ] || fail "test setup: $p missing"; done

# 1. the rehearsal changes nothing and names every path it would remove
before=$(find "$R" | sort)
dry=$(ROOT="$R" sh "$R$U/scripts/topdevices/uninstall.sh" --dry-run) || fail "--dry-run exited non-zero"
[ "$(find "$R" | sort)" = "$before" ] || fail "--dry-run changed the tree"
for p in $OURS; do
    echo "$dry" | grep -qF "would remove $R$p" || fail "--dry-run did not name $p"
done
echo "$dry" | grep -q "weekly" || fail "--dry-run did not mention the weekly job"
echo "$dry" | grep -q "configd" || fail "--dry-run did not mention configd"

# 2. for real, from the installed copy: it removes its own directory while running
out=$(ROOT="$R" sh "$R$U/scripts/topdevices/uninstall.sh" 2>&1) || fail "uninstall exited non-zero: $out"
for p in $OURS; do [ ! -e "$R$p" ] || fail "still there: $p"; done
for p in $THEIRS; do [ -e "$R$p" ] || fail "removed something of core's: $p"; done
echo "$out" | grep -q "^done" || fail "no closing line after removing its own directory: $out"
echo "$out" | grep -q "ROOT=" || fail "the dry root run did not say it skipped the firewall-only steps"
echo "$out" | grep -qi "dashboard" || fail "the closing lines do not mention the dashboard slot it leaves"
echo "$out" | grep -q "Cron" || fail "the output does not tell the user about the weekly job"
[ -z "$(find "$R" -name '*topdevices*' -o -name 'TopDevices*')" ] || fail "something named topdevices survived: $(find "$R" -name '*topdevices*' -o -name 'TopDevices*')"

# 3. again, with nothing left: still exit 0, nothing removed, nothing of core's touched
before=$(find "$R" | sort)
again=$(ROOT="$R" sh src/opnsense/scripts/topdevices/uninstall.sh 2>&1) || fail "second uninstall exited non-zero: $again"
[ "$(find "$R" | sort)" = "$before" ] || fail "the second run changed the tree"
echo "$again" | grep -q "removed " && fail "the second run claims to have removed something"

# 4. a bad flag is refused, and refuses before touching anything
ROOT="$R" sh src/opnsense/scripts/topdevices/uninstall.sh --force >/dev/null 2>&1 && fail "an unknown flag was accepted"

echo "uninstall dry run: OK"
