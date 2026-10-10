#!/bin/zsh
set -eu
app='/Applications/Relay.app'
if [[ ! -d "$app" ]]; then print 'Relay is not installed in Applications.'; exit 1; fi
"$app/Contents/MacOS/Relay" --remove-login-item
# Terminate the exact installed app; its signal handler closes its child server.
app_pids=("${(@f)$(/usr/bin/pgrep -f '^/Applications/Relay.app/Contents/MacOS/Relay( |$)' || true)}")
if [[ -n "${app_pids[1]:-}" ]]; then /bin/kill -TERM "${app_pids[@]}"; /bin/sleep 1; fi
stage=$(/usr/bin/mktemp -d /private/tmp/relay-remove.XXXXXX)
trap '/bin/rm -rf "$stage"' EXIT
/bin/cp "$app/Contents/Resources/website/remove-helper.zsh" "$stage/"
/usr/bin/osascript - "$stage/remove-helper.zsh" <<'APPLESCRIPT'
on run argv
do shell script "/bin/zsh " & quoted form of item 1 of argv with administrator privileges
end run
APPLESCRIPT
/bin/mkdir -p "$HOME/.Trash"
/bin/mv "$app" "$HOME/.Trash/Relay-$(/bin/date +%Y%m%d-%H%M%S).app"
print 'Relay was moved to your Trash. Internet Sharing was restored and the Login Item removed.'
