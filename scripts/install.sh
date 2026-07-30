#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${HOME}/ollie-hermes-orchestrator"
CONFIG_DIR="${HOME}/.config/ollie-orchestrator"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
ENV_FILE="${CONFIG_DIR}/.env"

if [[ ! -d "${REPO_DIR}" ]]; then
  echo "error: ${REPO_DIR} does not exist. Clone first: git clone <url> ~/ollie-hermes-orchestrator"
  exit 1
fi

cd "${REPO_DIR}"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
pip install -q -r requirements.txt
deactivate

mkdir -p "${CONFIG_DIR}"
chmod 700 "${CONFIG_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
  KEY="$(openssl rand -hex 32)"
  cat > "${ENV_FILE}" <<EOF
ORCHESTRATOR_KEY=${KEY}
HERMES_GATEWAY_KEY=
HERMES_STACK_DIR=${HOME}/hermes-stack
HERMES_PROFILES_DIR=${HOME}/.hermes/profiles
SYSTEMD_USER_DIR=${SYSTEMD_DIR}
# Agent-to-agent dispatch. Instance-wide: this gates consults for every profile
# on the box. Set to "direct" AND set DISPATCH_MODE per profile to enable.
# See docs/runbooks/agent-dispatch.md.
DISPATCH_MODE=off
EOF
  chmod 600 "${ENV_FILE}"
  echo "Generated ${ENV_FILE}. Edit it to paste your HERMES_GATEWAY_KEY (from ~/hermes-stack/.env)."
  echo ""
  echo "Save this for your dashboard's .env:"
  echo "  ORCHESTRATOR_KEY=${KEY}"
  echo ""
fi

mkdir -p "${SYSTEMD_DIR}"
cp "${REPO_DIR}/systemd/ollie-orchestrator.service" "${SYSTEMD_DIR}/ollie-orchestrator.service"
systemctl --user daemon-reload
systemctl --user enable --now ollie-orchestrator

echo "Installed."
echo "Verify (once HERMES_GATEWAY_KEY is set):"
echo "  curl -fsSL http://localhost:9123/healthz"
echo "  curl -fsSL -H \"Authorization: Bearer \$ORCHESTRATOR_KEY\" http://localhost:9123/v1/agents"
