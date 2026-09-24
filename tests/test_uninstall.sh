#!/bin/sh
# Dry-run the installer into a scratch root, then the uninstaller: as a rehearsal
# (--dry-run), for real from its installed copy (it removes itself), once more when
# nothing is left, and then with stand-ins for php, configctl and daemon so the
# firewall-only steps run too. Run from the repository root:  sh tests/test_uninstall.sh
set -eu
W=$(mktemp -d "${TMPDIR:-/tmp}/tdu.XXXXXX")
trap 'rm -rf "$W"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
U=/usr/local/opnsense
UN=src/opnsense/scripts/topdevices/uninstall.sh

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
/var/log/flowd.log.000001
"

# A fresh root with a full install, what the keeper and the dashboard leave on a
# real install, and some of core's neighbours that must survive. Core's rotated
# flow log file is hard-linked into the kept directory, as keep.py does.
fresh() {
    R=$(mktemp -d "$W/root.XXXXXX")
    mkdir -p "$R$U/www/js/widgets/Metadata" "$R$U/service/conf/actions.d"
    ROOT="$R" sh install.sh >/dev/null
    mkdir -p "$R/var/log/topdevices" "$R$U/scripts/topdevices/__pycache__" "$R/usr/local/etc/cron.d" \
             "$R$U/mvc/app/controllers/OPNsense/Other" "$R$U/mvc/app/models/OPNsense/Other"
    echo flows > "$R/var/log/flowd.log.000001"
    ln "$R/var/log/flowd.log.000001" "$R/var/log/topdevices/flowd.1000.2000"
    : > "$R/var/log/topdevices/.current"
    : > "$R$U/scripts/topdevices/__pycache__/flows.cpython-313.pyc"
    : > "$R$U/www/js/widgets/Other.js"; : > "$R$U/www/js/widgets/Metadata/Other.xml"
    : > "$R$U/service/conf/actions.d/actions_other.conf"; : > "$R/usr/local/etc/cron.d/other"
    for p in $OURS $THEIRS; do [ -e "$R$p" ] || fail "test setup: $p missing"; done
    echo "$R"
}
nlink() { stat -c '%h' "$1" 2>/dev/null || stat -f '%l' "$1"; }    # GNU first: BSD's -f means something else to GNU stat
all_gone() { for p in $OURS; do [ ! -e "$1$p" ] || fail "$2: still there: $p"; done
             for p in $THEIRS; do [ -e "$1$p" ] || fail "$2: removed something of core's: $p"; done; }

R=$(fresh)
[ "$(nlink "$R/var/log/flowd.log.000001")" = 2 ] || fail "test setup: the kept file is not a hard link"

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
all_gone "$R" "real run"
[ "$(cat "$R/var/log/flowd.log.000001")" = flows ] || fail "core's flow log file changed"
[ "$(nlink "$R/var/log/flowd.log.000001")" = 1 ] || fail "core's flow log file still has the kept name"
echo "$out" | grep -q "^done" || fail "no closing line after removing its own directory: $out"
echo "$out" | grep -qF "weekly GUI cron job left alone (ROOT=$R)" || fail "the dry root run did not say it skipped the GUI job"
echo "$out" | grep -qF "configd restart skipped (ROOT=$R)" || fail "the dry root run did not say it skipped configd"
echo "$out" | grep -qi "dashboard" || fail "the closing lines do not mention the dashboard slot it leaves"
echo "$out" | grep -qF "/tmp/tdx" || fail "the closing lines do not mention /tmp/tdx"
# the cron file goes before the kept log, so no keeper run can start in between
cron_at=$(echo "$out" | grep -nF "removed $R/usr/local/etc/cron.d/topdevices" | cut -d: -f1)
kept_at=$(echo "$out" | grep -nF "removed $R/var/log/topdevices" | cut -d: -f1)
[ -n "$cron_at" ] && [ -n "$kept_at" ] && [ "$cron_at" -lt "$kept_at" ] || fail "the cron file was not removed before the kept log ($cron_at vs $kept_at)"
[ -z "$(find "$R" -name '*topdevices*' -o -name 'TopDevices*')" ] || fail "something named topdevices survived: $(find "$R" -name '*topdevices*' -o -name 'TopDevices*')"

# 3. again, with nothing left: still exit 0, nothing removed, nothing of core's touched
before=$(find "$R" | sort)
again=$(ROOT="$R" sh $UN 2>&1) || fail "second uninstall exited non-zero: $again"
[ "$(find "$R" | sort)" = "$before" ] || fail "the second run changed the tree"
echo "$again" | grep -q "removed " && fail "the second run claims to have removed something"

# 4. a bad flag is refused before anything is touched
R=$(fresh)
before=$(find "$R" | sort)
ROOT="$R" sh $UN --force >/dev/null 2>&1 && fail "an unknown flag was accepted"
[ "$(find "$R" | sort)" = "$before" ] || fail "a refused flag still changed the tree"

# 5. the firewall-only steps, against stand-ins. php answers per $S/php.mode:
# a count, "fail" (exit 255, no output), or "noise" (a notice before the count).
# Every call is logged in order, so the sequence can be checked.
S="$W/stubs"; mkdir -p "$S"; LOG="$S/calls"
cat > "$S/php" <<'SH'
#!/bin/sh
case "$*" in
    *invalidateCache*) echo "php acl" >> "$CALLS"; exit 0 ;;
    *display_errors=stderr*) ;;
    *) echo "php jobs without display_errors=stderr" >> "$CALLS"; exit 255 ;;
esac
echo "php jobs" >> "$CALLS"
mode=$(cat "$MODE")
case "$mode" in
    fail)  exit 255 ;;
    noise) printf 'Deprecated: something in load_phalcon.php on line 1\n0' ;;
    *)     printf '%s' "$mode" ;;
esac
SH
cat > "$S/configctl" <<'SH'
#!/bin/sh
echo "configctl $*" >> "$CALLS"
[ "$(cat "$MODE")" != 1 ]    # in mode 1 the cron reload fails
SH
cat > "$S/daemon" <<'SH'
#!/bin/sh
echo "daemon $*" >> "$CALLS"
SH
chmod 755 "$S/php" "$S/configctl" "$S/daemon"
export CALLS="$LOG" MODE="$S/php.mode"
onbox() { # MODE FLAG... -> stdout only (warnings must be there, configd drops stderr); $? is the script's exit status
    echo "$1" > "$MODE"; shift; : > "$LOG"
    ROOT="$R" STUBS=1 PHP="$S/php" CONFIGCTL="$S/configctl" DAEMON="$S/daemon" sh "$R$U/scripts/topdevices/uninstall.sh" "$@" 2>/dev/null
}
# STUBS=1 without all three stand-ins must refuse, not reach for the real tools
R=$(fresh)
ROOT="$R" STUBS=1 PHP="$S/php" sh "$R$U/scripts/topdevices/uninstall.sh" --dry-run >/dev/null 2>&1 && fail "STUBS=1 ran with stand-ins missing"

# 5a. two jobs found: deleted, cron reloaded, ACL cleared before configd is scheduled, exit 0
R=$(fresh)
# the bare word is what configctl can pass through (its own parser eats --dry-run)
out=$(onbox 2 dry-run) || fail "stubbed dry-run exited non-zero: $out"
[ ! -s "$LOG" ] || fail "--dry-run called a tool: $(cat "$LOG")"
echo "$out" | grep -q "would delete the weekly" || fail "stubbed --dry-run did not say it would delete the job"
echo "$out" | grep -q "would restart configd" || fail "stubbed --dry-run did not say it would restart configd"
out=$(onbox 2) || fail "stubbed run exited non-zero: $out"
all_gone "$R" "stubbed run"
echo "$out" | grep -q "weekly GUI cron job removed (2) and cron reloaded" || fail "wrong job line: $out"
echo "$out" | grep -q "ACL cache cleared" || fail "the ACL step did not run"
echo "$out" | grep -q "configd restarts in 1 s" || fail "configd was not scheduled"
[ "$(cat "$LOG")" = "$(printf 'php jobs\nconfigctl cron restart\nphp acl\ndaemon -f /bin/sh -c sleep 1; /usr/local/etc/rc.d/configd restart')" ] \
    || fail "unexpected tool sequence: $(cat "$LOG")"

# 5b. no job: nothing reloaded, exit 0
R=$(fresh)
out=$(onbox 0) || fail "no-job run exited non-zero: $out"
echo "$out" | grep -q "no weekly GUI cron job found" || fail "no-job line missing: $out"
grep -q configctl "$LOG" && fail "cron was reloaded with no job deleted"

# 5c. php fails: the rest is still removed, the manual step is on stdout, exit 1
R=$(fresh)
out=$(onbox fail) && fail "a php failure still exited 0"
all_gone "$R" "php-failure run"
echo "$out" | grep -q "^WARNING: could not delete the weekly job" || fail "no warning on stdout after php failed: $out"
echo "$out" | grep -q "System > Settings > Cron" || fail "the warning does not name the manual step"
echo "$out" | grep -q "^done, with warnings" || fail "the closing line does not admit the warning"
grep -q configctl "$LOG" && fail "cron was reloaded after php failed"

# 5d. a notice in php's output is not a count
R=$(fresh)
out=$(onbox noise) && fail "a php notice was taken for a count"
echo "$out" | grep -q "^WARNING: could not delete the weekly job" || fail "no warning for noisy php output: $out"
grep -q configctl "$LOG" && fail "cron was reloaded on noisy php output"

# 5e. the job is deleted but the cron reload fails: says so, exit 1
R=$(fresh)
out=$(onbox 1) && fail "a failed cron reload still exited 0"
echo "$out" | grep -q "weekly GUI cron job removed (1)" || fail "the deletion is not reported when the reload fails"
echo "$out" | grep -q "^WARNING: cron reload failed" || fail "no warning for the failed reload: $out"
all_gone "$R" "reload-failure run"

echo "uninstall dry run: OK"
