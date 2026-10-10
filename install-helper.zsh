#!/bin/zsh
set -eu
umask 022
if (( EUID != 0 )); then print -u2 'macOS administrator authorization is required.'; exit 1; fi
source_dir=${0:A:h}
account=${1:-}
if [[ ! "$account" =~ '^[a-zA-Z_][a-zA-Z0-9_-]*$' ]]; then print -u2 'Invalid Mac account.'; exit 1; fi
/usr/bin/id "$account" >/dev/null
state_dir='/Library/Application Support/TV Internet Control'
helper='/Library/PrivilegedHelperTools/local.tv-internet-control.sharectl'
sudo_rule='/private/etc/sudoers.d/tv-internet-control'
# The helper independently validates discovered service IDs, adapters and subnet.
"$source_dir/sharectl" check-config "$source_dir/configuration.json"
/usr/bin/install -d -o root -g wheel -m 0755 "$state_dir" /Library/PrivilegedHelperTools /private/etc/sudoers.d
rollback=$(/usr/bin/mktemp -d /private/tmp/tv-sharing-rollback.XXXXXX)
previous_helper=0
previous_rule=0
if [[ -f "$helper" ]]; then /bin/cp -p "$helper" "$rollback/helper"; previous_helper=1; fi
if [[ -f "$sudo_rule" ]]; then /bin/cp -p "$sudo_rule" "$rollback/rule"; previous_rule=1; fi
success=0
cleanup() {
  if (( ! success )); then
    if (( previous_helper )); then /bin/cp -p "$rollback/helper" "$helper"; else /bin/rm -f "$helper"; fi
    if (( previous_rule )); then /bin/cp -p "$rollback/rule" "$sudo_rule"; else /bin/rm -f "$sudo_rule"; fi
  fi
  /bin/rm -rf "$rollback"
}
trap cleanup EXIT
print -r -- "$account ALL=(root) NOPASSWD: $helper status, $helper vpn, $helper direct, $helper restore" > "$rollback/new-rule"
/usr/sbin/visudo -cf "$rollback/new-rule" >/dev/null
/usr/bin/install -o root -g wheel -m 0755 "$source_dir/sharectl" "$helper"
/usr/bin/install -o root -g wheel -m 0440 "$rollback/new-rule" "$sudo_rule"
"$helper" configure "$source_dir/configuration.json"
success=1
print 'Sharing helper installed with four fixed permitted commands.'
