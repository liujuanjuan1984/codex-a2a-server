#!/usr/bin/env bash

ensure_sudo_ready() {
  local non_interactive_message="${1:-sudo requires a password or is not permitted (non-interactive). Refusing to continue.}"
  local interactive_hint="${2:-Run in an interactive shell, or configure NOPASSWD for required commands.}"

  if ! command -v sudo >/dev/null 2>&1; then
    return 127
  fi

  if sudo -n true 2>/dev/null; then
    return 0
  fi

  if [[ -t 0 ]]; then
    sudo -v
    return $?
  fi

  echo "$non_interactive_message" >&2
  echo "$interactive_hint" >&2
  return 1
}
