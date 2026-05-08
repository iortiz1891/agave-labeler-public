# AgaveInsight — Cloud-deployable labeler

This folder contains everything needed to deploy the `interactive_labeler` to
**Streamlit Community Cloud** so anyone with the invite-code can contribute
labels from anywhere in the world (no need to keep your Mac on).

## Files

| File | Purpose |
|---|---|
| `labeler_app.py` | The Streamlit app (uses Supabase for storage) |
| `requirements.txt` | Python deps for Streamlit Cloud |
| `sync_labels_from_supabase.py` | Pull labels back to your local Mac |

## Setup steps (one-time, ~30 min)

### 1. Supabase (10 min)

Already done if you completed the SQL setup. Need:

- **SUPABASE_URL**: `https://<your-project>.supabase.co`
- **SUPABASE_ANON_KEY**: the public anon JWT (for the cloud app)
- **SUPABASE_SERVICE_KEY**: the secret service-role JWT (for `sync_labels_from_supabase.py`)

Get them at: https://supabase.com/dashboard/project/<your-project>/settings/api

### 2. Create GitHub repo (5 min)

Create a **new public** repo (e.g., `agave-labeler-public`):

```bash
mkdir agave-labeler-public && cd agave-labeler-public
cp /path/to/cloud_deploy/labeler_app.py .
cp /path/to/cloud_deploy/requirements.txt .
git init
git add .
git commit -m "Initial commit"
# Create the repo on GitHub via UI, then:
git remote add origin https://github.com/<your-user>/agave-labeler-public.git
git push -u origin main
```

### 3. Deploy on Streamlit Cloud (10 min)

1. https://share.streamlit.io/ → "Sign in with GitHub"
2. Click "New app" → connect to `agave-labeler-public`
3. Main file: `labeler_app.py`
4. Click "Advanced settings" → "Secrets" → paste:

```toml
SUPABASE_URL = "https://<your-project>.supabase.co"
SUPABASE_ANON_KEY = "eyJ..."
INVITE_CODE = "agave2025"
```

5. Click "Deploy" → wait ~3 min → URL: `https://<app-name>.streamlit.app`

### 4. Sync labels back to your Mac (any time)

```bash
cd /path/to/cloud_deploy
echo 'SUPABASE_URL=https://<your-project>.supabase.co' > .env
echo 'SUPABASE_SERVICE_KEY=eyJ...' >> .env
python3 sync_labels_from_supabase.py
# → updates prps_interactive_labels.json on your Mac
```

Recommended: run this every few days OR add a cron job to sync hourly.

## How it works

```
Internet user                  Streamlit Cloud           Supabase Postgres        Your Mac
     │                                │                         │                    │
     ├── HTTPS ──────────────────────▶│                         │                    │
     │   /agave-labeler.streamlit.app │                         │                    │
     │                                │                         │                    │
     │◀─── auth + label UI ───────────┤                         │                    │
     │                                │                         │                    │
     ├── click "Save All" ───────────▶├── INSERT 10 records ───▶│                    │
     │                                │                         │                    │
     │                                │◀── ack ─────────────────┤                    │
     │◀─── confirmation ──────────────┤                         │                    │
     │                                                          │                    │
     │                                                          │◀── you sync ───────┤
     │                                                          │   sync_labels.py   │
     │                                                          ├──── records ─────▶│
                                                                                     │
                                                                  Updated local file ┘
                                                              prps_interactive_labels.json
```

## Costs

- Streamlit Community Cloud: **free** (hobbyist tier)
- Supabase: **free** (500 MB DB, 50k rows/mo)
- GitHub: **free** (public repo)

Total: $0/mo for ~50,000 labels and unlimited labellers.
