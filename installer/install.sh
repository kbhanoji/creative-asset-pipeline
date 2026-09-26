#!/usr/bin/env bash
# =============================================================================
# Creative Asset Pipeline: guided installer (Google Cloud Shell first, any Linux/macOS with gcloud)
#
#   ./installer/install.sh                 guided install (resumable; re-run to continue)
#   ./installer/install.sh --config PATH   use an existing customer.yaml
#   ./installer/install.sh --from N        re-run from phase N
#   ./installer/install.sh status          show phase status
#   ./installer/install.sh teardown        dry run, then optional delete (typed confirmation)
#
# Phases: 1 tools  2 python env  3 config  4 infrastructure  5 Creative Studio
#         6 lineage router  7 schedules  8 prompt agent  9 verify
# State:  ~/.cap-install/<customer>-<env>.done   Logs: ~/.cap-install/logs/
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(dirname "$ROOT")"                       # GCC clone goes next to this repo
STATE_HOME="${HOME}/.cap-install"
mkdir -p "$STATE_HOME/logs" "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$HOME/.local/node_modules/.bin:$PATH"
LOG="$STATE_HOME/logs/install-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

C0='\033[0m'; CB='\033[1;34m'; CG='\033[1;32m'; CY='\033[1;33m'; CR='\033[1;31m'
say()  { echo -e "${CB}➜${C0} $*"; }
ok()   { echo -e "${CG}✔${C0} $*"; }
warn() { echo -e "${CY}!${C0} $*"; }
die()  { echo -e "${CR}✖ $*${C0}"; echo "Log: $LOG"; exit 1; }
hr()   { echo -e "${CB}------------------------------------------------------------------${C0}"; }
ask()  { local __v="$1" __q="$2" __d="${3:-}" __a; read -r -p "$(echo -e "${CY}?${C0} ${__q}${__d:+ [${__d}]}: ")" __a < /dev/tty; printf -v "$__v" '%s' "${__a:-$__d}"; }
confirm_continue() { local a; read -r -p "$(echo -e "${CY}?${C0} $1 Press Enter when done (or type 'skip'): ")" a < /dev/tty; [ "$a" != "skip" ]; }

CONFIG=""; FROM=1; CMD="install"
while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    status|teardown|install) CMD="$1"; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

CAP="$ROOT/.venv/bin/cap"
yq_get() { "$ROOT/.venv/bin/python" - "$CAP_CONFIG" "$1" <<'PY'
import sys
from cap import config
c = config.load(sys.argv[1])
v = c
for part in sys.argv[2].split("."):
    v = v() if callable(v) else v
    v = getattr(v, part)
v = v() if callable(v) else v
print(v.value if hasattr(v, "value") else v)
PY
}
done_file() { echo "$STATE_HOME/$(yq_get customer.id)-$(yq_get environment).done"; }
mark_done() { echo "$1" >> "$(done_file)"; }
is_done()   { [ -f "$(done_file)" ] && grep -qx "$1" "$(done_file)"; }
api() {  # api METHOD URL [json]  -> body on stdout, HTTP code in $API_CODE
  local tok; tok=$(gcloud auth print-access-token)
  local out; out=$(curl -s -w '\n%{http_code}' -X "$1" -H "Authorization: Bearer $tok" -H "x-goog-user-project: $PROJECT" \
                 -H "Content-Type: application/json" ${3:+-d "$3"} "$2")
  API_CODE="${out##*$'\n'}"; echo "${out%$'\n'*}"
}

# ----------------------------------------------------------------------------- phases
phase_tools() {
  say "Checking tools"
  command -v gcloud >/dev/null || die "gcloud not found (use Cloud Shell or install the Google Cloud CLI)"
  command -v python3 >/dev/null || die "python3 not found"
  command -v git >/dev/null || die "git not found"
  local acct; acct=$(gcloud config get-value account 2>/dev/null)
  [ -n "$acct" ] || die "not logged in: run 'gcloud auth login'"
  ok "gcloud account: $acct"
  if ! terraform version 2>/dev/null | head -1 | grep -qE 'v1\.(1[4-9]|[2-9][0-9])'; then
    say "Installing Terraform into ~/.local/bin"
    local os arch v=1.14.1
    os=$(uname -s | tr '[:upper:]' '[:lower:]'); arch=$(uname -m); [ "$arch" = x86_64 ] && arch=amd64; [ "$arch" = aarch64 ] && arch=arm64
    curl -fsSLo /tmp/tf.zip "https://releases.hashicorp.com/terraform/${v}/terraform_${v}_${os}_${arch}.zip" || die "Terraform download failed"
    (cd /tmp && unzip -oq tf.zip terraform && mv terraform "$HOME/.local/bin/")
  fi
  ok "$(terraform version | head -1)"
  command -v jq >/dev/null || warn "jq missing (needed by Creative Studio's installer)"
  if ! command -v uv >/dev/null; then say "Installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1; fi
  command -v uv >/dev/null && ok "uv $(uv --version | awk '{print $2}')" || warn "uv not installed"
  if ! command -v firebase >/dev/null; then
    say "Installing firebase-tools into ~/.local"
    npm install --silent --prefix "$HOME/.local" firebase-tools >/dev/null 2>&1 || warn "firebase-tools install failed"
  fi
  command -v firebase >/dev/null && ok "firebase $(firebase --version)" || warn "firebase CLI not installed"
}

phase_python() {
  say "Preparing Python environment"
  [ -x "$ROOT/.venv/bin/python" ] || python3 -m venv "$ROOT/.venv" || die "python venv failed"
  "$ROOT/.venv/bin/pip" install -q --upgrade pip >/dev/null
  "$ROOT/.venv/bin/pip" install -q -e "$ROOT[gcp,agent,router]" || die "pip install failed"
  ok "cap CLI ready ($("$ROOT/.venv/bin/python" --version))"
}

phase_config() {
  if [ -z "$CONFIG" ]; then
    local found; found=$(ls "$ROOT"/config/customers/*/customer.yaml 2>/dev/null | sed "s#$ROOT/##")
    echo "Existing customer configs:"; echo "${found:-  (none)}" | sed 's/^/  /'
    ask CONFIG "Config to use (path), or 'new' to create one with the wizard" "new"
    if [ "$CONFIG" = "new" ]; then
      local cid; ask cid "Customer id (short slug, e.g. acme)"
      (cd "$ROOT" && "$CAP" init --customer "$cid") < /dev/tty || die "config wizard failed"
      CONFIG="config/customers/$cid/customer.yaml"
      warn "Edit $CONFIG (SKUs in skus.csv, brand skills, creative_studio.fork_url) before continuing if needed."
      confirm_continue "Review the config." || true
    fi
  fi
  [ -f "$CONFIG" ] || CONFIG="$ROOT/$CONFIG"
  [ -f "$CONFIG" ] || die "config not found: $CONFIG"
  export CAP_CONFIG="$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")"
  PROJECT=$(yq_get gcp.project_id); REGION=$(yq_get gcp.region)
  if [ "$(yq_get gcp.create_project)" = "True" ] && [ -z "${CAP_BILLING_ACCOUNT:-}" ]; then
    echo "Open billing accounts:"; gcloud billing accounts list --filter=open=true --format="table(name.basename(),displayName)"
    ask CAP_BILLING_ACCOUNT "Billing account ID for the new project"
    export CAP_BILLING_ACCOUNT
  fi
  (cd "$ROOT" && "$CAP" validate) || die "config validation failed"
  gcloud config set project "$PROJECT" >/dev/null 2>&1 || true
}

phase_infra() {
  say "Provisioning project and pipeline infrastructure (Terraform)"
  (cd "$ROOT" && "$CAP" infra apply --yes) || die "infrastructure apply failed"
  gcloud config set project "$PROJECT" >/dev/null
  gcloud auth application-default set-quota-project "$PROJECT" >/dev/null 2>&1 || true
  ok "Infrastructure ready in $PROJECT"
}

gcc_wait_connection() {
  local conn
  conn=$(gcloud builds connections list --region="$REGION" --project="$PROJECT" \
           --format="value(name.basename(),installationState.stage)" 2>/dev/null | awk '$2=="COMPLETE"{print $1; exit}')
  if [ -z "$conn" ]; then
    hr; say "Browser step 1/3: Cloud Build GitHub connection"
    echo "  1. Open https://console.cloud.google.com/cloud-build/connections/create?project=$PROJECT"
    echo "  2. Region: $REGION   Provider: GitHub (Cloud Build GitHub App) → CONTINUE"
    echo "  3. Name it (e.g. gh-$(yq_get customer.id)-con), authorize, and install the app for ONLY your fork:"
    echo "     $(yq_get creative_studio.fork_url)"
    while [ -z "$conn" ]; do
      confirm_continue "Create the connection." || die "GitHub connection is required"
      conn=$(gcloud builds connections list --region="$REGION" --project="$PROJECT" \
               --format="value(name.basename(),installationState.stage)" | awk '$2=="COMPLETE"{print $1; exit}')
      [ -n "$conn" ] || warn "No COMPLETE connection found in $REGION yet (check the authorization finished)."
    done
  fi
  ok "Cloud Build connection: $conn"; CS_CONNECTION_NAME="$conn"
}

gcc_wait_firebase() {
  api GET "https://firebase.googleapis.com/v1beta1/projects/$PROJECT" >/dev/null
  if [ "$API_CODE" != "200" ]; then
    hr; say "Browser step 2/3: add Firebase to the project"
    echo "  1. Open https://console.firebase.google.com/?project=$PROJECT"
    echo "  2. 'Add Firebase' to the existing Google Cloud project, accept the terms (Analytics not needed)."
    while [ "$API_CODE" != "200" ]; do
      confirm_continue "Add Firebase." || die "Firebase is required"
      api GET "https://firebase.googleapis.com/v1beta1/projects/$PROJECT" >/dev/null
      [ "$API_CODE" = "200" ] || warn "Firebase not linked yet (HTTP $API_CODE)."
    done
  fi
  ok "Firebase linked"
}

gcc_wait_oauth() {
  local url="https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT/defaultSupportedIdpConfigs/google.com" body cid=""
  body=$(api GET "$url"); cid=$(echo "$body" | jq -r 'select(.enabled==true) | .clientId // empty' 2>/dev/null)
  if [ -z "$cid" ]; then
    hr; say "Browser step 3/3: Google sign-in (Google Auth Platform + Firebase Authentication)"
    echo "  a. https://console.cloud.google.com/auth/overview?project=$PROJECT → Get started:"
    echo "     App name: '$(yq_get customer.name) Creative Studio ($(yq_get environment))'  Support/contact email: your email"
    echo "     Audience: External (Internal only if the project is in a Workspace org) → Create;"
    echo "     then Audience → Add users → everyone who will sign in (Testing mode)."
    echo "  b. https://console.firebase.google.com/project/$PROJECT/authentication → Get started →"
    echo "     Sign-in method → Google → Enable → support email → Save."
    echo "  c. https://console.cloud.google.com/auth/clients?project=$PROJECT → 'Web client (auto created by Google Service)':"
    echo "     add JavaScript origin https://$PROJECT.web.app and redirect URI https://$PROJECT.web.app/__/auth/handler → Save."
    while [ -z "$cid" ]; do
      confirm_continue "Finish a–c." || die "Google sign-in is required"
      body=$(api GET "$url"); cid=$(echo "$body" | jq -r 'select(.enabled==true) | .clientId // empty' 2>/dev/null)
      [ -n "$cid" ] || warn "Google sign-in not enabled in Firebase Authentication yet."
    done
  fi
  ok "Google sign-in enabled; OAuth client ${cid:0:24}…"; CS_OAUTH_CLIENT_ID="$cid"
}

phase_gcc() {
  local dep; dep=$(yq_get creative_studio.deployment)
  if [ "$dep" != "cloud_run" ]; then ok "Creative Studio deployment is '$dep': nothing to install here"; return; fi
  local fork ref name clone envf
  fork=$(yq_get creative_studio.fork_url); ref=$(yq_get gcc_ref); envf=$(yq_get creative_studio.gcc_env_folder)
  [ -n "$fork" ] || die "creative_studio.fork_url is empty: fork gcc-creative-studio (all branches) and set it"
  name=$(basename "${fork%.git}"); clone="$WORK/$name"
  if [ -f "$clone/infra/environments/$envf/.bootstrap_state" ] && grep -q '^LAST_COMPLETED_STEP=14' "$clone/infra/environments/$envf/.bootstrap_state"; then
    ok "Creative Studio already installed ($clone)"; return
  fi
  say "Creative Studio from $fork @ $ref"
  if [ ! -d "$clone/.git" ]; then git clone -q -b "$ref" "$fork" "$clone" || die "clone failed"; fi
  (cd "$clone" && git fetch -q origin && git checkout -q "$ref" && git pull -q --ff-only) || warn "could not update $clone"
  gcc_wait_connection; gcc_wait_firebase; gcc_wait_oauth
  say "Running Creative Studio's installer non-interactively (≈20 min: database, backend, frontend, seed data)"
  ( cd "$WORK" && \
    CS_USE_PREVIOUS_PROJECT=y CS_USE_ACTIVE_PROJECT=y CS_HAVE_PROJECT=y CS_PROJECT_ID="$PROJECT" \
    CS_REPO_URL="$fork" CS_BRANCH="$ref" CS_USE_EXISTING_DIR=y CS_ENV_NAME="$envf" \
    CS_HAVE_STATE_BUCKET=n CS_TFVAR_GITHUB_BRANCH_NAME="$ref" \
    CS_HAVE_CONNECTION=y CS_CONNECTION_NAME="$CS_CONNECTION_NAME" CS_FIREBASE_LINKED=1 \
    CS_OAUTH_CLIENT_ID="$CS_OAUTH_CLIENT_ID" CS_TERRAFORM_APPLY=y CS_TRIGGER_BUILDS=y CS_INSTALL_TOOLS=y \
    CS_NONINTERACTIVE=1 bash "$clone/bootstrap.sh" ) || die "Creative Studio installer failed (re-run this script to resume)"
  ok "Creative Studio installed: https://$PROJECT.web.app"
}

phase_router()   { (cd "$ROOT" && "$CAP" deploy router) || die "router deploy failed"; }
phase_schedule() {
  if [ "$(yq_get environment)" != "dev" ] || [ "$(yq_get creative_studio.auto_schedule.enabled)" != "True" ]; then
    ok "Auto-stop schedule not enabled for this environment"; return; fi
  (cd "$ROOT" && "$CAP" gcc schedule) || die "schedule setup failed"
}
phase_agent()    { (cd "$ROOT" && "$CAP" deploy agent) || die "agent deploy failed"; }

phase_verify() {
  say "Verifying"
  local ui agent tok
  ui=$(curl -s -o /dev/null -w '%{http_code}' "https://$PROJECT.web.app")
  [ "$ui" = "200" ] && ok "Creative Studio UI https://$PROJECT.web.app (HTTP 200)" || warn "UI returned HTTP $ui"
  agent=$(gcloud run services describe cap-prompt-agent --region="$REGION" --project="$PROJECT" --format='value(status.url)' 2>/dev/null)
  if [ -n "$agent" ]; then
    tok=$(gcloud auth print-identity-token)
    curl -s -H "Authorization: Bearer $tok" "$agent/list-apps" | grep -q prompt_enrichment \
      && ok "Prompt agent answering ($agent)" || warn "Prompt agent not answering yet"
  fi
  (cd "$ROOT" && "$CAP" gcc status) || true
  hr
  echo -e "${CG}Installation complete for $PROJECT${C0}"
  echo "  Creative Studio : https://$PROJECT.web.app"
  echo "  Prompt agent    : cd $ROOT && CAP_CONFIG=$CAP_CONFIG .venv/bin/cap agent open   (then http://localhost:8000)"
  echo "                    In Cloud Shell use Web Preview on port 8000."
  echo "  Lineage         : BigQuery dataset $(yq_get bigquery.dataset) in $PROJECT"
  echo "  Remove it all   : ./installer/install.sh teardown --config $CONFIG"
  echo "  Log             : $LOG"
}

PHASES=(tools python config infra gcc router schedule agent verify)
TITLES=("Tools" "Python environment" "Configuration" "Infrastructure" "Creative Studio" "Lineage router" "Cost-control schedules" "Prompt agent" "Verify")

run_install() {
  hr; echo -e "${CG}Creative Asset Pipeline installer${C0}   repo: $ROOT"; hr
  for i in "${!PHASES[@]}"; do
    local n=$((i + 1)) p="${PHASES[$i]}"
    [ "$n" -lt "$FROM" ] && [ "$p" != python ] && [ "$p" != config ] && continue
    if [ "$p" != tools ] && [ "$p" != python ] && [ "$p" != config ] && [ "$p" != verify ] && is_done "$p" && [ "$n" -gt "$FROM" ]; then
      ok "Phase $n ${TITLES[$i]}: done earlier"; continue
    fi
    hr; echo -e "${CB}Phase $n/9: ${TITLES[$i]}${C0}"
    "phase_$p"
    [ "$p" = python ] || [ "$p" = config ] || mark_done "$p"
  done
}

case "$CMD" in
  install) run_install ;;
  status)  phase_python >/dev/null; phase_config >/dev/null
           for i in "${!PHASES[@]}"; do p="${PHASES[$i]}"; is_done "$p" && s="done" || s="-"; printf "  %d %-24s %s\n" $((i+1)) "${TITLES[$i]}" "$s"; done ;;
  teardown)
           phase_python >/dev/null; phase_config
           (cd "$ROOT" && "$CAP" teardown) || exit 1
           ans=""; ask ans "Delete these resources now? (yes/no)" "no"
           if [ "$ans" = "yes" ]; then
             (cd "$ROOT" && "$CAP" teardown --execute) < /dev/tty && rm -f "$(done_file)"
           else echo "Nothing deleted."; fi ;;
esac
