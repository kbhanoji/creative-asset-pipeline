# Creative Asset Pipeline: guided installation

## Welcome

This walkthrough installs the Creative Asset Pipeline into a Google Cloud project:
- the prompt agent
- BigQuery lineage
- the scoring router
- Creative Studio (GCC)
- the development cost controls

Everything is driven by one customer configuration file.

**Time:** about 40 minutes, most of it waiting for Creative Studio to build.

**You need:**
- A Google account that can create projects (or owner access to an existing project)
- An open billing account
- A GitHub fork of `gcc-creative-studio` with **all branches** (untick "Copy the main branch only")

Click **Start** to begin.

## Get the installer

If you opened this from the repository, the files are already here. Otherwise use one of these two options:

**Private GitHub repository**
```sh
gh auth login
gh repo clone kbhanoji/creative-asset-pipeline && cd creative-asset-pipeline
```

**Zip file** (upload it with the ⋮ menu → **Upload** at the top of this terminal)
```sh
sha256sum -c creative-asset-pipeline-*.zip.sha256
unzip creative-asset-pipeline-*.zip && cd creative-asset-pipeline
```

## Choose or create the customer configuration

Existing configurations live in `config/customers/<customer>/customer.yaml`. For a new customer, the installer runs the configuration wizard. It asks for:
- the Google account and project ID, and whether to create the project
- the billing account, region and users
- the generation mode and score thresholds
- the Creative Studio fork and the agent runtime

After the wizard, check these files for the new customer:
- `skus.csv`: the products
- `skills/*.md`: brand, safety and prompt rules
- `creative_studio.fork_url` in `customer.yaml`

## Run the installer

```sh
./installer/install.sh
```

It runs 9 phases and remembers what's finished. If the session ends, run the same command again to continue.

1. Tools (Terraform, uv, firebase-tools are installed in your home directory)
2. Python environment
3. Configuration and validation
4. Project, billing, Terraform state bucket and pipeline infrastructure
5. Creative Studio
6. Lineage router
7. Cost-control schedules (development only)
8. Prompt agent
9. Verification and summary

## The three browser steps in phase 5

Google requires a person for these. The installer prints each link, waits, and **checks through Google's APIs** that each step is complete before moving on.

1. **Cloud Build GitHub connection.** Create it in the project's region and give the GitHub app access to only your fork.
2. **Add Firebase** to the project and accept the terms.
3. **Google sign-in:**
   - Configure Google Auth Platform (External, Testing, add test users).
   - Enable Google sign-in in Firebase Authentication.
   - Add the `web.app` URLs to the auto-created web client.

After that, Creative Studio's own installer runs without further questions. It builds the database, backend and frontend, and loads the seed data.

## Try it

- **Creative Studio:** open `https://<project-id>.web.app` and sign in.
- **Prompt agent:**
  ```sh
  .venv/bin/cap agent open --port 8080
  ```
  Then use <walkthrough-web-preview-icon></walkthrough-web-preview-icon> **Web Preview → Preview on port 8080**.
- **Lineage:**
  ```sh
  .venv/bin/cap status --sku <SKU>
  ```

## Remove everything (optional)

```sh
./installer/install.sh teardown
```

It shows exactly what will be deleted. To go ahead, you type `delete <project-id>`.
- **Project the installer created:** the whole project is deleted. It can be restored for 30 days with `gcloud projects undelete`.
- **Existing project:** only this pipeline's and Creative Studio's resources are removed.

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy> Done.
