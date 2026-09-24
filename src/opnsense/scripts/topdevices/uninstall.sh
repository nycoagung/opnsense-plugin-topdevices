#!/bin/sh
# TopDevices widget uninstaller.
#
#   configctl topdevices uninstall              removes everything, for good
#   configctl topdevices uninstall --dry-run    prints what that would do
#   sh /usr/local/opnsense/scripts/topdevices/uninstall.sh [--dry-run]
#
# It undoes install.sh: the weekly GUI cron job that would otherwise put it all
# back, the keeper's cron file and the kept flow log, the widget, its backends and
# their configd actions. It touches nothing of core's: core's own flow log files
# lose only the second name the keeper gave them. Running it twice is harmless.
#
# Everything, warnings included, goes to stdout: run as a configd script_output
# action, stderr reaches only the system log, and the action says errors:no so a
# non-zero exit still returns this output to configctl instead of "Execute error".
#
# ROOT=<dir> works under that directory instead of / and skips the three steps
# that only make sense on a firewall (the GUI job, configd, the ACL cache): the
# test's dry root, never used on a firewall. STUBS=1 runs those steps anyway,
# against the PHP, CONFIGCTL and DAEMON given in the environment (the test's
# stand-ins).
#
# The [uninstall] configd action is registered WITHOUT a description on purpose:
# the GUI's cron command list keeps only actions whose description matches
# /(.){1,255}/ (Cron.xml), and configd reports a missing one as '', so this
# action can never be scheduled from System > Settings > Cron.
set -u

ROOT="${ROOT:-}"
if [ -n "${STUBS:-}" ]; then
    # the test's stand-ins, all three or nothing; the firewall-only steps run against them
    PHP="${PHP:?STUBS=1 needs PHP}"; CONFIGCTL="${CONFIGCTL:?STUBS=1 needs CONFIGCTL}"; DAEMON="${DAEMON:?STUBS=1 needs DAEMON}"
    ONBOX=1
else
    PHP=/usr/local/bin/php; CONFIGCTL=/usr/local/sbin/configctl; DAEMON=/usr/sbin/daemon
    ONBOX=1; [ -n "$ROOT" ] && ONBOX=
fi
DRY=
case "${1:-}" in
    --dry-run) DRY=1 ;;
    '') ;;
    *) echo "usage: $0 [--dry-run]"; exit 2 ;;
esac

WIDGETS=/usr/local/opnsense/www/js/widgets
SCRIPTS=/usr/local/opnsense/scripts/topdevices
MVC=/usr/local/opnsense/mvc/app
ACTIONS=/usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf
CRON=/usr/local/etc/cron.d/topdevices
KEPT=/var/log/topdevices
JOB_TITLE="Install/refresh TopDevices dashboard widget"

[ -n "$DRY" ] && echo "TopDevices uninstall: dry run, nothing is changed"
failed=0
warn() { echo "WARNING: $*"; failed=1; }

# remove PATH (a file or a whole directory) under ROOT, if it is there
remove() {
    p="$ROOT$1"
    [ -e "$p" ] || [ -L "$p" ] || return 0
    if [ -n "$DRY" ]; then
        echo "would remove $p"
    elif rm -rf "$p"; then
        echo "removed $p"
    else
        warn "could not remove $p"
    fi
}

# --- 1. the weekly GUI cron job, or it comes back to fail every Sunday ----------
# Through core's own Cron model, as System > Settings > Cron deletes a job:
# every job with origin 'cron' (a GUI-made one) whose command is our action,
# under the same config lock the GUI takes. PHP's notices would go to stdout
# from the command line, so they are sent to stderr and only digits count.
if [ -z "$ONBOX" ]; then
    echo "weekly GUI cron job left alone (ROOT=$ROOT)"
elif [ -n "$DRY" ]; then
    echo "would delete the weekly 'topdevices install' job(s) under System > Settings > Cron and reload cron"
else
    n=$("$PHP" -d display_errors=stderr -r '
        require "/usr/local/opnsense/mvc/script/load_phalcon.php";
        OPNsense\Core\Config::getInstance()->lock();
        $mdl = new OPNsense\Cron\Cron();
        $gone = [];
        foreach ($mdl->jobs->job->iterateItems() as $uuid => $job) {
            if ((string)$job->command === "topdevices install" && (string)$job->origin === "cron") {
                $gone[] = $uuid;
            }
        }
        foreach ($gone as $uuid) {
            $mdl->jobs->job->del($uuid);
        }
        if ($gone) {
            $mdl->serializeToConfig();
            OPNsense\Core\Config::getInstance()->save();
        }
        echo count($gone);' 2>/dev/null) || n=
    case "$n" in
        ''|*[!0-9]*)
            warn "could not delete the weekly job: delete '$JOB_TITLE' under System > Settings > Cron yourself, or it fails every Sunday from now on" ;;
        0)  echo "no weekly GUI cron job found" ;;
        *)  if "$CONFIGCTL" cron restart >/dev/null 2>&1; then
                echo "weekly GUI cron job removed ($n) and cron reloaded"
            else
                echo "weekly GUI cron job removed ($n)"
                warn "cron reload failed: press Apply under System > Settings > Cron"
            fi ;;
    esac
fi

# --- 2. the keeper's cron file, then the kept log -------------------------------
# The cron file goes first so no new run starts, and a run already going (under a
# second, every 10 minutes) gets to finish, or it could recreate the directory.
# Each kept file is a hard link; unlinking it leaves core's own name untouched.
remove "$CRON"
if [ -z "$DRY" ]; then
    i=0
    while [ $i -lt 30 ] && pgrep -qf "$SCRIPTS/keep.py" 2>/dev/null; do sleep 1; i=$((i + 1)); done
    [ $i -lt 30 ] || warn "a keep.py run is still going after 30 s; if $KEPT comes back, remove it by hand"
fi
remove "$KEPT"

# --- 3. the widget, its backends, and last of all this script's own directory ---
# Removing the directory a running sh script lives in is safe: the shell reads
# from an open descriptor, and nothing below runs anything from SCRIPTS.
remove "$WIDGETS/TopDevices.js"
remove "$WIDGETS/Metadata/TopDevices.xml"
remove "$MVC/controllers/OPNsense/TopDevices"
remove "$MVC/models/OPNsense/TopDevices"
remove "$SCRIPTS"

# --- 4. the configd actions; configd itself restarts last of all (step 6) ------
had_actions=
[ -e "$ROOT$ACTIONS" ] && had_actions=1
remove "$ACTIONS"

# --- 5. the privilege leaves the ACL now, not when the one-hour cache expires ----
if [ -n "$ONBOX" ] && [ -z "$DRY" ]; then
    if "$PHP" -r 'require "/usr/local/opnsense/mvc/script/load_phalcon.php"; (new OPNsense\Core\ACL())->invalidateCache();' >/dev/null 2>&1; then
        echo "ACL cache cleared"
    else
        warn "could not clear the ACL cache; the privilege disappears within an hour"
    fi
fi

cat <<'LEFT'
left as they are: the widget's slot in your dashboard layout (an empty tile until
you remove it there), the browser's saved widget settings, and the bootstrap's
/tmp/tdx and /tmp/td.tgz. A 'topdevices install' cron job registered by something
other than the Cron page (origin not 'cron') stays too.
LEFT

# --- 6. configd forgets the actions on its next start: detached and a second ---
# late, as install.sh does, because run through configctl this script is
# configd's child and the caller must get this output first. Nothing may follow.
if [ -z "$had_actions" ]; then
    echo "configd: no actions file, nothing to restart for"
elif [ -z "$ONBOX" ]; then
    echo "configd restart skipped (ROOT=$ROOT)"
elif [ -n "$DRY" ]; then
    echo "would restart configd in 1 s"
else
    "$DAEMON" -f /bin/sh -c 'sleep 1; /usr/local/etc/rc.d/configd restart'
    echo "configd actions removed - configd restarts in 1 s"
fi
if [ "$failed" -ne 0 ]; then
    echo "done, with warnings above"
    exit 1
fi
echo "done - hard-refresh the dashboard (Cmd+Shift+R)"
