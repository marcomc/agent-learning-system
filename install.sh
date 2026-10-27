#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIG_DIR="${XDG_CONFIG_HOME:-"${HOME}/.config"}/agent-learning-system"
CONFIG_FILE="${CONFIG_DIR}/config.env"
DEFAULT_LEARNING_DIR="${HOME}/Library/Mobile Documents/iCloud~md~obsidian/Documents/Obsidian Vault"
DEFAULT_EMAIL="user@example.com"
LEARNING_DIR=""
OBSIDIAN_PROVIDER="direct"
EMAIL="${DEFAULT_EMAIL}"
EMAIL_PROVIDER="gmail"
SKILL_MODE="copy"
INSTALL_AUTOMATIONS="1"
AUTONOMOUS_REVIEW="0"
REVIEW_GROUP_THRESHOLD="8"
VALIDATE_ONLY="0"
usage() {
  printf '%s\n' "Usage: $0 [--dir PATH] [--vault PATH] [--email ADDRESS] [--email-provider gmail|msmtp] [--autonomous-review|--manual-review] [--review-group-threshold N] [--skip-automations] [--validate-only]"
}

validate_project() {
  bash "${SCRIPT_DIR}/scripts/check.sh"
}

quote_env() {
  local value
  value=${1//\\/\\\\}
  value=${value//\"/\\\"}
  printf '"%s"' "${value}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir|--vault)
      LEARNING_DIR=${2:-}
      shift 2
      ;;
    --email)
      EMAIL=${2:-}
      shift 2
      ;;
    --email-provider)
      EMAIL_PROVIDER=${2:-}
      shift 2
      ;;
    --skip-automations)
      INSTALL_AUTOMATIONS="0"
      shift
      ;;
    --autonomous-review)
      AUTONOMOUS_REVIEW="1"
      shift
      ;;
    --validate-only)
      VALIDATE_ONLY="1"
      shift
      ;;
    --manual-review)
      AUTONOMOUS_REVIEW="0"
      shift
      ;;
    --review-group-threshold)
      REVIEW_GROUP_THRESHOLD=${2:-}
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "${REVIEW_GROUP_THRESHOLD}" =~ ^[1-9][0-9]*$ ]]; then
  printf '%s\n' "--review-group-threshold must be a positive integer." >&2
  exit 2
fi

if [[ "${VALIDATE_ONLY}" == "1" ]]; then
  validate_project
  exit 0
fi

if [[ "${EMAIL_PROVIDER}" != "gmail" && "${EMAIL_PROVIDER}" != "msmtp" ]]; then
  printf 'Unsupported --email-provider: %s\n' "${EMAIL_PROVIDER}" >&2
  exit 2
fi

if [[ -z "${LEARNING_DIR}" ]]; then
  if [[ -d "${DEFAULT_LEARNING_DIR}" ]]; then
    LEARNING_DIR=${DEFAULT_LEARNING_DIR}
  elif [[ -t 0 ]]; then
    printf '%s\n' "Could not find the default learning directory."
    printf '%s\n' "Choose an access mode:"
    printf '%s\n' "1) Direct filesystem directory path (recommended)"
    printf '%s\n' "2) MCP mode (advanced; automations remain pending until configured)"
    read -r -p "Selection [1]: " selection
    selection=${selection:-1}
    if [[ "${selection}" == "2" ]]; then
      OBSIDIAN_PROVIDER="mcp-pending"
      LEARNING_DIR="${HOME}/Agent Learning"
      printf '%s\n' "MCP mode recorded as pending. Configure an Obsidian MCP before enabling automations."
    else
      read -r -p "Learning base directory path: " LEARNING_DIR
      OBSIDIAN_PROVIDER="direct"
    fi
  else
    OBSIDIAN_PROVIDER="mcp-pending"
    LEARNING_DIR="${HOME}/Agent Learning"
  fi
fi

if [[ "${OBSIDIAN_PROVIDER}" == "direct" && ! -d "${LEARNING_DIR}" ]]; then
  printf 'Learning base directory path does not exist: %s\n' "${LEARNING_DIR}" >&2
  exit 1
fi

validate_project

mkdir -p "${CONFIG_DIR}"
repo_value=$(quote_env "${SCRIPT_DIR}")
learning_dir_value=$(quote_env "${LEARNING_DIR}")
store_name_value=$(quote_env "AI Agent Learnings")
obsidian_provider_value=$(quote_env "${OBSIDIAN_PROVIDER}")
email_value=$(quote_env "${EMAIL}")
email_provider_value=$(quote_env "${EMAIL_PROVIDER}")
skill_mode_value=$(quote_env "${SKILL_MODE}")
{
  printf 'AGENT_LEARNING_REPO=%s\n' "${repo_value}"
  printf 'AGENT_LEARNING_DIR=%s\n' "${learning_dir_value}"
  printf 'AGENT_LEARNING_STORE_NAME=%s\n' "${store_name_value}"
  printf 'AGENT_LEARNING_OBSIDIAN_PROVIDER=%s\n' "${obsidian_provider_value}"
  printf 'AGENT_LEARNING_EMAIL=%s\n' "${email_value}"
  printf 'AGENT_LEARNING_EMAIL_PROVIDER=%s\n' "${email_provider_value}"
  printf 'AGENT_LEARNING_SKILL_MODE=%s\n' "${skill_mode_value}"
  printf 'AGENT_LEARNING_AUTONOMOUS_REVIEW="%s"\n' "${AUTONOMOUS_REVIEW}"
  printf 'AGENT_LEARNING_REVIEW_GROUP_THRESHOLD="%s"\n' "${REVIEW_GROUP_THRESHOLD}"
} > "${CONFIG_FILE}"

python3 "${SCRIPT_DIR}/scripts/agent_learning.py" --config "${CONFIG_FILE}" init-store >/dev/null

install_path() {
  local source=$1
  local dest=$2
  local backup
  local timestamp

  mkdir -p "$(dirname -- "${dest}")"
  timestamp=$(date +%Y%m%d%H%M%S)

  if [[ -e "${dest}" || -L "${dest}" ]]; then
    backup="${dest}.backup.${timestamp}"
    mv "${dest}" "${backup}"
    printf 'Backed up existing %s to %s\n' "${dest}" "${backup}"
  fi

  cp -R "${source}" "${dest}"
}

install_codex_link() {
  local agent_dest=$1
  local codex_dest=$2
  local backup
  local existing_target
  local timestamp

  mkdir -p "$(dirname -- "${codex_dest}")"
  timestamp=$(date +%Y%m%d%H%M%S)
  existing_target=""
  if [[ -L "${codex_dest}" ]]; then
    existing_target=$(readlink "${codex_dest}")
  fi
  if [[ -e "${codex_dest}" || -L "${codex_dest}" ]]; then
    if [[ -L "${codex_dest}" && "${existing_target}" == "${agent_dest}" ]]; then
      return 0
    fi
    backup="${codex_dest}.backup.${timestamp}"
    mv "${codex_dest}" "${backup}"
    printf 'Backed up existing %s to %s\n' "${codex_dest}" "${backup}"
  fi
  ln -s "${agent_dest}" "${codex_dest}"
}

install_automations() {
  printf '%s\n' "Codex scheduled tasks require app setup. Existing tasks were not changed."
  printf 'Open %s/automations/README.md for project and chat setup.\n' "${SCRIPT_DIR}"
}

for skill in record-agent-learning consolidate-agent-learnings; do
  source="${SCRIPT_DIR}/skills/${skill}"
  agent_dest="${HOME}/.agents/skills/${skill}"
  codex_dest="${HOME}/.codex/skills/${skill}"
  install_path "${source}" "${agent_dest}"
  install_codex_link "${agent_dest}" "${codex_dest}"
done

if [[ "${INSTALL_AUTOMATIONS}" == "1" ]]; then
  install_automations
else
  printf '%s\n' "Skipped Codex automation installation."
fi

if [[ "${EMAIL_PROVIDER}" == "gmail" ]]; then
  printf '%s\n' "Email provider is Gmail connector. If unavailable, configure msmtp and rerun with --email-provider msmtp."
elif ! command -v msmtp >/dev/null 2>&1; then
  printf '%s\n' "msmtp is not installed; install/configure it before enabling local email fallback."
fi
if [[ "${EMAIL}" == "${DEFAULT_EMAIL}" ]]; then
  printf '%s\n' "Review email is a placeholder; rerun with --email ADDRESS before relying on morning notifications."
fi

printf 'Installed Agent Learning System using config %s\n' "${CONFIG_FILE}"
printf '%s\n' "Codex automation prompts are in ${SCRIPT_DIR}/automations/."
