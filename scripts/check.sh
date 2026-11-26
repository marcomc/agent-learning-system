#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
lint_config="${MARKDOWNLINT_CONFIG:-${HOME}/.markdownlint.json}"
if [[ ! -f "${lint_config}" ]]; then
  lint_config="${REPO_DIR}/.markdownlint.json"
fi
markdown_list=$(mktemp)
trap 'rm -f "${markdown_list}"' EXIT
find "${REPO_DIR}" \( -name .git -o -name .work -o -name .venv -o \
  -name .pytest_cache -o -name node_modules -o -name __pycache__ \) -prune -o \
  -type f -name '*.md' -print0 > "${markdown_list}"
markdown_files=()
while IFS= read -r -d '' file_path; do
  markdown_files+=("${file_path}")
done < "${markdown_list}"
if [[ ${#markdown_files[@]} -gt 0 ]]; then
  markdownlint --config "${lint_config}" "${markdown_files[@]}"
fi
shellcheck --enable=all "${REPO_DIR}/install.sh" "${REPO_DIR}"/scripts/*.sh
python3 -m compileall -q "${REPO_DIR}/scripts" "${REPO_DIR}/tests"
# Runtime overrides take precedence even over explicitly supplied fixture configs.
python3 -c '
import os
import sys

test_env = {
    key: value for key, value in os.environ.items()
    if not key.startswith(("AGENT_LEARNING_", "CODEX_"))
}
os.execve(sys.executable, [sys.executable, "-m", "unittest", "discover", "-s", sys.argv[1]], test_env)
' "${REPO_DIR}/tests"
exit 0
