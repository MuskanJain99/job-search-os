#!/usr/bin/env bash
# Installs the laptop helper on macOS: a virtualenv, Playwright, an env file and a launchd job that
# runs `jobsearch laptop auto` every 2 hours and when you log in. Uses your installed Google Chrome
# with a SEPARATE profile at ~/.job-search-os/chrome-profile (never your everyday profile).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="$HOME/.job-search-os"
VENV="$HOME_DIR/venv"
ENV_FILE="$HOME_DIR/env"
PLIST="$HOME/Library/LaunchAgents/com.jobsearchos.laptop.plist"

mkdir -p "$HOME_DIR/logs"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -e "$REPO_DIR" >/dev/null

if [ ! -f "$ENV_FILE" ]; then
  cp "$REPO_DIR/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE — fill in DATABASE_URL, the LLM key/model, and set DRY_RUN/SUBMIT_ENABLED when ready."
fi

cat > "$HOME_DIR/run.sh" <<EOF
#!/usr/bin/env bash
set -a; source "$ENV_FILE"; set +a
cd "$REPO_DIR"
exec "$VENV/bin/python" -m jobsearch laptop auto >> "$HOME_DIR/logs/laptop.log" 2>&1
EOF
chmod +x "$HOME_DIR/run.sh"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jobsearchos.laptop</string>
  <key>ProgramArguments</key><array><string>$HOME_DIR/run.sh</string></array>
  <key>StartInterval</key><integer>7200</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME_DIR/logs/launchd.err</string>
</dict></plist>
EOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo
echo "Installed. Next:"
echo "  1) Edit $ENV_FILE"
echo "  2) Sign in once:  set -a; source $ENV_FILE; set +a; $VENV/bin/python -m jobsearch laptop login"
echo "  3) Finish blocked applications any time:  $VENV/bin/python -m jobsearch laptop assist"
echo "Uninstall: launchctl unload $PLIST && rm $PLIST"
