# Deploying to a single VM

Everything runs on one VM with Docker Compose:

| Service | What it does |
|---|---|
| `web` | Caddy: automatic HTTPS, serves the built React app, proxies `/api`, `/health`, `/ws` to the backend, and sends it social apps' link-preview requests and `/sitemap-data.xml` |
| `backend` | FastAPI app (`api_main.py`) |
| `neo4j` | Neo4j 5.26 Community, data in a Docker volume (not exposed publicly) |

No Railway, Vercel or Aura needed. The graph can always be rebuilt from the CSVs in the repo, so the only thing worth backing up is `deploy/.env`.

Any Ubuntu 22.04/24.04 VM with **4 GB RAM** or more works, on x86_64 or arm64. Below are steps for Azure for Students (no credit card) and Oracle Cloud.

## 1a. Create the VM on Azure (Azure for Students)

1. In the [Azure portal](https://portal.azure.com), search **Virtual machines** → **Create** → **Azure virtual machine**.
2. **Basics** tab:
   - Subscription: **Azure for Students**. Resource group: **Create new**, e.g. `fpl`.
   - Virtual machine name: e.g. `fpl-vm`.
   - Region: any nearby region. If you get a *RequestDisallowedByPolicy* error, the student subscription doesn't allow that region; pick another.
   - Availability options: **No infrastructure redundancy required**.
   - Security type: **Standard**.
   - Image: **Ubuntu Server 24.04 LTS – x64 Gen2**.
   - Size: **Standard_B2s** or **Standard_B2als_v2** (2 vCPU, 4 GiB). The size picker shows the monthly price. These run about $25–35/month, so the $100 credit lasts roughly 3 months. Don't pick a 1 GiB size; it can't run the ML models.
   - Authentication: **SSH public key**, username `azureuser`, key source **Generate new key pair** (or use your existing key).
   - Inbound port rules: **Allow selected ports** → **SSH (22), HTTP (80), HTTPS (443)**.
3. **Disks** tab: OS disk type **Standard SSD**; the default size is enough.
4. **Networking** tab: keep the defaults (a new public IP is created; Standard public IPs are static, so the address won't change).
5. **Management** tab: make sure **Auto-shutdown** is **off**, or the site goes down every night.
6. **Review + create** → **Create**. If you generated a key, **download the `.pem` file** when prompted; it can't be downloaded again.
7. Open the new VM and copy its **Public IP address**.

Optional, for a nicer address than `<ip>.sslip.io`:
- On the VM's page, click the public IP → **Configuration** → set a **DNS name label** (e.g. `fpl-assistant`), then **Save**.
- You get `fpl-assistant.<region>.cloudapp.azure.com`. Put that in `SITE_ADDRESS` in step 2.

Connect from your computer (PowerShell works):

```bash
ssh -i path/to/fpl-vm_key.pem azureuser@<public-ip>
```

If Windows complains the key's permissions are too open, run this once in PowerShell:

```powershell
icacls path\to\fpl-vm_key.pem /inheritance:r /grant:r "$($env:USERNAME):R"
```

> **Credit:** when the student credit runs out, Azure disables the resources instead of charging you. Keep an eye on the balance at **Cost Management + Billing**, and plan a move (e.g. to Oracle or your own machine) before it runs low; the setup is the same anywhere.

## 1b. Or create the VM on Oracle Cloud (needs a credit or non-PIN debit card)

1. **Compute → Instances → Create instance**
   - Image: **Canonical Ubuntu 24.04** (or 22.04), *aarch64*.
   - Shape: **Ampere → VM.Standard.A1.Flex**. 2 OCPU / 12 GB RAM is plenty; the Always Free allowance is 4 OCPU / 24 GB in total.
   - Networking: a public subnet, with **Assign a public IPv4 address** on.
   - Add your SSH public key.
   - Boot volume: 50 GB or more (Always Free includes 200 GB in total).
2. If you get **"Out of capacity"**, try another availability domain or retry later. This is common for free Ampere shapes.
3. Open the firewall at the cloud level: **Networking → Virtual cloud networks → your VCN → Security Lists → Default → Add ingress rules**
   - Source `0.0.0.0/0`, TCP, destination port `80`
   - Source `0.0.0.0/0`, TCP, destination port `443`
   - (optional) Source `0.0.0.0/0`, UDP, destination port `443` for HTTP/3

> **Idle reclamation:** Oracle may reclaim *Always Free* instances that stay mostly idle for 7 days on accounts that haven't been upgraded. Upgrading the account to **Pay As You Go** stops this and also makes Ampere capacity easier to get. Always Free resources stay free after the upgrade, but set a budget alert to be safe.

On Oracle the SSH user is `ubuntu` instead of `azureuser`, and with 12 GB+ RAM you can raise `NEO4J_HEAP=2G` and `NEO4J_PAGECACHE=1G` in `deploy/.env`.

## 2. Set up the VM

```bash
ssh azureuser@<vm-public-ip>     # ubuntu@ on Oracle
git clone https://github.com/youssef20031/ACL_M3.git
cd ACL_M3
bash deploy/setup-vm.sh
```

If the repository is private, clone it with a read-only **deploy key** (GitHub → repo Settings → Deploy keys) or a fine-grained personal access token.

`setup-vm.sh`:
- installs Docker
- adds a 4 GB swap file on small VMs
- opens ports 80/443 in the VM's own iptables (Oracle's Ubuntu images block them by default, in addition to the Security List)
- writes `deploy/.env` with a random Neo4j password and `SITE_ADDRESS=<ip-with-dashes>.sslip.io`

Log out and back in once so your user can run `docker` without `sudo`.

Then put your Groq key in `deploy/.env`:

```bash
nano deploy/.env        # set GROQ_API_KEY=...
```

## 3. Start it

```bash
bash deploy/deploy.sh --load-data   # first time: build, start, load the graph (~5-10 min on ARM)
```

Open `https://<SITE_ADDRESS>` (shown in `deploy/.env`). Caddy gets the certificate on the first request.

## 4. Turn on the daily season refresh

`pipeline/daily.py` keeps the current season up to date from the official FPL API:
- new results once FPL finalizes them (bonus confirmed)
- team news and availability (injuries, suspensions, transfers)
- bookmaker odds from football-data.co.uk (an input of the v4 points model), kept in the `live_data` volume at `odds/` (override with `ODDS_DIR`): this season's file and the upcoming `fixtures.csv` on every run, past seasons once. A failed download only leaves the odds features empty for that run.
- the graph and next-gameweek predictions (model v4), plus, before each deadline, the squad picks of the live model and of the shadow model (v3, the previous production model, no longer retrained) in `model_picks/<season>/` and `model_picks/<season>/shadow/`
- on Mondays, a retrain of the v4 points model on everything up to the weekend. The new model is kept only if it still beats the simple baselines on held-out seasons and isn't more than 10% worse than the current one; otherwise the current model stays. Each attempt is logged in the `live_data` volume at `models/retrain_history.json`, with its peak memory. To force one now: `fpl-pipeline pipeline.retrain --force`

When it finishes it tells the API to reload, so there's no restart. Install the timer once, and again whenever `deploy/install-daily-job.sh` changes (it's safe to re-run). It runs at 06:00 and 18:00 UTC, plus Fridays at 17:15 and Tuesdays at 16:00 UK time so evening deadlines get that day's team news and odds:

```bash
bash deploy/install-daily-job.sh
sudo systemctl start fpl-daily.service   # optional: run it right away
```

Every run goes through `fpl-pipeline` (installed in `/usr/local/bin` by the script), which takes one lock (`flock /run/lock/fpl-pipeline.lock`) for the whole run, so the timer's runs, `deploy.sh`'s jobs (`--run-daily`, `--load-data`, `--load-fixtures`) and manual runs never overlap: a second run waits for the first. Use it for manual runs too, e.g. `fpl-pipeline pipeline.daily` or `fpl-pipeline pipeline.retrain --force`, and run it as yourself, not with `sudo` (the lock file belongs to the timer's user).

A second timer, `fpl-deadline`, fires every hour (at :40 UTC; `FPL_DEADLINE_SCHEDULE` moves it) and runs the same job with `--before-deadline`: it goes ahead only when the next deadline is 45-105 minutes away and no run started in the 150 minutes before it, so late team news reaches the predictions, the saved pick and the ML manager's plan. Otherwise the container exits within seconds, without a healthcheck ping. It never retrains, and missed firings aren't caught up.

The odds for Friday-Monday matches are collected on Friday afternoon (by 17:00 UK time) and for Tuesday-Thursday matches on Tuesday (by 13:00). A prediction run between then and the deadline uses them; an earlier one predicts with empty odds features, and each run's summary shows the share of upcoming player-fixtures with odds (`predictions.input_coverage.odds`). The 06:00/18:00 UTC runs alone would miss Tuesday-evening deadlines, hence the extra runs; `FPL_FRIDAY_SCHEDULE` and `FPL_TUESDAY_SCHEDULE` (systemd `OnCalendar` values) move them, e.g. `FPL_TUESDAY_SCHEDULE="Tue *-*-* 15:30:00 Europe/London" bash deploy/install-daily-job.sh`.

**After updating to the v4 points model** (the deploy itself is automatic):
1. `bash deploy/install-daily-job.sh`: the extra Friday and Tuesday runs, the lock, and a 90-minute limit instead of 45 (it now includes up to 40 minutes' wait for the lock). Until then the old timer still runs the daily job, without them.
2. `fpl-pipeline pipeline.daily`: the first run downloads every odds file since 2020-21 into the `live_data` volume (`odds/`). Check `"odds"` (each file `fetched` or `fresh`) and `input_coverage` in its summary.
3. Until a bundled `ml/models/points_v4.json` is committed (the **ML train** workflow), the site serves v3 and there's no shadow pick; the first Monday retrain (or `fpl-pipeline pipeline.retrain --force`, after step 2) trains v4 on the VM and keeps it if it beats the baselines and is within 10% of v3's recorded top-20 result.

**Live vs shadow model.** The ledger compares the two models' saved picks gameweek by gameweek (points under FPL's rules, the difference, its running total and a bootstrap confidence interval). `scripts/` isn't in the backend image, so mount it into a one-off container:

```bash
docker compose -f deploy/docker-compose.yml run --rm --no-deps \
  -v "$PWD/scripts:/app/scripts:ro" backend python -m scripts.analysis.shadow_ledger
```

Optional failure alerts:
1. Create a free check on [healthchecks.io](https://healthchecks.io) with period 12 hours and grace 2 hours.
2. Put its ping URL in `deploy/.env` as `HEALTHCHECK_URL=https://hc-ping.com/...`, then run `bash deploy/deploy.sh`.

You get an email if a run fails or doesn't happen, and also when a run finishes but reports a problem (the summary's `problems`): an ML manager step that failed, a plan the rules rejected or whose solve hit its time limit, no plan for the next deadline, a captain or vice-captain who is out, an unsaved model pick, failed projections, an unreadable v4 input, or odds missing for most of the next gameweek after football-data.co.uk has collected them.

## Day-to-day

| Task | Command |
|---|---|
| Deploy the latest `main` | `bash deploy/deploy.sh` |
| Reload the graph after changing the CSVs or the loader | `bash deploy/deploy.sh --load-data` |
| Reload only promoted teams + 2026-27 fixtures (after editing the fixtures CSV) | `bash deploy/deploy.sh --load-fixtures` |
| Refresh the current season now (results, team news, predictions) | `bash deploy/deploy.sh --run-daily` |
| Daily job status / logs | `systemctl list-timers fpl-daily.timer fpl-deadline.timer`, `journalctl -u fpl-daily.service --no-pager \| tail -50` |
| Pre-deadline runs | `journalctl -u fpl-deadline.service --no-pager \| grep -i 'pre-deadline run' \| tail` |
| Run a pipeline step without overlapping the timer | `fpl-pipeline pipeline.daily`, `fpl-pipeline pipeline.retrain --force` |
| Live vs shadow model ledger | see "Live vs shadow model" above |
| Tail backend logs | `docker compose -f deploy/docker-compose.yml logs -f backend` |
| Status | `docker compose -f deploy/docker-compose.yml ps` |
| Neo4j Browser | `ssh -L 7474:localhost:7474 -L 7687:localhost:7687 azureuser@<vm-ip>` (`ubuntu@` on Oracle), then open http://localhost:7474 (user `neo4j`, password from `deploy/.env`) |

**Automatic deploys:** `.github/workflows/deploy.yml` runs `deploy.sh` over SSH on every push to `main` once you add these repository secrets:
- `VM_HOST`
- `VM_USER` (`ubuntu`)
- `VM_SSH_KEY` (the private key of a pair whose public key is in the VM's `~/.ssh/authorized_keys`)
- `VM_APP_DIR` (optional)

Until then the workflow skips itself.

**Using your own domain later:** point an `A` record at the VM's IP, set `SITE_ADDRESS=your.domain` in `deploy/.env`, and run `bash deploy/deploy.sh`.

**Link previews and the sitemap:** social apps (WhatsApp, X, Facebook, Slack, Discord, LinkedIn, Telegram, ...) don't run JavaScript, so Caddy sends their crawlers' page requests to the backend (`/api/seo/preview`), which answers that page's own title, description and image; people and Googlebot get the app as before. `/sitemap.xml` is an index of the build's `/sitemap-pages.xml` and `/sitemap-data.xml`, the current season's club and match pages from the backend. The backend takes the absolute URLs from `SITE_ADDRESS` in `deploy/.env` (`SITE_URL` overrides it). To see what a crawler gets: `curl -A "facebookexternalhit/1.1" https://<SITE_ADDRESS>/teams/arsenal`, or a platform's own preview tool (Facebook's Sharing Debugger, LinkedIn's Post Inspector); apps cache previews, so an old link can keep its old card for a while.

## Retiring the old hosting

Once the VM works, shut these down in their own dashboards:
- the Railway service (`aclm3-production`)
- the Vercel project
- optionally, the Aura instance

The repo no longer contains their configs.
