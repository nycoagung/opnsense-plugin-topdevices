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
#     the firewall's own public IP that counts). Three files is survivable, but
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
# NOTE: OPNsense cron runs as root regardless - configd executes jobs as root.
# Using cron avoids interactive SSH, not root privileges.
set -e

GH_OWNER="${GH_OWNER:-nycoagung}"
GH_REPO="${GH_REPO:-opnsense-plugin-topdevices}"
GH_REF="${GH_REF:-main}"
P=src/opnsense/www/js/widgets

W=/usr/local/opnsense/www/js/widgets
SCRIPTDIR=/usr/local/opnsense/scripts/topdevices
ACTIONS=/usr/local/opnsense/service/conf/actions.d

[ -d "$W/Metadata" ] || { echo "widget dir not found: $W/Metadata" >&2; exit 1; }

FILES="
$P/TopDevices.js|$W/TopDevices.js
$P/Metadata/TopDevices.xml|$W/Metadata/TopDevices.xml
install.sh|$SCRIPTDIR/install.sh
"

HERE=$(dirname "$0")
CLEAN=""
if [ -d "$HERE/$P" ]; then
    SRC="$HERE"
    echo "installing from $SRC"
else
    TMP=$(mktemp -d /tmp/tdinst.XXXXXX)
    CLEAN="$TMP"
    echo "fetching ${GH_OWNER}/${GH_REPO}@${GH_REF} from codeload"
    fetch -qT 30 -o "$TMP/src.tgz" \
        "https://codeload.github.com/${GH_OWNER}/${GH_REPO}/tar.gz/refs/heads/${GH_REF}"
    tar -xzf "$TMP/src.tgz" -C "$TMP"
    SRC=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)
    [ -n "$SRC" ] && [ -d "$SRC/$P" ] || { echo "archive did not contain $P" >&2; exit 1; }
fi

# Check the whole set is present before touching anything on disk, so a
# truncated archive cannot leave a half-installed widget behind.
for entry in $FILES; do
    [ -n "$entry" ] || continue
    s=${entry%%|*}
    [ -f "$SRC/$s" ] || { echo "missing from source: $s" >&2; exit 1; }
done

mkdir -p "$SCRIPTDIR"

# Stage beside the destination, then rename into place. Not only for atomicity:
# this script installs ITSELF, and sh reads a script incrementally, so a cp over
# the running file shifts the shell's read offset and it dies mid-script. mv
# gives the file a new inode and leaves the running descriptor untouched.
for entry in $FILES; do
    [ -n "$entry" ] || continue
    s=${entry%%|*}; d=${entry#*|}
    mkdir -p "$(dirname "$d")"
    cp "$SRC/$s" "$d.tdnew"
done
for entry in $FILES; do
    [ -n "$entry" ] || continue
    s=${entry%%|*}; d=${entry#*|}
    mv "$d.tdnew" "$d"
    case "$d" in *.sh) chmod 0755 "$d" ;; *) chmod 0644 "$d" ;; esac
    printf '  %-26s %6d bytes\n' "$(basename "$s")" "$(wc -c < "$d" | tr -d ' ')"
done
[ -n "$CLEAN" ] && rm -rf "$CLEAN"
echo "widget installed"

# --- register the configd action (idempotent) ---
if [ -d "$ACTIONS" ]; then
    TMP2="$ACTIONS/.actions_topdevices.new"
    cat > "$TMP2" <<ACT
[install]
command:$SCRIPTDIR/install.sh
parameters:
type:script
message:Refreshing TopDevices widget
description:Install/refresh TopDevices dashboard widget
ACT
    if cmp -s "$TMP2" "$ACTIONS/actions_topdevices.conf" 2>/dev/null; then
        # Unchanged. Do NOT restart configd here: when this script is invoked BY
        # configd (the cron path) restarting it would kill this very process.
        rm -f "$TMP2"
        echo "configd action already current"
    else
        mv "$TMP2" "$ACTIONS/actions_topdevices.conf"
        chmod 0644 "$ACTIONS/actions_topdevices.conf"
        service configd restart >/dev/null 2>&1 || true
        echo "configd action 'topdevices install' registered"
    fi
fi

echo "done - hard-refresh the dashboard (Cmd+Shift+R)"
