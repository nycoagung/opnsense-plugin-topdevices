#!/bin/sh
# TopDevices widget installer / updater.
#
# Run once on the firewall as root:
#   fetch -o - https://raw.githubusercontent.com/nycoagung/opnsense-plugin-topdevices/main/install.sh | sh
#
# It installs the widget, then registers a configd action so later refreshes can
# run from System > Settings > Cron without SSH. These files are not owned by any
# package, so a firmware upgrade can remove them.
#
# NOTE: OPNsense cron runs as root regardless - configd executes jobs as root.
# Using cron avoids interactive SSH, not root privileges.
#
# WHY THE GITHUB API AND NOT raw.githubusercontent:
# raw is CDN-cached, the cache lags pushes by minutes, and it is cached per edge
# so two machines can see different content at the same moment. Three installs in
# a row silently fetched stale files and reported success. The API is
# authoritative, and every file is verified against the git blob SHA it reports,
# so a truncated or stale download fails loudly instead of installing quietly.
set -e

GH_OWNER="${GH_OWNER:-nycoagung}"
GH_REPO="${GH_REPO:-opnsense-plugin-topdevices}"
GH_REF="${GH_REF:-main}"
SRC="src/opnsense/www/js/widgets"

W=/usr/local/opnsense/www/js/widgets
SCRIPTDIR=/usr/local/opnsense/scripts/topdevices
ACTIONS=/usr/local/opnsense/service/conf/actions.d

[ -d "$W/Metadata" ] || { echo "widget dir not found: $W/Metadata" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

# fetch_verified <repo-path> <destination>
fetch_verified() {
    python3 -c '
import base64, hashlib, json, sys, urllib.request
owner, repo, ref, path, dest = sys.argv[1:6]
url = "https://api.github.com/repos/%s/%s/contents/%s?ref=%s" % (owner, repo, path, ref)
req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                           "User-Agent": "topdevices-installer"})
with urllib.request.urlopen(req, timeout=60) as r:
    meta = json.load(r)
data = base64.b64decode(meta["content"])
want = meta["sha"]
got = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
if got != want:
    sys.stderr.write("SHA MISMATCH %s\n  expected %s\n  got      %s\n" % (path, want, got))
    sys.exit(1)
with open(dest, "wb") as f:
    f.write(data)
sys.stderr.write("  %-22s %s  %d bytes\n" % (path.rsplit("/", 1)[-1], want[:12], len(data)))
' "$GH_OWNER" "$GH_REPO" "$GH_REF" "$1" "$2"
}

echo "fetching from github api (${GH_OWNER}/${GH_REPO}@${GH_REF}):"

# Stage everything first, swap only once every download has verified, so a bad
# fetch can never leave a half-installed widget behind.
fetch_verified "$SRC/TopDevices.js"           "$W/TopDevices.js.new"
fetch_verified "$SRC/Metadata/TopDevices.xml" "$W/Metadata/TopDevices.xml.new"
mkdir -p "$SCRIPTDIR"
fetch_verified "install.sh"                   "$SCRIPTDIR/install.sh.new"

mv "$W/TopDevices.js.new"           "$W/TopDevices.js"
mv "$W/Metadata/TopDevices.xml.new" "$W/Metadata/TopDevices.xml"
mv "$SCRIPTDIR/install.sh.new"      "$SCRIPTDIR/install.sh"
chmod 0644 "$W/TopDevices.js" "$W/Metadata/TopDevices.xml"
chmod 0755 "$SCRIPTDIR/install.sh"
echo "widget installed"

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
