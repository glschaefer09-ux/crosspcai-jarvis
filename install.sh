#!/usr/bin/env bash
# JARVAS installer — Linux and macOS.
#
#   sudo ./install.sh              install, put one icon in the launcher
#   sudo ./install.sh --server     also enable the background service
#   sudo ./install.sh --uninstall  remove it
#
# Works from a built binary (dist/JARVAS) or straight from source.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="/opt/jarvas"
BIN="/usr/local/bin/jarvas"
OS="$(uname -s)"
MODE="desktop"

for arg in "$@"; do
  case "$arg" in
    --server)    MODE="server" ;;
    --uninstall) MODE="uninstall" ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[36m▸\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this with sudo"

# ── uninstall ────────────────────────────────────────────────────────────────
if [ "$MODE" = "uninstall" ]; then
  say "Stopping services"
  systemctl --user stop crosspcai-jarvas 2>/dev/null || true
  systemctl --user disable crosspcai-jarvas 2>/dev/null || true
  rm -rf "$PREFIX" "$BIN"
  rm -f /usr/share/applications/jarvas.desktop
  rm -f /usr/share/icons/hicolor/512x512/apps/jarvas.png
  command -v update-desktop-database >/dev/null && update-desktop-database || true
  say "JARVAS removed. Your data in ~/.crosspcai was left alone."
  exit 0
fi

# ── locate what to install ───────────────────────────────────────────────────
if [ -d "$ROOT/dist/JARVAS" ]; then
  SOURCE="$ROOT/dist/JARVAS"; KIND="binary"
elif [ -d "$ROOT/jarvas" ]; then
  SOURCE="$ROOT"; KIND="source"
  command -v python3 >/dev/null || die "python3 is required for a source install"
  PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  case "$PYV" in 3.1[0-9]|3.[2-9]*) ;; *) die "Python 3.10+ required (found $PYV)" ;; esac
else
  die "no build found — run: python3 packaging/build.py"
fi
say "Installing from $KIND: $SOURCE"

# ── install ──────────────────────────────────────────────────────────────────
rm -rf "$PREFIX"; mkdir -p "$PREFIX"

if [ "$KIND" = "binary" ]; then
  cp -R "$SOURCE"/. "$PREFIX"/
  chmod +x "$PREFIX/jarvas"
  LAUNCH="$PREFIX/jarvas"
else
  cp -R "$SOURCE/jarvas" "$PREFIX/"
  cat > "$PREFIX/jarvas" <<EOF
#!/bin/sh
exec python3 -m jarvas "\$@"
EOF
  chmod +x "$PREFIX/jarvas"
  # Source installs get the desktop extras; they are optional everywhere else.
  python3 -m pip install --quiet --upgrade pywebview pystray pillow 2>/dev/null \
    || say "note: install pywebview/pystray for a native window (browser used otherwise)"
  LAUNCH="$PREFIX/jarvas"
fi

ln -sf "$LAUNCH" "$BIN"
say "Installed to $PREFIX (command: jarvas)"

# ── one app icon ─────────────────────────────────────────────────────────────
if [ "$OS" = "Linux" ]; then
  install -Dm644 "$PREFIX/jarvas/assets/icon.png" \
    /usr/share/icons/hicolor/512x512/apps/jarvas.png 2>/dev/null || \
  install -Dm644 "$ROOT/jarvas/assets/icon.png" \
    /usr/share/icons/hicolor/512x512/apps/jarvas.png
  sed "s|^Exec=.*|Exec=$LAUNCH|" "$ROOT/packaging/jarvas.desktop" \
    > /usr/share/applications/jarvas.desktop
  chmod 644 /usr/share/applications/jarvas.desktop
  command -v update-desktop-database >/dev/null && update-desktop-database || true
  command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true
  say "Added JARVAS to your applications menu"

  # Start at login for the person who ran sudo, not for root. The binary owns
  # this logic (jarvas/installer.py) so there is one implementation of it.
  if [ "$MODE" = "desktop" ] && [ -n "${SUDO_USER:-}" ]; then
    su - "$SUDO_USER" -c "'$LAUNCH' --autostart on" >/dev/null 2>&1 \
      && say "JARVAS will start when $SUDO_USER signs in" \
      || say "note: turn on start-at-login from Settings when you first open it"
  fi
fi

# ── optional background service ──────────────────────────────────────────────
if [ "$MODE" = "server" ] && [ "$OS" = "Linux" ]; then
  REAL_USER="${SUDO_USER:-$USER}"
  UNIT_DIR="$(getent passwd "$REAL_USER" | cut -d: -f6)/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  sed "s|^ExecStart=.*|ExecStart=$LAUNCH --server --bind 0.0.0.0|" \
    "$ROOT/packaging/crosspcai-jarvas.service" > "$UNIT_DIR/crosspcai-jarvas.service"
  chown -R "$REAL_USER": "$UNIT_DIR"
  loginctl enable-linger "$REAL_USER" 2>/dev/null || true
  su - "$REAL_USER" -c "systemctl --user daemon-reload && \
    systemctl --user enable --now crosspcai-jarvas" || \
    say "enable it yourself: systemctl --user enable --now crosspcai-jarvas"
  say "Service running — reachable on port 5580"
fi

cat <<EOF

  JARVAS is installed.

    Open it        click the JARVAS icon, or run: jarvas
    Server mode    jarvas --server
    Health check   jarvas --status

  First launch walks you through setup. Nothing leaves your machine
  unless you switch reporting on and press Send.

EOF
