#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIG_DIR="${XDG_CONFIG_HOME:-"${HOME}/.config"}/agent-learning-system"
CONFIG_FILE="${CONFIG_DIR}/config.env"
CODEX_HOME="${CODEX_HOME:-"${HOME}/.codex"}"
DEFAULT_VAULT="${HOME}/Library/Mobile Documents/iCloud~md~obsidian/Documents/Obsidian Vault"
DEFAULT_EMAIL="user@example.com"
VAULT_PATH=""
OBSIDIAN_PROVIDER="direct"
EMAIL="${DEFAULT_EMAIL}"
EMAIL_PROVIDER="gmail"
SKILL_MODE="symlink"
INSTALL_AUTOMATIONS="1"
MARKDOWN_LIST=""

cleanup() {
  if [[ -n "${MARKDOWN_LIST}" && -f "${MARKDOWN_LIST}" ]]; then
    rm -f "${MARKDOWN_LIST}"
  fi
}

trap cleanup EXIT

usage() {
  printf '%s\n' "Usage: $0 [--vault PATH] [--email ADDRESS] [--email-provider gmail|msmtp] [--mode symlink|copy] [--skip-automations]"
}

validate_project() {
  markdown_files=()
  MARKDOWN_LIST=$(mktemp)
  find "${SCRIPT_DIR}" -name '*.md' -not -path '*/.git/*' -print0 > "${MARKDOWN_LIST}"
  while IFS= read -r -d '' file; do
    markdown_files+=("${file}")
  done < "${MARKDOWN_LIST}"

  if [[ ${#markdown_files[@]} -gt 0 ]]; then
    markdownlint --config "${HOME}/.markdownlint.json" "${markdown_files[@]}"
  fi

  shellcheck --enable=all "${SCRIPT_DIR}/install.sh"
  python3 -m py_compile "${SCRIPT_DIR}/scripts/agent_learning.py" "${SCRIPT_DIR}/tests/test_agent_learning.py"
  python3 -m unittest discover -s "${SCRIPT_DIR}/tests"
}

quote_env() {
  local value
  value=${1//\\/\\\\}
  value=${value//\"/\\\"}
  printf '"%s"' "${value}"
}

toml_string() {
  quote_env "$1"
}

timestamp_ms() {
  python3 -c 'import time; print(int(time.time() * 1000))'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vault)
      VAULT_PATH=${2:-}
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
    --mode)
      SKILL_MODE=${2:-}
      shift 2
      ;;
    --skip-automations)
      INSTALL_AUTOMATIONS="0"
      shift
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

if [[ "${SKILL_MODE}" != "symlink" && "${SKILL_MODE}" != "copy" ]]; then
  printf 'Unsupported --mode: %s\n' "${SKILL_MODE}" >&2
  exit 2
fi

if [[ "${EMAIL_PROVIDER}" != "gmail" && "${EMAIL_PROVIDER}" != "msmtp" ]]; then
  printf 'Unsupported --email-provider: %s\n' "${EMAIL_PROVIDER}" >&2
  exit 2
fi

if [[ -z "${VAULT_PATH}" ]]; then
  if [[ -d "${DEFAULT_VAULT}" ]]; then
    VAULT_PATH=${DEFAULT_VAULT}
  elif [[ -t 0 ]]; then
    printf '%s\n' "Could not find the default Obsidian vault."
    printf '%s\n' "Choose an access mode:"
    printf '%s\n' "1) Direct Obsidian directory path (recommended)"
    printf '%s\n' "2) MCP mode (advanced; automations remain pending until configured)"
    read -r -p "Selection [1]: " selection
    selection=${selection:-1}
    if [[ "${selection}" == "2" ]]; then
      OBSIDIAN_PROVIDER="mcp-pending"
      VAULT_PATH="${HOME}/Obsidian Vault"
      printf '%s\n' "MCP mode recorded as pending. Configure an Obsidian MCP before enabling automations."
    else
      read -r -p "Obsidian vault path: " VAULT_PATH
      OBSIDIAN_PROVIDER="direct"
    fi
  else
    OBSIDIAN_PROVIDER="mcp-pending"
    VAULT_PATH="${HOME}/Obsidian Vault"
  fi
fi

if [[ "${OBSIDIAN_PROVIDER}" == "direct" && ! -d "${VAULT_PATH}" ]]; then
  printf 'Obsidian vault path does not exist: %s\n' "${VAULT_PATH}" >&2
  exit 1
fi

validate_project

mkdir -p "${CONFIG_DIR}"
repo_value=$(quote_env "${SCRIPT_DIR}")
vault_value=$(quote_env "${VAULT_PATH}")
dir_value=$(quote_env "AI Agent Learnings")
obsidian_provider_value=$(quote_env "${OBSIDIAN_PROVIDER}")
email_value=$(quote_env "${EMAIL}")
email_provider_value=$(quote_env "${EMAIL_PROVIDER}")
skill_mode_value=$(quote_env "${SKILL_MODE}")
{
  printf 'AGENT_LEARNING_REPO=%s\n' "${repo_value}"
  printf 'AGENT_LEARNING_VAULT=%s\n' "${vault_value}"
  printf 'AGENT_LEARNING_DIR=%s\n' "${dir_value}"
  printf 'AGENT_LEARNING_OBSIDIAN_PROVIDER=%s\n' "${obsidian_provider_value}"
  printf 'AGENT_LEARNING_EMAIL=%s\n' "${email_value}"
  printf 'AGENT_LEARNING_EMAIL_PROVIDER=%s\n' "${email_provider_value}"
  printf 'AGENT_LEARNING_SKILL_MODE=%s\n' "${skill_mode_value}"
} > "${CONFIG_FILE}"

python3 "${SCRIPT_DIR}/scripts/agent_learning.py" --config "${CONFIG_FILE}" init-store >/dev/null

install_path() {
  local source=$1
  local dest=$2
  local backup
  local existing_target
  local timestamp

  mkdir -p "$(dirname -- "${dest}")"
  timestamp=$(date +%Y%m%d%H%M%S)
  existing_target=""
  if [[ -L "${dest}" ]]; then
    existing_target=$(readlink "${dest}")
  fi

  if [[ -e "${dest}" || -L "${dest}" ]]; then
    if [[ "${SKILL_MODE}" == "symlink" && -L "${dest}" && "${existing_target}" == "${source}" ]]; then
      return 0
    fi
    backup="${dest}.backup.${timestamp}"
    mv "${dest}" "${backup}"
    printf 'Backed up existing %s to %s\n' "${dest}" "${backup}"
  fi

  if [[ "${SKILL_MODE}" == "symlink" ]]; then
    ln -s "${source}" "${dest}"
  else
    cp -R "${source}" "${dest}"
  fi
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

write_automation() {
  local id=$1
  local name=$2
  local prompt=$3
  local rrule=$4
  local reasoning_effort=$5
  local automation_dir
  local automation_file
  local backup
  local created_at
  local cwd_value
  local current_ms
  local desired
  local existing_updated_at
  local id_value
  local name_value
  local prompt_value
  local reasoning_effort_value
  local rrule_value
  local timestamp

  automation_dir="${CODEX_HOME}/automations/${id}"
  automation_file="${automation_dir}/automation.toml"
  mkdir -p "${automation_dir}"

  current_ms=$(timestamp_ms)
  created_at="${current_ms}"
  existing_updated_at="${created_at}"
  if [[ -f "${automation_file}" ]]; then
    created_at=$(sed -n 's/^created_at = //p' "${automation_file}" | head -n 1)
    if [[ -z "${created_at}" ]]; then
      created_at="${current_ms}"
    fi
    existing_updated_at=$(sed -n 's/^updated_at = //p' "${automation_file}" | head -n 1)
    if [[ -z "${existing_updated_at}" ]]; then
      existing_updated_at="${created_at}"
    fi
  fi
  id_value=$(toml_string "${id}")
  name_value=$(toml_string "${name}")
  prompt_value=$(toml_string "${prompt}")
  rrule_value=$(toml_string "${rrule}")
  reasoning_effort_value=$(toml_string "${reasoning_effort}")
  cwd_value=$(toml_string "${SCRIPT_DIR}")

  desired=$(mktemp)
  {
    printf 'version = 1\n'
    printf 'id = %s\n' "${id_value}"
    printf 'kind = "cron"\n'
    printf 'name = %s\n' "${name_value}"
    printf 'prompt = %s\n' "${prompt_value}"
    printf 'status = "ACTIVE"\n'
    printf 'rrule = %s\n' "${rrule_value}"
    printf 'model = "gpt-5.2"\n'
    printf 'reasoning_effort = %s\n' "${reasoning_effort_value}"
    printf 'execution_environment = "local"\n'
    printf 'cwds = [%s]\n' "${cwd_value}"
    printf 'created_at = %s\n' "${created_at}"
    printf 'updated_at = %s\n' "${existing_updated_at}"
  } > "${desired}"

  if [[ -f "${automation_file}" ]] && cmp -s "${automation_file}" "${desired}"; then
    rm -f "${desired}"
    printf 'Automation already active: %s\n' "${id}"
    return 0
  fi

  {
    printf 'version = 1\n'
    printf 'id = %s\n' "${id_value}"
    printf 'kind = "cron"\n'
    printf 'name = %s\n' "${name_value}"
    printf 'prompt = %s\n' "${prompt_value}"
    printf 'status = "ACTIVE"\n'
    printf 'rrule = %s\n' "${rrule_value}"
    printf 'model = "gpt-5.2"\n'
    printf 'reasoning_effort = %s\n' "${reasoning_effort_value}"
    printf 'execution_environment = "local"\n'
    printf 'cwds = [%s]\n' "${cwd_value}"
    printf 'created_at = %s\n' "${created_at}"
    printf 'updated_at = %s\n' "${current_ms}"
  } > "${desired}"

  if [[ -f "${automation_file}" ]]; then
    timestamp=$(date +%Y%m%d%H%M%S)
    backup="${automation_file}.backup.${timestamp}"
    cp "${automation_file}" "${backup}"
    printf 'Backed up existing automation %s to %s\n' "${id}" "${backup}"
  fi

  mv "${desired}" "${automation_file}"
  printf 'Installed active automation: %s\n' "${id}"
}

install_automations() {
  local global_agents_file
  local markdownlint_config
  local midnight_prompt
  local morning_prompt
  local noon_prompt

  if [[ "${OBSIDIAN_PROVIDER}" != "direct" ]]; then
    printf '%s\n' "Skipping Codex automations because Obsidian provider is ${OBSIDIAN_PROVIDER}."
    return 0
  fi

  global_agents_file="${HOME}/AGENTS.md"
  markdownlint_config="${HOME}/.markdownlint.json"

  midnight_prompt="Use the local Agent Learning System in ${SCRIPT_DIR}. Follow the consolidate-agent-learnings skill. Process new Obsidian notes from inbox and reviewed notes from needs-review. Promote only safe, grounded, reusable lessons into ${global_agents_file}, the smallest relevant reusable skill, the touched project AGENTS.md, and the existing agent-template mining workflow when useful. Move processed notes to processed/YYYY/MM or needs-review as appropriate, write a report, validate changed Markdown with ${markdownlint_config}, validate changed shell scripts with shellcheck --enable=all, and do not commit or push."
  morning_prompt="Use the local Agent Learning System in ${SCRIPT_DIR}. Check the configured Obsidian AI Agent Learnings/needs-review directory. If there are no pending or ambiguous review notes, do nothing and do not send email. If the configured recipient is empty or still uses an example.com, example.org, example.net, or example.test placeholder, do not send email; report that ./install.sh --email ADDRESS must be run first. If there are pending or ambiguous review notes and the recipient is configured, send one concise email with count, note paths, project paths, and review reasons. Prefer the Gmail connector if available. If Gmail is unavailable, use python3 scripts/agent_learning.py notify --send-msmtp as the msmtp fallback. Do not modify learning notes from this automation."
  noon_prompt="Use the local Agent Learning System in ${SCRIPT_DIR}. Follow the consolidate-agent-learnings skill. Process new inbox notes and automatically reprocess needs-review notes where exactly one Review Decision checkbox is selected. Leave unchecked or ambiguous notes pending. Do not reread processed history except for a specific duplicate or conflict. Promote only safe, grounded, reusable lessons into bounded targets, write a report, validate changed Markdown and shell files, and do not commit or push."

  write_automation \
    "agent-learning-midnight-consolidation" \
    "Agent Learning Midnight Consolidation" \
    "${midnight_prompt}" \
    "FREQ=DAILY;BYHOUR=0;BYMINUTE=0;BYSECOND=0" \
    "medium"

  write_automation \
    "agent-learning-morning-review-email" \
    "Agent Learning Morning Review Email" \
    "${morning_prompt}" \
    "FREQ=DAILY;BYHOUR=8;BYMINUTE=30;BYSECOND=0" \
    "low"

  write_automation \
    "agent-learning-noon-consolidation" \
    "Agent Learning Noon Consolidation" \
    "${noon_prompt}" \
    "FREQ=DAILY;BYHOUR=12;BYMINUTE=0;BYSECOND=0" \
    "medium"
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
