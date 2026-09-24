#!/bin/sh
# TopDevices widget uninstaller.
#
#   configctl topdevices uninstall              removes everything, for good
#   sh /usr/local/opnsense/scripts/topdevices/uninstall.sh --dry-run
#                                               prints what that would do
#
# It undoes install.sh: the weekly GUI cron job that would otherwise put it all
# back, the keeper's cron file and the kept flow log, the widget, its backends and
# their configd actions. It touches nothing of core's: core's own flow log files
# lose only the second name the keeper gave them. Running it twice is harmless.
#
# ROOT=<dir> works under that directory instead of / and skips the three steps
# that only make sense on a firewall (the GUI job, configd, the ACL cache): the
# test's dry root, never used on a firewall.
#
# The [uninstall] configd action is registered WITHOUT a description on purpose:
# the GUI's cron command list keeps only actions whose description matches
# /(.){1,255}/ (Cron.xml), and configd reports a missing one as '', so this
# action can never be scheduled from System > Settings > Cron.
set -u

ROOT="${ROOT:-}"
DRY=
case "${1:-}" in
    --dry-run) DRY=1 ;;
    '') ;;
    *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

WIDGETS=/usr/local/opnsense/www/js/widgets
SCRIPTS=/usr/local/opnsense/scripts/topdevices
MVC=/usr/local/opnsense/mvc/app
ACTIONS=/usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf
CRON=/usr/local/etc/cron.d/topdevices
KEPT=/var/log/topdevices
PHP=/usr/local/bin/php
CONFIGCTL=/usr/local/sbin/configctl

[ -n "$DRY" ] && echo "TopDevices uninstall: dry run, nothing is changed"
failed=0

# remove PATH (a file or a whole directory) under ROOT, if it is there
remove() {
    p="$ROOT$1"
    [ -e "$p" ] || [ -L "$p" ] || return 0
    if [ -n "$DRY" ]; then
        echo "would remove $p"
    elif rm -rf "$p"; then
        echo "removed $p"
    else
        echo "WARNING: could not remove $p" >&2
        failed=1
    fi
}

# --- 1. the weekly GUI cron job, or it reinstalls everything next Sunday -------
# Through core's own Cron model, as System > Settings > Cron deletes a job:
# every job with origin 'cron' (a GUI-made one) whose command is our action.
if [ -n "$ROOT" ]; then
    echo "weekly GUI cron job left alone (ROOT=$ROOT)"
elif [ -n "$DRY" ]; then
    echo "would delete the weekly 'topdevices install' job(s) under System > Settings > Cron and reload cron"
else
    n=$("$PHP" -r '
        require "/usr/local/opnsense/mvc/script/load_phalcon.php";
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
        echo count($gone);' 2>/dev/null) && [ -n "$n" ] || n=
    case "$n" in
        '')
            echo "WARNING: could not read the cron jobs; delete the weekly 'Install/refresh TopDevices dashboard widget' job under System > Settings > Cron yourself" >&2
            failed=1 ;;
        0)  echo "no weekly GUI cron job found" ;;
        *)  if "$CONFIGCTL" cron restart >/dev/null 2>&1; then
                echo "weekly GUI cron job removed ($n) and cron reloaded"
            else
                echo "weekly GUI cron job removed ($n); WARNING: cron reload failed, press Apply under System > Settings > Cron" >&2
                failed=1
            fi ;;
    esac
fi

# --- 2. the keeper's cron file, then the kept log -------------------------------
# The cron file goes first so no run starts while the directory is being removed.
# Each kept file is a hard link; unlinking it leaves core's own name untouched.
remove "$CRON"
remove "$KEPT"

# --- 3. the widget, its backends, and last of all this script's own directory ---
# Removing the directory a running sh script lives in is safe: the shell reads
# from an open descriptor, and nothing below runs anything from SCRIPTS.
remove "$WIDGETS/TopDevices.js"
remove "$WIDGETS/Metadata/TopDevices.xml"
remove "$MVC/controllers/OPNsense/TopDevices"
remove "$MVC/models/OPNsense/TopDevices"
remove "$SCRIPTS"

# --- 4. the configd actions: configd forgets them on its next start -------------
had_actions=
[ -e "$ROOT$ACTIONS" ] && had_actions=1
remove "$ACTIONS"
if [ -n "$had_actions" ]; then
    if [ -n "$ROOT" ]; then
        echo "configd restart skipped (ROOT=$ROOT)"
    elif [ -n "$DRY" ]; then
        echo "would restart configd in 1 s"
    else
        # Detached and a second late, as install.sh does: run through configctl
        # this script is configd's child, and the caller must get its reply first.
        /usr/sbin/daemon -f /bin/sh -c 'sleep 1; /usr/local/etc/rc.d/configd restart'
        echo "configd actions removed - configd restarts in 1 s"
    fi
else
    echo "configd: no actions file, nothing to restart for"
fi

# --- 5. the privilege leaves the ACL now, not when the one-hour cache expires ----
if [ -z "$ROOT" ] && [ -z "$DRY" ]; then
    if "$PHP" -r 'require "/usr/local/opnsense/mvc/script/load_phalcon.php"; (new OPNsense\Core\ACL())->invalidateCache();' >/dev/null 2>&1; then
        echo "ACL cache cleared"
    else
        echo "WARNING: could not clear the ACL cache; the privilege disappears within an hour" >&2
    fi
fi

cat <<'LEFT'
left as they are: the widget's slot in your dashboard layout (an empty tile until
you remove it there), the browser's saved widget settings, and the bootstrap's
/tmp/tdx and /tmp/td.tgz. Any other 'topdevices' job under System > Settings > Cron
(a disabled copy, or one with parameters) stays too.
LEFT
if [ "$failed" -ne 0 ]; then
    echo "done, with warnings above"
    exit 1
fi
echo "done - hard-refresh the dashboard (Cmd+Shift+R)"
