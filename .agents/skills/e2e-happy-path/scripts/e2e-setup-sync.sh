#!/usr/bin/env bash
# E2E happy-path setup + sync for skill-manager.
#
# Deterministic flow ONLY: build an isolated tree, redirect XDG, write the
# fixture declaration, and run `skill-manager sync`. NO assertions, NO pass/fail
# judgement — that is the driving agent's job.
#
# Contract with the driving agent (skill-manager issue #8):
#   - stdout: `skill-manager sync` output flows straight through, followed by
#     exactly one `E2E_MANIFEST {...}` line emitted on EXIT (success or failure).
#   - stderr: sync failure diagnostics (`Error: ...`).
#   - exit code: the process exit code (0 = setup+sync ok, non-zero = infra fail).
#
# The manifest carries the isolated paths the agent needs to assert against, plus
# `root` (the tmpdir) so a failed run can preserve the whole tree for triage.
set -euo pipefail

E2E_ROOT=""
PROJECT_DIR=""
XDG_CONFIG_HOME_VAL=""
XDG_CACHE_HOME_VAL=""
_E2E_EMITTED=0

emit_manifest() {
  if [ "$_E2E_EMITTED" -eq 1 ]; then return; fi
  _E2E_EMITTED=1
  printf 'E2E_MANIFEST {"root":"%s","project_dir":"%s","xdg_config_home":"%s","xdg_cache_home":"%s"}\n' \
    "$E2E_ROOT" "$PROJECT_DIR" "$XDG_CONFIG_HOME_VAL" "$XDG_CACHE_HOME_VAL"
}
trap emit_manifest EXIT

# 1. Build the isolated tree: <root>/{project,config,cache}.
E2E_ROOT="$(mktemp -d -t e2e-happy-path.XXXXXX)"
PROJECT_DIR="$E2E_ROOT/project"
XDG_CONFIG_HOME_VAL="$E2E_ROOT/config"
XDG_CACHE_HOME_VAL="$E2E_ROOT/cache"
mkdir -p "$PROJECT_DIR" "$XDG_CONFIG_HOME_VAL" "$XDG_CACHE_HOME_VAL"

# 2. Declare the fixture skill. Full project-config shape is required by
#    config.load_skill_declarations ({"skills":[...]}), not the bare SkillRef shown
#    in the fixture README.
cat > "$PROJECT_DIR/.skill-manager.json" <<'EOF'
{"skills":[{"name":"e2e-fixture","repo":"CNife/skill-manager-e2e-fixture","path":"."}]}
EOF

# 3. Redirect XDG so sync writes the ledger + clone cache under <root>, never
#    the real ~/.config/skill-manager or ~/.cache/skill-manager.
export XDG_CONFIG_HOME="$XDG_CONFIG_HOME_VAL"
export XDG_CACHE_HOME="$XDG_CACHE_HOME_VAL"

# 4. Run sync with cwd = project dir: sync resolves .skill-manager.json and
#    .agents/skills/ relative to cwd. stdout passes through for the agent;
#    a sync failure exits non-zero (set -e) and the trap still emits the manifest.
cd "$PROJECT_DIR"
skill-manager sync

# trap EXIT emits the manifest on the success path too.
