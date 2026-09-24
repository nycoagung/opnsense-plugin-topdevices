#!/bin/sh
# TopDevices widget installer / updater.
#
# Bootstrap (one command, no GitHub API involved):
#   fetch -qo /tmp/td.tgz https://codeload.github.com/nycoagung/opnsense-plugin-topdevices/tar.gz/refs/heads/main && \
#     rm -rf /tmp/tdx && mkdir -p /tmp/tdx && tar -xzf /tmp/td.tgz -C /tmp/tdx && \
#     sh /tmp/tdx/opnsense-plugin-topdevices-main/install.sh
#
# Afterwards:  configctl topdevices install
#
# WHY codeload AND NOT THE API OR raw:
#   - the API costs one rate-limited request per file (60/hour per IP, and it is
#     the firewall's own public IP that counts). Ten files is survivable, but
#     it is the same flaw that locked the sibling parentalcontrol plugin out
#     entirely at fifteen.
#   - raw.githubusercontent is CDN-cached, lags pushes by minutes and is cached
#     per edge, so it silently served stale files here more than once.
#   - codeload serves the git ref directly: one request for the whole tree, no
#     rate limit, and current.
#
# These files are not owned by any package, so a firmware upgrade can remove
# them - hence the configd action and the weekly cron job.
#
# UPGRADING FROM 0.0.1: the 0.0.1 installer only knows its own three files. Run
# through configctl or cron it installs the new widget and this script but not
# the live backend, until the next run. Use the bootstrap command once instead.
# UPGRADING FROM 0.1.x: likewise, the 0.1.x installer only knows its six files:
# run through configctl or cron it installs the new widget and this script but
# not flows.py, its controller or the flows actions, until the next run (the
# widget falls back to NetFlow's records meanwhile). Use the bootstrap command
# once instead.
# UPGRADING FROM 0.2.0: likewise, the 0.2.0 installer only knows its eight files:
# run through configctl or cron it installs everything but keep.py and its cron
# file, until the next run (Yesterday is read from NetFlow's records meanwhile).
# Use the bootstrap command once instead.
# UPGRADING FROM 0.3.0: likewise, the 0.3.0 installer only knows its ten files:
# run through configctl or cron it installs everything but uninstall.sh and its
# action, until the next run. Nothing else changes in 0.3.1, so the second run
# can wait for the next Sunday.
#
# REMOVING: configctl topdevices uninstall (see uninstall.sh).
#
# NOTE: OPNsense cron runs as root regardless - configd executes jobs as root.
# Using cron avoids interactive SSH, not root privileges.
#
# ROOT=<dir> installs under that directory instead of / and skips the configd
# restart and the ACL cache: a dry run for testing, never used on a firewall.
set -e

GH_OWNER="${GH_OWNER:-nycoagung}"
GH_REPO="${GH_REPO:-opnsense-plugin-topdevices}"
GH_REF="${GH_REF:-main}"
ROOT="${ROOT:-}"
P=src/opnsense

WIDGETS=/usr/local/opnsense/www/js/widgets
SCRIPTS=/usr/local/opnsense/scripts/topdevices
MVC=/usr/local/opnsense/mvc/app
ACTIONS=/usr/local/opnsense/service/conf/actions.d

[ -d "$ROOT$WIDGETS/Metadata" ] || { echo "widget dir not found: $ROOT$WIDGETS/Metadata" >&2; exit 1; }

FILES="
$P/www/js/widgets/TopDevices.js|$WIDGETS/TopDevices.js
$P/www/js/widgets/Metadata/TopDevices.xml|$WIDGETS/Metadata/TopDevices.xml
$P/scripts/topdevices/live.py|$SCRIPTS/live.py
$P/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php|$MVC/controllers/OPNsense/TopDevices/Api/LiveController.php
$P/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml|$MVC/models/OPNsense/TopDevices/ACL/ACL.xml
install.sh|$SCRIPTS/install.sh
$P/scripts/topdevices/flows.py|$SCRIPTS/flows.py
$P/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php|$MVC/controllers/OPNsense/TopDevices/Api/FlowsController.php
$P/scripts/topdevices/keep.py|$SCRIPTS/keep.py
src/etc/cron.d/topdevices|/usr/local/etc/cron.d/topdevices
$P/scripts/topdevices/uninstall.sh|$SCRIPTS/uninstall.sh
"

HERE=$(dirname "$0")
if [ -d "$HERE/$P/www/js/widgets" ]; then
    SRC="$HERE"
    echo "installing from $SRC"
else
    TMP=$(mktemp -d /tmp/tdinst.XXXXXX)
    trap 'rm -rf "$TMP"' EXIT              # a failed fetch must not leave it behind
    echo "fetching ${GH_OWNER}/${GH_REPO}@${GH_REF} from codeload"
    fetch -qT 30 -o "$TMP/src.tgz" \
        "https://codeload.github.com/${GH_OWNER}/${GH_REPO}/tar.gz/refs/heads/${GH_REF}"
    tar -xzf "$TMP/src.tgz" -C "$TMP"
    SRC=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)
    [ -n "$SRC" ] && [ -d "$SRC/$P/www/js/widgets" ] || { echo "archive did not contain $P" >&2; exit 1; }
fi

# Check the whole set is present before touching anything on disk, so a
# truncated archive cannot leave a half-installed widget behind.
for entry in $FILES; do
    [ -n "$entry" ] || continue
    s=${entry%%|*}
    [ -f "$SRC/$s" ] || { echo "missing from source: $s" >&2; exit 1; }
done

# Stage beside the destination, then rename into place. Not only for atomicity:
# this script installs ITSELF, and sh reads a script incrementally, so a cp over
# the running file shifts the shell's read offset and it dies mid-script. mv
# gives the file a new inode and leaves the running descriptor untouched.
# The staging name is dotted: for /usr/local/etc/cron.d/topdevices, cron reads
# any file in cron.d not starting with a dot, so an undotted "topdevices.tdnew"
# would be a live crontab for the cp's duration, or for good if left behind.
for entry in $FILES; do
    [ -n "$entry" ] || continue
    s=${entry%%|*}; d=$ROOT${entry#*|}
    mkdir -p "$(dirname "$d")"
    cp "$SRC/$s" "$(dirname "$d")/.$(basename "$d").tdnew"
done
for entry in $FILES; do
    [ -n "$entry" ] || continue
    s=${entry%%|*}; d=$ROOT${entry#*|}
    mv "$(dirname "$d")/.$(basename "$d").tdnew" "$d"
    case "$d" in *.sh|*.py) chmod 0755 "$d" ;; *) chmod 0644 "$d" ;; esac
    printf '  %-26s %6d bytes\n' "$(basename "$s")" "$(wc -c < "$d" | tr -d ' ')"
done
echo "widget installed"

# --- keep NetFlow's flow log from now on: cron runs keep.py every 10 minutes ---
# Once now, so the kept log starts with core's current files (spec 2026-09-24 §9).
if [ -z "$ROOT" ]; then
    if "$SCRIPTS/keep.py"; then
        echo "flow log kept in /var/log/topdevices (cron: every 10 minutes)"
    else
        echo "WARNING: keep.py failed (see the system log); cron tries again every 10 minutes" >&2
    fi
else
    echo "keep.py not run (ROOT=$ROOT)"
fi

# --- register the configd actions (idempotent) ---
if [ -d "$ROOT$ACTIONS" ]; then
    TMP2="$ROOT$ACTIONS/.actions_topdevices.new"
    cat > "$TMP2" <<ACT
[install]
command:$SCRIPTS/install.sh
parameters:
type:script
message:Refreshing TopDevices widget
description:Install/refresh TopDevices dashboard widget

[live]
command:$SCRIPTS/live.py
parameters:%s
type:stream_output
message:TopDevices live stream (%s s)

[flows.totals]
command:$SCRIPTS/flows.py totals
parameters:%s %s
type:script_output
message:TopDevices flows totals %s %s

[flows.device]
command:$SCRIPTS/flows.py device
parameters:%s %s %s
type:script_output
message:TopDevices flows device %s

[uninstall]
command:$SCRIPTS/uninstall.sh
parameters:
type:script_output
message:Removing TopDevices widget
ACT
    if cmp -s "$TMP2" "$ROOT$ACTIONS/actions_topdevices.conf" 2>/dev/null; then
        # Unchanged: no restart, so the weekly cron run never restarts configd.
        rm -f "$TMP2"
        echo "configd actions already current"
    else
        mv "$TMP2" "$ROOT$ACTIONS/actions_topdevices.conf"
        chmod 0644 "$ROOT$ACTIONS/actions_topdevices.conf"
        if [ -z "$ROOT" ]; then
            # Detached and a second late. configctl and the cron job run this
            # script under configd; configd_stop only signals configd itself, so
            # the script would survive a synchronous restart - but the configctl
            # caller would lose its reply halfway.
            /usr/sbin/daemon -f /bin/sh -c 'sleep 1; /usr/local/etc/rc.d/configd restart'
            echo "configd actions changed - configd restarts in 1 s"
        else
            echo "configd actions changed - restart skipped (ROOT=$ROOT)"
        fi
    fi
fi

# --- make a new ACL privilege visible now, not when the one-hour cache expires ---
if [ -z "$ROOT" ]; then
    if /usr/local/bin/php -r 'require "/usr/local/opnsense/mvc/script/load_phalcon.php"; (new OPNsense\Core\ACL())->invalidateCache();' >/dev/null 2>&1; then
        echo "ACL cache cleared"
    else
        echo "WARNING: could not clear the ACL cache; the new privilege appears within an hour" >&2
    fi
fi

echo "done - hard-refresh the dashboard (Cmd+Shift+R)"
