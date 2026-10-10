#!/bin/zsh
set -eu
if (( EUID != 0 )); then print -u2 'Administrator authorization is required.'; exit 1; fi
helper='/Library/PrivilegedHelperTools/local.tv-internet-control.sharectl'
state_dir='/Library/Application Support/TV Internet Control'
if [[ -x "$helper" ]]; then "$helper" restore; fi
/bin/rm -f /private/etc/sudoers.d/tv-internet-control "$helper" "$state_dir/configuration.json" "$state_dir/relay-pf.conf"
print 'Original sharing restored; helper access removed. The backup is preserved.'
