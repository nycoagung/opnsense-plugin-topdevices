#!/bin/sh
# Install the TopDevices widget without packaging. Run ON the firewall as root.
#   fetch -o - https://raw.githubusercontent.com/nycoagung/opnsense-plugin-topdevices/main/install.sh | sh
set -e
W=/usr/local/opnsense/www/js/widgets
BASE="${BASE:-https://raw.githubusercontent.com/nycoagung/opnsense-plugin-topdevices/main/src/opnsense/www/js/widgets}"

[ -d "$W/Metadata" ] || { echo "widget dir not found: $W/Metadata" >&2; exit 1; }
fetch -q -o "$W/TopDevices.js.new"          "$BASE/TopDevices.js"
fetch -q -o "$W/Metadata/TopDevices.xml.new" "$BASE/Metadata/TopDevices.xml"
# only swap in once BOTH downloads succeeded, so a failed fetch cannot leave a half-install
mv "$W/TopDevices.js.new"           "$W/TopDevices.js"
mv "$W/Metadata/TopDevices.xml.new" "$W/Metadata/TopDevices.xml"
chmod 0644 "$W/TopDevices.js" "$W/Metadata/TopDevices.xml"
echo "TopDevices widget installed - hard-refresh the dashboard (Cmd+Shift+R)"
