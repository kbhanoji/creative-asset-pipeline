# Creative Studio (GCC) installation runbook

A record of installing GCC with its `bootstrap.sh` into a pipeline project. It was written during the first install (SBD dev sandbox, Sep 25, 2026) so later installs, for SBD UAT/prod or other customers, are predictable. For another customer, replace the values in the **Answer** column with that customer's config.

| Item | SBD dev value |
|---|---|
| GCP project | `sbd-cs-dev-kbhanoji` (created by `cap infra apply`) |
| Region | `us-central1` |
| GCC fork | `https://github.com/kbhanoji/gcc-creative-studio` |
| GCC branch | `developlocal` (`creative_studio.branch_by_environment.dev`) |
| GCC environment name | `development` (`creative_studio.gcc_environment`) |
| Installer run from | `code/` (so it reuses the existing clone `code/gcc-creative-studio`) |

## Before you start

1. `cap infra apply` has run, so the project exists with billing.
2. Tools installed: `git`, `jq`, `terraform` (≥ 1.14.1; `brew install hashicorp/tap/terraform`), `gcloud`, `firebase` (`npm install -g firebase-tools`), `uv` (`brew install uv`).
3. `gcloud config set project <project>`, so the installer detects the project.
4. `gcloud auth application-default login` done, with quota project set to the project.
5. **GitHub fork created with all branches** ("Copy the main branch only" unticked). The branch to deploy exists in the fork.
6. Run the installer in a **real terminal** (macOS Terminal / iTerm), not through Claude Code's `!`. It reads answers from `/dev/tty`.
   ```bash
   cd code/creative-asset-pipeline
   CAP_CONFIG=config/customers/<customer>/customer.yaml .venv/bin/cap gcc install
   ```
   `cap gcc install --dry-run` prints the answers without running anything.

## Questions and answers

Status: ✅ answered during the SBD dev install · ⏳ still ahead (expected from reading `bootstrap.sh`; update when reached).

| # | Step | Installer asks | Answer | Why | Status |
|---|---|---|---|---|---|
| 1 | 1 Prerequisites | "Would you like to try and install it now?" (only if a tool is missing) | Not asked if the tools above are installed | Install the tools beforehand so versions are known | ✅ not asked |
| 2 | 3 Project | "Detected active gcloud project '…'. Use this project?" | **y** | The project was created by `cap infra apply`; GCC goes into the same project | ✅ |
| 3 | 4 Git | "What is the git URL of YOUR forked repository?" | `https://github.com/kbhanoji/gcc-creative-studio` | Cloud Build deploys from the fork | ✅ |
| 4 | 4 Git | "Which git branch would you like to use?" | `developlocal` | Low-cost dev branch (scale-to-zero backend, DB autostart, small DB) | ✅ |
| 5 | 4 Git | "Directory 'gcc-creative-studio' already exists. Use this existing directory?" | **y** | Reuse `code/gcc-creative-studio` (already on `developlocal`) instead of a second clone | ✅ |
| 6 | 5 Terraform env | "What would you like to call this deployment environment? [default value: dev-infra]" | SBD dev: `dev-infra` (the default was kept) | This names only the **folder** `infra/environments/<name>` and the state bucket. The `environment` value inside the tfvars stays `development` (from the template), which names the media bucket `<project>-cs-development-bucket` and must match `creative_studio.gcc_environment` | ✅ |
| 7 | 5 Terraform env | "Do you have an existing GCS bucket for Terraform state?" | **n** | The installer creates `gs://<project>-cstudio-<name>-tfstate` (SBD dev: `sbd-cs-dev-kbhanoji-cstudio-dev-infra-tfstate`, prefix `infra/dev-infra/state`). Keep GCC's state separate from the pipeline's (the pipeline uses local `.state/`) | ✅ |
| 8 | 5 Terraform env | "GitHub Branch to deploy from [default value: …]" | `developlocal` | Written to `github_branch_name` in the tfvars. The Cloud Build trigger deploys pushes to this branch. The installer also sets the service names to `cstudio-be` / `cstudio-fe`: copy them to `creative_studio.backend_service` / `frontend_service` | ✅ |
| 9 | 6 Manual steps | "Do you already have a Cloud Build Host Connection for GitHub in this project?" | **n** (on a new project) | `gcloud builds connections list --region=us-central1` showed 0 connections. Answer **y** and give its name only if one exists | ✅ |
| 10 | 6 Manual steps | "Paste the new Cloud Build Connection Name here" | The name you gave the connection. SBD dev: `gh-kbhanoji-con` | See **Console step A** below | ✅ |
| 11 | 6 Manual steps | "Press [Enter] to continue after you have linked the project." (Firebase) | Press Enter after **Console step B** | Terraform can't accept Firebase terms | ✅ |
| 12 | 6 Manual steps | "Paste the OAuth Client ID here" | Client ID of **"Web client (auto created by Google Service)"** | On a new project this client **doesn't exist yet**, and the Credentials page says "Google Auth Platform not configured yet". Do **Console step C** (C1–C4) first, then paste | ✅ |
| 13 | 8 OAuth secrets | "Paste the OAuth Client ID here" (fallback, only if automatic lookup fails) | Same client ID as #12 | | ⏳ |
| 14 | 10 Terraform | "Do you want to proceed with 'terraform apply'?" | **y** | Creates Cloud SQL (`db-custom-1-3840`, ENTERPRISE), the Cloud Run backend (min instances 0), Firebase Hosting, secrets and the Cloud Build triggers. The database takes ~10 min. SBD dev result: `creative-studio-db-0c8b3b63`, POSTGRES_18, db-custom-1-3840, ENTERPRISE ✔ | ✅ |
| — | 13 Seed data | (no question) **Failed on the SBD dev laptop.** See "Issues seen and fixes" #4 and #5 | Run `scripts/seed_data.sh` from **Cloud Shell** | | ✅ workaround |
| 15 | 14 Builds | "Would you like to trigger the initial builds for the frontend and backend now?" | **y** | First deploy of the backend and frontend from `developlocal`. SBD dev: the installer stopped at step 13, so the builds were triggered by hand: `gcloud builds triggers run cstudio-be-trigger --region=us-central1 --branch=developlocal` and the same for `<project>-trigger` (frontend). Both SUCCESS (~6 min) | ✅ manual |

Automatic steps with no questions: 7 (Firebase web app), 9 (database password secret), 11 (OAuth redirect URIs), 12 (remaining secrets), 13 (seed data, owned by the gcloud user running the script).

## Console steps

**A. Cloud Build GitHub connection (question 10)**
1. Open `https://console.cloud.google.com/cloud-build/connections/create?project=<project>`.
2. Region **us-central1** (it must equal the GCC region; Terraform looks the connection up there).
3. Provider **GitHub (Cloud Build GitHub App)** → CONTINUE.
4. Give the connection a name (e.g. `gh-kbhanoji-con`) and authorize the Google Cloud Build app on GitHub (account `kbhanoji`).
5. Install the app for **only** the repository `kbhanoji/gcc-creative-studio` (least access).
6. Paste the connection name back into the installer.

**B. Firebase terms (question 11)**
1. Open `https://console.firebase.google.com/?project=<project>`.
2. "Add Firebase" to the existing Google Cloud project and accept the terms. Google Analytics isn't needed (you can turn it off).
3. Return to the terminal and press Enter.

**C. OAuth client ID (question 12).** GCC signs users in with Firebase Google sign-in (`signInWithPopup`) and Google Identity Services, so the project needs a configured Google Auth Platform and the Google provider enabled in Firebase.

*C1. Google Auth Platform (branding / consent)*: `https://console.cloud.google.com/auth/overview?project=<project>` → **Get started**:

| Field | Answer (SBD dev) | Notes |
|---|---|---|
| App name | `SBD Creative Studio (Dev)` | Shown on the Google sign-in screen. Don't use the word "Google" in the name (not allowed) |
| User support email | `kbhanoji@gmail.com` | The dropdown only offers your own address or groups you manage |
| Audience | **External** | **Internal** needs a Google Workspace organization, which a personal account doesn't have. For a customer tenant with Workspace/Cloud Identity, choose Internal |
| Contact information | `kbhanoji@gmail.com` | Google's notifications about the app |
| Policy | Agree, then **Create** | |

Then **Audience** → keep publishing status **Testing** → **Add users**: `kbhanoji@gmail.com` and anyone else who will sign in (up to 100 test users). There's no Google verification needed while in Testing. Testing-mode sign-ins expire after 7 days, and you just sign in again.

*C2. Enable Google sign-in in Firebase*: `https://console.firebase.google.com/project/<project>/authentication` → **Get started** → **Sign-in method** → **Google** → Enable → project support email `kbhanoji@gmail.com` → **Save**. This creates the OAuth client **"Web client (auto created by Google Service)"** with the `firebaseapp.com` origin and redirect.

*C3. Add the web.app URLs to that client*: `https://console.cloud.google.com/auth/clients?project=<project>` → open **Web client (auto created by Google Service)**:
- Authorized JavaScript origins: add `https://<project>.web.app` (keep `https://<project>.firebaseapp.com`)
- Authorized redirect URIs: add `https://<project>.web.app/__/auth/handler` (keep the `firebaseapp.com` one)
- **Save**. The installer's step 11 tries to add these through `gcloud iap oauth-clients`, which doesn't always resolve a Firebase-created client, so add them by hand to be sure.

*C4.* Copy the **Client ID** (`…apps.googleusercontent.com`; the secret isn't needed) and paste it into the installer.

## After the installer finishes

```bash
cap gcc status          # backend min instances 0, DB tier db-custom-1-3840, UI https://<project>.web.app
cap deploy router       # lineage router + Eventarc (incl. GCC media bucket)
cap gcc schedule        # 17:00 daily stop + 30-min idle stop (needs the router)
cap deploy agent        # prompt agent on Cloud Run
```

### SBD dev result (Sep 25, 2026)

After `scripts/seed_data.sh` succeeded (in Cloud Shell), the installer was marked finished locally (`LAST_COMPLETED_STEP=14` in `infra/environments/dev-infra/.bootstrap_state`, so a re-run skips everything). Checks:

| Check | Result |
|---|---|
| Backend `cstudio-be` min instances | `minScale: 0` ✔ (scales to zero) |
| Backend env `DB_AUTOSTART` | `true` ✔; backend SA `cs-be-development-run@…` has `roles/cloudsql.editor` ✔ |
| Cloud SQL | `creative-studio-db-0c8b3b63`, `db-custom-1-3840`, ENTERPRISE, RUNNABLE ✔ |
| Artifact Registry `cs-be-development-repo` | cleanup policies delete-older-than-1-day + keep-most-recent ✔ |
| UI `https://<project>.web.app` | HTTP 200 ✔. `/api/version` returns 403 without a signed-in user (expected: the backend requires authentication) |

### Pipeline deployment issues (after GCC)

| When | Issue | Fix |
|---|---|---|
| `cap deploy router` | `eventarc triggers create`: `PERMISSION_DENIED: Permission "storage.buckets.get" denied ... Eventarc service account` | First Eventarc use in a new project: its service agent isn't provisioned/granted yet. `cap deploy router` now creates it (Service Usage API `generateServiceIdentity`), grants `roles/eventarc.serviceAgent`, and retries trigger creation while IAM propagates |
| `cap deploy router` | Hung at `Do you want to continue (Y/n)?` | `gcloud beta services identity create` prompts to install the gcloud beta component. Replaced with a direct Service Usage API call (also for Gemini Enterprise registration in `cap deploy agent`) |

| `cap gcc schedule` | `argument --http-method: Invalid choice: 'patch'` | Cloud Scheduler HTTP jobs support only GET/POST/PUT/DELETE/HEAD, but Cloud SQL `instances.patch` needs PATCH. The jobs now send **POST** with header `X-HTTP-Method-Override: PATCH` (verified against the instance with a no-op patch → UPDATE operation) |

| `cap agent open` → http://localhost:8000 | Blank page; console: `Failed to load resource: 403` for every `chunk-*.js` / `main-*.js` (response body `Forbidden: origin not allowed`) | **ADK 2.x DNS-rebinding/CSRF guard:** browser module requests carry `Origin: http://localhost:8000`, while the service sees its `run.app` Host, so ADK rejects them (`curl` without Origin got 200, which hid it). **Fix:** `services/prompt_agent/main.py` passes `allow_origins` from `CAP_ALLOW_ORIGINS`; `cap deploy agent` sets localhost/127.0.0.1 on ports 8000/8080/8001. Safe because the service only accepts authenticated callers. Verified locally: proxy origins → 200, a foreign origin → 403. After redeploy (revision `cap-prompt-agent-00002`), headless Chrome renders the UI and a chat message sent through the UI is answered (`list_skus` → DCD800B). The only console error left is `404 prism-dark.css`, a code-highlighting theme missing from the ADK package itself (cosmetic) |
| `cap deploy agent` | (caught before deploying) env values containing commas or `@` | Env vars are passed with gcloud's custom delimiter `^\|^` so the comma-separated origins and the email in `CAP_USER` stay intact |

### Pipeline deployment result (SBD dev)

| Component | Result |
|---|---|
| Lineage router `cap-lineage-router` | Deployed; Eventarc triggers `cap-creative-studio-finalized` (GCC media bucket) and `cap-intermediate-finalized` |
| Schedules | `cap-gcc-sql-stop` 17:00 America/Chicago daily; `cap-gcc-sql-idle-check` every 15 min (stop after 30 idle min). Verified: the 22:45 UTC run → router `200`, result `{'action': 'none', 'reason': '6 backend request(s) in the last 30 min'}`. A forced `gcloud scheduler jobs run` can take a few minutes to execute |
| Prompt agent `cap-prompt-agent` | `cap agent open` → `gcloud run services proxy` on http://localhost:8000. The first run auto-installs a gcloud component (~1 min). Deployed (min instances 0). Authenticated `/list-apps` → `["prompt_enrichment"]`, anonymous → 403. A test chat turn called `list_skus` and answered with DCD800B |

## Issues seen and fixes

| When | Issue | Fix |
|---|---|---|
| Before install | `cap gcc install` from Claude Code hangs | The installer needs a TTY: run it in Terminal |
| Before install | The installer clones into `./gcc-creative-studio` of the current folder | `cap gcc install` now runs from `code/`, and you answer **y** to reuse the existing clone |
| Before install | `firebase` and `uv` missing | `npm install -g firebase-tools`, `brew install uv` |
| Possible (step 7) | `firebase apps:list` fails with an auth error | Run `firebase login` (same Google account) in another terminal, then run `cap gcc install` again. The installer saves `LAST_COMPLETED_STEP` and resumes |
| 4. Step 13 (seed) | `Connect call failed ('127.0.0.1', 5432)` | **Upstream bug:** the installer always downloaded `cloud-sql-proxy.linux.amd64`, which can't run on macOS/Apple Silicon, and hid its output. **Fixed in `developlocal` (`2de201e`)**: it downloads the proxy for the local OS/CPU, logs to `cloud-sql-proxy.log`, and fails with the log if the proxy doesn't start |
| 5. Step 13 (seed) | Next error: `ConnectionResetError`; `cloud-sql-proxy.log`: `x509: certificate signed by unknown authority` | **Corporate network TLS interception.** The certificate presented for the instance on port 3307 was issued by *Cato Networks Root CA* (HCL network). Cloud SQL's proxy correctly refuses it (the latest proxy v2.25.4 too). It affects only laptop → Cloud SQL connections, not the deployed app. **Workaround:** run the seed step from Google Cloud Shell: `git clone -b developlocal https://github.com/kbhanoji/gcc-creative-studio.git && cd gcc-creative-studio && bash scripts/seed_data.sh <project> dev-infra` (script added in `5592764`). Alternatives: a network without TLS inspection, or ask IT to exempt Cloud SQL (port 3307) from inspection |
| 7. `scripts/seed_data.sh` (first version) | `pushd: /infra/environments/dev-infra: No such file` then `Python project file not found at '/backend/pyproject.toml'` | Sourcing `bootstrap.sh` resets its globals (`REPO_ROOT=""`). **Fixed in `dae4ac4`**: variables are set after sourcing, and the script refuses to run outside a GCC checkout. In Cloud Shell: `cd ~/gcc-creative-studio && git pull` and run again. (The proxy itself connected fine from Cloud Shell, which confirms #5 is the laptop network) |
| 8. Step 13 in Cloud Shell | `ConnectionResetError: [Errno 104] Connection reset by peer` (the proxy was "Connected!") | The server logs showed no connection attempt. My first guess (old proxy v2.8.0) was **wrong**; upgrading to v2.25.4 in `dfe2169` is kept as a general improvement. The proxy log (now printed by `seed_data.sh`) showed the real cause: `failed to get instance metadata ... Error 400: Invalid request: instance name (  Follow the instructions at https://developer.hashicorp.com/terraform/install ...)`. See #9 |
| 9. Step 13 in Cloud Shell | Proxy used Terraform install instructions as the instance name | **Upstream bug:** the installer reads the connection name from `terraform output`. In Cloud Shell `terraform` isn't installed, and its "command not found" helper prints help text to **stdout**, which was taken as the name, so the gcloud fallback never ran. **Fixed in `db9335f`**: use `$INSTANCE_CONNECTION_NAME` if set, run Terraform only when installed and the env folder exists, accept only `project:region:instance`, otherwise use `gcloud sql instances list`. Tested with a fake `terraform` that prints junk |
| 10. Step 13 seeding, VTO assets | `sqlalchemy.exc.NoReferencedTableError: Foreign key associated with column 'source_assets.folder_id' could not find table 'folders'` (the database connection now works; workspaces, templates and some assets were already seeded) | **Upstream bug on `develop`:** the new folders feature added `folder_id` foreign keys on `source_assets` and `media_items`, but `backend/bootstrap/bootstrap.py` doesn't import the `Folder` model (the app imports it through its routers). **Fixed in `add0a4a`**: import it. Verified that all foreign keys resolve and the table sort that crashed succeeds. Seeding is idempotent (existing users/workspaces/templates/assets are skipped), so just re-run `scripts/seed_data.sh` |
| 6. Resuming | Re-running `cap gcc install` repeats the early questions | The installer finds its `.bootstrap_state` only after step 4, so it asks steps 1–4 again. For a single failed step, run that step's function instead (the seed step: `scripts/seed_data.sh`) |
