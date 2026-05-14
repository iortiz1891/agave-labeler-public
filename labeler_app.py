"""Cloud-deployable AgaveInsight labeler — uses Supabase for label storage.

Designed for Streamlit Community Cloud deployment.

Required Streamlit secrets (set in https://share.streamlit.io/ app settings):
    SUPABASE_URL = "https://<project>.supabase.co"
    SUPABASE_ANON_KEY = "eyJ..."
    INVITE_CODE = "agave2025"  (or your custom code)

Local testing: set them as env vars OR put them in `.streamlit/secrets.toml`.
"""
import json, os, io, base64, uuid
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as st_components
import folium
from streamlit_folium import st_folium
from pyproj import Transformer
from supabase import create_client, Client


# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────
def _get_secret(key, default=None):
    """Read from st.secrets, fall back to env var, then default."""
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


SUPABASE_URL  = _get_secret("SUPABASE_URL", "")
SUPABASE_ANON = _get_secret("SUPABASE_ANON_KEY", "")
INVITE_CODE   = _get_secret("INVITE_CODE", "agave2025")

ALL_YEARS = [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
LABEL_OPTS = ["yes", "no", "unsure", "not_visible"]
LABEL_LBLS = {"yes": "✅", "no": "❌", "unsure": "❓", "not_visible": "👁"}
DEFAULT_LABEL = "no"
COLOR_MAP = {"yes": "#00C853", "no": "#E53935",
              "unsure": "#FF9800", "not_visible": "#9E9E9E"}
HALF_M = 45.0
NUDGE_M = 5.0

# AOI bounds (central Oaxaca) — hardcoded since no local rasters
AOI_BOUNDS = (15.93, -96.87, 17.18, -95.55)   # (s, w, n, e)

# Wayback releases — bundled in deployment
WAYBACK_RELEASES_URL = (
    "https://raw.githubusercontent.com/iortiz1891/agave-labeler-public/"
    "main/esri_wayback_releases.json"
)


# ─────────────────────────────────────────────────────────────────────
# Streamlit page
# ─────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Agave Interactive Labeler",
    page_icon="🎯",
    layout="wide",
)

# ─────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────
if "auth_ok" not in st.session_state:
    st.session_state["auth_ok"] = False

if not st.session_state["auth_ok"]:
    st.title("🎯 Agave Labeler — Invite-only access")
    st.caption(
        "App de etiquetado de parcelas de Agave en Oaxaca, México "
        "(proyecto AgaveInsight, Iván Ortiz). Para acceder ingresa el código "
        "de invitación que recibiste."
    )
    code = st.text_input("Código de invitación:", type="password",
                          key="auth_input")
    if st.button("Entrar", key="auth_btn"):
        if code == INVITE_CODE:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Código incorrecto.")
    st.markdown("---")
    st.caption("¿Necesitas acceso? Contacta: iortiz1891@gmail.com")
    st.stop()

if "labeller_name" not in st.session_state:
    st.session_state["labeller_name"] = ""
if not st.session_state["labeller_name"]:
    st.title("🎯 Agave Labeler — bienvenido")
    st.markdown("**Por favor, ingresa tu nombre o iniciales** "
                "(quedará registrado con cada etiqueta):")
    name = st.text_input("Nombre / iniciales:", key="name_input")
    if st.button("Comenzar a etiquetar", key="name_btn"):
        if name and len(name.strip()) >= 2:
            st.session_state["labeller_name"] = name.strip()
            st.rerun()
        else:
            st.error("Mínimo 2 caracteres.")
    st.stop()

# ─────────────────────────────────────────────────────────────────────
# Supabase client
# ─────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_ANON:
        st.error("Supabase credentials missing. Check secrets configuration.")
        st.stop()
    return create_client(SUPABASE_URL, SUPABASE_ANON)


supa = get_supabase()


@st.cache_data(ttl=20)   # 20s cache so multiple labellers see updates
def fetch_recent_samples(limit=2000):
    """Get most recent label-records (for showing previously-labelled
    polygons on map)."""
    try:
        res = (supa.table("prps_interactive_labels")
                  .select("id,lat,lon,polygon,year,label,labeller,datetime")
                  .order("datetime", desc=True)
                  .limit(limit)
                  .execute())
        return res.data or []
    except Exception as e:
        st.warning(f"Supabase fetch error: {e}")
        return []


def insert_labels(records):
    """Insert a batch of labels into Supabase."""
    try:
        res = supa.table("prps_interactive_labels").insert(records).execute()
        return True
    except Exception as e:
        st.error(f"Supabase insert error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────
# Wayback releases — load from bundled local file first, then GitHub, then fallback
# ─────────────────────────────────────────────────────────────────────
@st.cache_data
def load_wayback():
    # 1. Bundled in repo (most reliable — these IDs are validated)
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(here, "esri_wayback_releases.json")
    if os.path.exists(local):
        try:
            with open(local) as f:
                return json.load(f)
        except Exception:
            pass
    # 2. Remote (in case the bundled file isn't present)
    try:
        import urllib.request
        with urllib.request.urlopen(WAYBACK_RELEASES_URL, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        pass
    # 3. Last-resort: validated IDs (verified 2026-05-08 by HTTP 200)
    return {
        "2014": {"release_id": "4230",  "date": "2014-03-26"},
        "2015": {"release_id": "15084", "date": "2015-03-18"},
        "2016": {"release_id": "19085", "date": "2016-03-16"},
        "2017": {"release_id": "29387", "date": "2017-03-15"},
        "2018": {"release_id": "8255",  "date": "2018-03-14"},
        "2019": {"release_id": "4383",  "date": "2019-03-13"},
        "2020": {"release_id": "16062", "date": "2020-03-23"},
        "2021": {"release_id": "5359",  "date": "2021-03-17"},
        "2022": {"release_id": "10321", "date": "2022-03-16"},
        "2023": {"release_id": "44873", "date": "2023-03-15"},
        "2024": {"release_id": "60013", "date": "2024-03-07"},
        "2025": {"release_id": "6543",  "date": "2025-03-27"},
    }


wayback_rel = load_wayback()


# ─────────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────────
st.title(f"🎯 Agave Interactive Labeler  ·  Labeller: **{st.session_state['labeller_name']}**")
st.caption(
    "Click en el mapa grande para colocar el cuadrado 90×90 m (aparece en "
    "TODOS los 10 años). Etiqueta yes/no/unsure por año. Save guarda 10 "
    "records (uno por año) y limpia el cuadrado para el siguiente."
)

# Live counters
samples_recent = fetch_recent_samples(limit=5000)
n_recent_records = len(samples_recent)
n_recent_locs = len(set((round(s["lat"], 5), round(s["lon"], 5))
                         for s in samples_recent))
n_recent_yes = sum(1 for s in samples_recent if s.get("label") == "yes")

cstat1, cstat2, cstat3 = st.columns(3)
with cstat1: st.metric("Total records (proyecto)", n_recent_records)
with cstat2: st.metric("Ubicaciones únicas",      n_recent_locs)
with cstat3: st.metric("Yes-labels",              n_recent_yes)

# ─────────────────────────────────────────────────────────────────────
# Map state
# ─────────────────────────────────────────────────────────────────────
if "int_lat" not in st.session_state:
    st.session_state["int_lat"] = 16.8625
    st.session_state["int_lon"] = -96.4083
if "int_marker_lat" not in st.session_state:
    st.session_state["int_marker_lat"] = None
    st.session_state["int_marker_lon"] = None
if "int_round" not in st.session_state:
    st.session_state["int_round"] = 0
ROUND = st.session_state["int_round"]

cur_lat = st.session_state["int_lat"]
cur_lon = st.session_state["int_lon"]
marker_lat = st.session_state["int_marker_lat"]
marker_lon = st.session_state["int_marker_lon"]

# Build sample square at marker
sample_polygon_geojson = None
sample_polygon = None
if marker_lat is not None and marker_lon is not None:
    xf = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    xi = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    cx, cy = xf.transform(marker_lon, marker_lat)
    corners = [(cx-HALF_M,cy-HALF_M),(cx+HALF_M,cy-HALF_M),
                (cx+HALF_M,cy+HALF_M),(cx-HALF_M,cy+HALF_M),(cx-HALF_M,cy-HALF_M)]
    ring = [list(xi.transform(x, y)) for x, y in corners]
    sample_polygon_geojson = {"type": "Polygon", "coordinates": [ring]}
    sample_polygon = {"type": "Feature",
                        "geometry": sample_polygon_geojson, "properties": {}}


# ─────────────────────────────────────────────────────────────────────
# Controls
# ─────────────────────────────────────────────────────────────────────
ctrl1, ctrl2, ctrl3 = st.columns([1, 1, 4])
with ctrl1:
    if st.button("🌍 Fit AOI", width="stretch"):
        lat_s, lon_w, lat_n, lon_e = AOI_BOUNDS
        st.session_state["int_lat"] = (lat_s + lat_n) / 2
        st.session_state["int_lon"] = (lon_w + lon_e) / 2
        st.session_state["int_marker_lat"] = None
        st.session_state["int_marker_lon"] = None
        st.session_state["int_round"] += 1
        st.rerun()
with ctrl2:
    BASEMAP_KEYS = ["Esri Wayback 2025", "Esri current",
                     "Google Satellite", "Google Hybrid",
                     "Planet 2025-05", "Planet 2024-05",
                     "Planet 2023-05", "Planet 2022-05",
                     "Planet 2021-05", "Planet 2020-05",
                     "Planet 2019-05", "Planet 2018-05",
                     "Planet 2017-05",
                     "CartoDB Dark", "OpenStreetMap"]
    selected_basemap = st.selectbox("Base layer:", options=BASEMAP_KEYS,
                                       index=0)
with ctrl3:
    big_zoom = st.select_slider(
        "Zoom (10 = AOI overview, 17-19 = parcel detail)",
        options=[8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
        value=14)


# Nudge controls
def nudge(d_lat_m=0.0, d_lon_m=0.0):
    if marker_lat is None: return
    xf = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    xi = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    cx, cy = xf.transform(marker_lon, marker_lat)
    cx += d_lon_m; cy += d_lat_m
    new_lon, new_lat = xi.transform(cx, cy)
    st.session_state["int_marker_lon"] = float(new_lon)
    st.session_state["int_marker_lat"] = float(new_lat)
    st.rerun()


st.markdown("**Nudge** (mueve el cuadrado con precisión):")
nudge_cols = st.columns(8)
for i, (lbl, kw, key) in enumerate([
    ("⬅️ −5m",  {"d_lon_m": -NUDGE_M}, "w"),
    ("➡️ +5m",  {"d_lon_m": +NUDGE_M}, "e"),
    ("⬆️ +5m",  {"d_lat_m": +NUDGE_M}, "n"),
    ("⬇️ −5m",  {"d_lat_m": -NUDGE_M}, "s"),
    ("⏪ −20m", {"d_lon_m": -20.0},    "w20"),
    ("⏩ +20m", {"d_lon_m": +20.0},    "e20"),
    ("⏫ +20m", {"d_lat_m": +20.0},    "n20"),
    ("⏬ −20m", {"d_lat_m": -20.0},    "s20"),
]):
    with nudge_cols[i]:
        if st.button(lbl, width="stretch", disabled=marker_lat is None,
                      key=f"nudge_{key}"):
            nudge(**kw)


# ─────────────────────────────────────────────────────────────────────
# Big map (2025 reference)
# ─────────────────────────────────────────────────────────────────────
# Planet NICFI / commercial monthly basemaps (API key tied to project)
PLANET_API_KEY = "PLAKab452efbf9b14cf2b10670ea24b3e0ef"

def add_basemap(m, key, year=None):
    rel = wayback_rel.get(str(year))
    if key == "Esri Wayback 2025" and rel:
        folium.TileLayer(
            f"https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{rel['release_id']}/{{z}}/{{y}}/{{x}}",
            attr=f"Wayback {rel['date']}",
            maxNativeZoom=17, maxZoom=22).add_to(m)
    elif key.startswith("Planet "):
        # key format "Planet YYYY-MM" → global_monthly_YYYY_MM_mosaic, 5m
        ym = key.replace("Planet ", "").replace("-", "_")  # 2025-05 → 2025_05
        mosaic = f"global_monthly_{ym}_mosaic"
        folium.TileLayer(
            f"https://tiles.planet.com/basemaps/v1/planet-tiles/{mosaic}/gmap/{{z}}/{{x}}/{{y}}.png?api_key={PLANET_API_KEY}",
            attr=f"Planet {ym.replace('_','-')} (5m)",
            maxNativeZoom=18, maxZoom=22).add_to(m)
    elif key == "Esri current":
        folium.TileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri current",
            maxNativeZoom=18, maxZoom=22).add_to(m)
    elif key == "Google Satellite":
        folium.TileLayer(
            "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
            attr="Google Satellite",
            maxNativeZoom=20, maxZoom=22).add_to(m)
    elif key == "Google Hybrid":
        folium.TileLayer(
            "https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
            attr="Google Hybrid",
            maxNativeZoom=20, maxZoom=22).add_to(m)
    elif key == "CartoDB Dark":
        folium.TileLayer(
            "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
            attr="© CARTO © OSM",
            maxNativeZoom=20, maxZoom=22).add_to(m)
    elif key == "OpenStreetMap":
        folium.TileLayer(
            "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            attr="© OpenStreetMap",
            maxNativeZoom=19, maxZoom=22).add_to(m)


big_map = folium.Map(location=[cur_lat, cur_lon],
                       zoom_start=big_zoom, tiles=None,
                       control_scale=True, max_zoom=22, zoom_control=True)
add_basemap(big_map, selected_basemap, year=2025)

# v29 model prob overlay (tiles bundled in this same repo, raw CDN)
V29_TILE_URL = (
    "https://raw.githubusercontent.com/iortiz1891/agave-labeler-public/"
    "main/tiles/agave_prob_v29/{z}/{x}/{y}.png"
)
folium.TileLayer(
    V29_TILE_URL,
    attr="v29 prob (model)",
    name="🎯 v29 prob Agave",
    overlay=True, show=True,
    maxNativeZoom=14, maxZoom=22, opacity=0.65,
).add_to(big_map)

if sample_polygon:
    folium.GeoJson(sample_polygon, style_function=lambda x: {
        "fillOpacity":0.10, "fillColor":"#FFD700",
        "color":"#FFD700", "weight":4}).add_to(big_map)
    folium.CircleMarker([marker_lat, marker_lon], radius=5,
                         color="#FFD700", fill=True, fill_opacity=1.0).add_to(big_map)

# Show all 2025 prior labels (latest 200 unique locations)
seen = set()
for s in samples_recent:
    if s.get("year") != 2025 or "polygon" not in s: continue
    loc_key = (round(s["lat"],5), round(s["lon"],5))
    if loc_key in seen: continue
    seen.add(loc_key)
    if len(seen) > 200: break
    c = COLOR_MAP.get(s.get("label"), "#FFD700")
    poly = s["polygon"]
    if isinstance(poly, str):
        try: poly = json.loads(poly)
        except: continue
    folium.GeoJson(
        {"type": "Feature", "geometry": poly, "properties": {}},
        style_function=lambda x, c=c: {
            "fillOpacity":0.0, "color":c, "weight":2,
            "dashArray":"3,3"}).add_to(big_map)

# LayerControl must be added AFTER all overlays
folium.LayerControl(collapsed=True, position="topright").add_to(big_map)

big_click = st_folium(big_map, height=560, width=None,
                        returned_objects=["last_clicked"],
                        key="int_big_map_static")

if big_click and big_click.get("last_clicked"):
    lc = big_click["last_clicked"]
    nl = lc.get("lat"); ng = lc.get("lng")
    if nl is not None and ng is not None:
        if (marker_lat is None or
                abs(nl - marker_lat) > 1e-7 or
                abs(ng - marker_lon) > 1e-7):
            st.session_state["int_lat"] = float(nl)
            st.session_state["int_lon"] = float(ng)
            st.session_state["int_marker_lat"] = float(nl)
            st.session_state["int_marker_lon"] = float(ng)
            st.rerun()


# ─────────────────────────────────────────────────────────────────────
# Mini-map grid (10 years, static HTML)
# ─────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, max_entries=200)
def render_mini_html(year, lat, lon, zoom, marker_lat, marker_lon,
                      poly_str, height=200):
    m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles=None,
                    control_scale=False, max_zoom=22, zoom_control=False,
                    width=320, height=height)
    rel = wayback_rel.get(str(year))
    if rel:
        folium.TileLayer(
            f"https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{rel['release_id']}/{{z}}/{{y}}/{{x}}",
            attr=f"WB {rel['date']}",
            name=f"Esri Wayback {year}",
            maxNativeZoom=17, maxZoom=22).add_to(m)
    else:
        folium.TileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri current",
            name=f"Esri current {year}",
            maxNativeZoom=18, maxZoom=22).add_to(m)
    # Planet monthly mosaic (May of same year) — alt source for cross-validation
    try:
        if 2017 <= int(year) <= 2025:
            mosaic = f"global_monthly_{int(year)}_05_mosaic"
            folium.TileLayer(
                f"https://tiles.planet.com/basemaps/v1/planet-tiles/{mosaic}/gmap/{{z}}/{{x}}/{{y}}.png?api_key={PLANET_API_KEY}",
                attr=f"Planet {year}-05 (5m)",
                name=f"Planet {year}-05",
                overlay=True, show=False,
                maxNativeZoom=18, maxZoom=22).add_to(m)
    except Exception:
        pass
    if poly_str:
        try:
            poly = json.loads(poly_str)
            folium.GeoJson(
                {"type": "Feature", "geometry": poly, "properties": {}},
                style_function=lambda x: {"fillOpacity":0.10,
                                           "fillColor":"#FFD700",
                                           "color":"#FFD700","weight":3}
            ).add_to(m)
        except: pass
    folium.LayerControl(collapsed=True, position="topright").add_to(m)
    return m._repr_html_()


st.markdown("---")
st.markdown("### Grid 10 años — read-only mini-mapas")
st.caption("Los mapas son sólo lectura — usa el grande arriba para click.")

mini_lat = marker_lat if marker_lat is not None else cur_lat
mini_lon = marker_lon if marker_lon is not None else cur_lon
mini_zoom = max(big_zoom, 17)

# Bulk-set all radios buttons (Yes all / No all)
bulk_cols = st.columns([1, 1, 4])
with bulk_cols[0]:
    if st.button("✅ Yes all", width="stretch",
                  help="Marca los 10 años como yes"):
        for y in ALL_YEARS:
            st.session_state[f"int_radio_{y}_r{ROUND}"] = "yes"
        st.rerun()
with bulk_cols[1]:
    if st.button("❌ No all", width="stretch",
                  help="Marca los 10 años como no (default)"):
        for y in ALL_YEARS:
            st.session_state[f"int_radio_{y}_r{ROUND}"] = "no"
        st.rerun()
with bulk_cols[2]:
    st.caption("ℹ️ Bulk-set: ahorra clicks cuando la parcela es claramente la misma clase en TODOS los años.")

radio_choices = {}
poly_str = json.dumps(sample_polygon_geojson) if sample_polygon_geojson else ""
for row_years in [ALL_YEARS[:5], ALL_YEARS[5:]]:
    cols_grid = st.columns(len(row_years))
    for ci, y in enumerate(row_years):
        with cols_grid[ci]:
            is_2025 = (y == 2025)
            st.markdown(f"**{y}** {'⭐' if is_2025 else ''}")
            html = render_mini_html(year=y, lat=mini_lat, lon=mini_lon,
                                      zoom=mini_zoom,
                                      marker_lat=marker_lat,
                                      marker_lon=marker_lon,
                                      poly_str=poly_str, height=190)
            st_components.html(html, height=200, scrolling=False)
            radio_key = f"int_radio_{y}_r{ROUND}"
            chosen = st.radio(
                f"label_{y}", options=LABEL_OPTS,
                format_func=lambda x: LABEL_LBLS[x],
                index=1, horizontal=True, label_visibility="collapsed",
                key=radio_key)
            radio_choices[y] = chosen


# ─────────────────────────────────────────────────────────────────────
# Save & info
# ─────────────────────────────────────────────────────────────────────
st.markdown("---")
if marker_lat is None:
    st.info("👆 Click en el mapa grande arriba para colocar un cuadrado de muestra.")
else:
    cinfo, cact = st.columns([1, 2])
    with cinfo:
        n_yes_now = sum(1 for v in radio_choices.values() if v == "yes")
        n_no_now  = sum(1 for v in radio_choices.values() if v == "no")
        n_unsure  = sum(1 for v in radio_choices.values() if v == "unsure")
        st.code(f"lat: {marker_lat:.6f}\nlon: {marker_lon:.6f}\n"
                  f"sample: 90 × 90 m\n"
                  f"radios: {n_yes_now} yes / {n_no_now} no / {n_unsure} unsure")
    with cact:
        st.markdown("**Save all 10 years (1 click → 10 records → auto-clear marker):**")

        def save_all():
            base_id = f"INT_{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:6]}"
            labeller = st.session_state.get("labeller_name", "anonymous")
            records = []
            for y, lab in radio_choices.items():
                rec = {
                    "id": f"{base_id}_{y}",
                    "lat": float(marker_lat),
                    "lon": float(marker_lon),
                    "polygon": sample_polygon_geojson,
                    "year": int(y),
                    "label": lab,
                    "labeller": labeller,
                    "datetime": datetime.utcnow().isoformat(),
                    "base_id": base_id,
                }
                records.append(rec)
            ok = insert_labels(records)
            if ok:
                st.toast("✅ Saved!", icon="🎯")
                st.session_state["int_marker_lat"] = None
                st.session_state["int_marker_lon"] = None
                st.session_state["int_round"] = ROUND + 1
                # Invalidate cache so next render shows the new label
                fetch_recent_samples.clear()
                st.rerun()

        bb = st.columns(2)
        with bb[0]:
            if st.button(f"💾 Save All {len(ALL_YEARS)} years & Next ▶",
                          width="stretch", type="primary"):
                save_all()
        with bb[1]:
            if st.button("↩️ Cancel (clear marker)", width="stretch"):
                st.session_state["int_marker_lat"] = None
                st.session_state["int_marker_lon"] = None
                st.session_state["int_round"] = ROUND + 1
                st.rerun()


# ─────────────────────────────────────────────────────────────────────
# Recent labels (collapsible)
# ─────────────────────────────────────────────────────────────────────
with st.expander("📊 Tus contribuciones recientes + global stats",
                  expanded=False):
    me = st.session_state.get("labeller_name", "")
    my_records = [s for s in samples_recent if s.get("labeller") == me]
    st.write(f"**Tus contribuciones**: {len(my_records)} records "
              f"({len(set((round(s['lat'],5),round(s['lon'],5)) for s in my_records))} ubicaciones)")

    import pandas as pd
    if my_records:
        df = pd.DataFrame([{
            "year": s.get("year"), "label": s.get("label"),
            "lat": round(s.get("lat",0), 5), "lon": round(s.get("lon",0), 5),
            "datetime": s.get("datetime","")[:19],
        } for s in my_records[:30]])
        st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("---")
    st.markdown("**Global stats** (todos los etiquetadores):")
    if samples_recent:
        from collections import Counter
        labellers = Counter(s.get("labeller","?") for s in samples_recent)
        st.write({k: v for k, v in labellers.most_common(10)})
