#!/bin/sh
# TopDevices widget installer / updater.
#
# Run once on the firewall as root:
#   fetch -o - https://raw.githubusercontent.com/nycoagung/opnsense-plugin-topdevices/main/install.sh | sh
#
# As well as installing the widget it registers a configd action, so that
# afterwards the very same script can be re-run from System > Settings > Cron
# with no SSH at all. That matters because these files are not owned by any
# package, so a firmware upgrade can remove them.
#
# NOTE: OPNsense cron runs as root regardless - configd executes jobs as root.
# Using cron avoids interactive SSH, not root privileges.
set -e

REPO="${REPO:-https://raw.githubusercontent.com/nycoagung/opnsense-plugin-topdevices/main}"
W=/usr/local/opnsense/www/js/widgets
SCRIPTDIR=/usr/local/opnsense/scripts/topdevices
ACTIONS=/usr/local/opnsense/service/conf/actions.d

[ -d "$W/Metadata" ] || { echo "widget dir not found: $W/Metadata" >&2; exit 1; }

# --- widget files: stage both, swap only once BOTH downloads succeeded, so a
#     failed fetch can never leave a half-installed widget behind ---
fetch -q -o "$W/TopDevices.js.new"           "$REPO/src/opnsense/www/js/widgets/TopDevices.js"
fetch -q -o "$W/Metadata/TopDevices.xml.new" "$REPO/src/opnsense/www/js/widgets/Metadata/TopDevices.xml"
mv "$W/TopDevices.js.new"           "$W/TopDevices.js"
mv "$W/Metadata/TopDevices.xml.new" "$W/Metadata/TopDevices.xml"
chmod 0644 "$W/TopDevices.js" "$W/Metadata/TopDevices.xml"
echo "widget installed"

# --- keep a local copy so cron has something to call ---
mkdir -p "$SCRIPTDIR"
fetch -q -o "$SCRIPTDIR/install.sh.new" "$REPO/install.sh"
mv "$SCRIPTDIR/install.sh.new" "$SCRIPTDIR/install.sh"
chmod 0755 "$SCRIPTDIR/install.sh"

# --- register the configd action (idempotent) ---
if [ -d "$ACTIONS" ]; then
    TMP="$ACTIONS/.actions_topdevices.new"
    cat > "$TMP" <<ACT
[install]
command:$SCRIPTDIR/install.sh
parameters:
type:script
message:Refreshing TopDevices widget
description:Install/refresh TopDevices dashboard widget
ACT
    if cmp -s "$TMP" "$ACTIONS/actions_topdevices.conf" 2>/dev/null; then
        # Unchanged. Do NOT restart configd here: when this script is invoked BY
        # configd (the cron path) restarting it would kill this very process.
        rm -f "$TMP"
        echo "configd action already current"
    else
        mv "$TMP" "$ACTIONS/actions_topdevices.conf"
        chmod 0644 "$ACTIONS/actions_topdevices.conf"
        service configd restart >/dev/null 2>&1 || true
        echo "configd action 'topdevices install' registered"
    fi
fi

echo "done - hard-refresh the dashboard (Cmd+Shift+R)"
