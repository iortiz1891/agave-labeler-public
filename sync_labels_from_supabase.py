"""Pull labels from Supabase to local prps_interactive_labels.json.

Run periodically on your Mac to keep local file in sync with what
remote labellers have submitted via the cloud-deployed app.

Usage:
  export SUPABASE_URL="https://<project>.supabase.co"
  export SUPABASE_SERVICE_KEY="eyJ..."   # (or anon if RLS allows SELECT)
  python3 cloud_deploy/sync_labels_from_supabase.py
"""
import os, json, sys
from datetime import datetime
from pathlib import Path

# Optional: load .env in same folder
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from supabase import create_client


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = (os.environ.get("SUPABASE_SERVICE_KEY")
                or os.environ.get("SUPABASE_ANON_KEY", ""))

LOCAL_PATH = Path(
    "/Volumes/Ivan's Disk/repo_agave_titan_v12_4mar/data/processed/"
    "prps_interactive_labels.json")


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[!] Set SUPABASE_URL and SUPABASE_SERVICE_KEY (or _ANON_KEY) "
              "env vars (or in .env file).")
        sys.exit(1)

    supa = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"Pulling labels from {SUPABASE_URL}…")
    # Pagination: Supabase default page = 1000 rows
    all_records = []
    offset = 0; page_size = 1000
    while True:
        res = (supa.table("prps_interactive_labels")
                  .select("id,lat,lon,polygon,year,label,labeller,datetime,base_id")
                  .order("datetime")
                  .range(offset, offset + page_size - 1)
                  .execute())
        rows = res.data or []
        if not rows: break
        all_records.extend(rows)
        print(f"  ↓ fetched {len(all_records)}")
        if len(rows) < page_size: break
        offset += page_size

    print(f"\nFetched {len(all_records)} cloud records total")

    # Convert to local JSON format (the existing labeler reads this format)
    samples = []
    for r in all_records:
        samples.append({
            "id":       r["id"],
            "lat":      r["lat"],
            "lon":      r["lon"],
            "polygon":  r["polygon"],
            "year":     r["year"],
            "label":    r["label"],
            "labeller": r.get("labeller", "anonymous"),
            "datetime": r.get("datetime"),
        })

    # Read existing local file (preserve any local-only records)
    if LOCAL_PATH.exists():
        with open(LOCAL_PATH) as f:
            local = json.load(f)
        local_ids = {s["id"] for s in local.get("samples", [])}
        cloud_ids = {s["id"] for s in samples}
        only_local = [s for s in local["samples"] if s["id"] not in cloud_ids]
        only_cloud = [s for s in samples if s["id"] not in local_ids]
        print(f"  local-only: {len(only_local)}  cloud-only: {len(only_cloud)}")
        # Merge: cloud records win (newer schema), preserve local-only
        merged = samples + only_local
    else:
        merged = samples
        print(f"  local file did not exist — creating fresh")

    out = {"samples": merged, "synced_at": datetime.utcnow().isoformat()}
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_PATH, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n[OK] {LOCAL_PATH}  ({LOCAL_PATH.stat().st_size//1024} KB, "
          f"{len(merged)} total records)")


if __name__ == "__main__":
    main()
