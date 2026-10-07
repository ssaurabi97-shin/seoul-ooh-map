import json
import os
import re
import urllib.request

import duckdb
import pandas as pd
import streamlit as st

# ==========================================
# 0. 기본 설정
# ==========================================
st.set_page_config(
    page_title="서울시 250m 격자 타겟 생활인구 지도",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GRID_PATH = os.path.join(BASE_DIR, "grid_coords.csv")
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
DEFAULT_AGES = ["20-24세", "25-29세", "30-34세", "35-39세"]

LEVEL_COLORS = {
    1: [49, 130, 189, 170],
    2: [107, 174, 214, 190],
    3: [254, 217, 118, 210],
    4: [253, 141, 60, 225],
    5: [215, 25, 28, 240],
}
LEVEL_HEX = {1: "#3182BD", 2: "#6BAED6", 3: "#FED976", 4: "#FD8D3C", 5: "#D7191C"}
LEVEL_TEXT = {1: "white", 2: "black", 3: "black", 4: "black", 5: "white"}


def get_setting(name, default=""):
    try:
        val = st.secrets.get(name, "")
    except Exception:
        val = ""
    return val or os.environ.get(name, "") or default


# 데이터 위치: 외부 저장소 URL(권장) 또는 로컬 data 폴더
DATA_BASE = get_setting("DATA_BASE_URL", os.path.join(BASE_DIR, "data")).rstrip("/")
IS_REMOTE = DATA_BASE.startswith("http")
# 한 번에 집계할 최대 일수 (원격 저장소 읽기 속도 보호). Secrets의 MAX_DAYS로 조정 가능
MAX_DAYS = int(get_setting("MAX_DAYS", "92"))


# ==========================================
# 1. 데이터 접근
# ==========================================
@st.cache_data(ttl=600, show_spinner="데이터 목록 확인 중...")
def load_manifest(base):
    if base.startswith("http"):
        req = urllib.request.Request(f"{base}/manifest.json", headers={"User-Agent": "seoul-ooh-map"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    with open(os.path.join(base, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_grid():
    g = pd.read_csv(GRID_PATH, dtype={"grid_id": str})
    g["grid_id"] = g["grid_id"].str.strip()
    return g


def sql_str(s):
    return "'" + str(s).replace("'", "''") + "'"


@st.cache_resource
def get_con(remote):
    con = duckdb.connect()
    if remote:
        try:
            con.execute("INSTALL httpfs")
        except Exception:
            pass
        con.execute("LOAD httpfs")
        for q in ("SET enable_http_metadata_cache=true", "SET http_keep_alive=true"):
            try:
                con.execute(q)
            except Exception:
                pass
    return con


@st.cache_data(ttl=3600, show_spinner="선택한 기간의 인구 데이터를 집계하는 중...")
def query_target(base, dates, cols, h0, h1):
    """일별 Parquet에서 필요한 컬럼만 읽어 격자별 합계를 계산 (DuckDB)"""
    if not all(re.fullmatch(r"[a-z0-9_]+", c) for c in cols):
        raise ValueError("허용되지 않은 컬럼명")
    paths = [f"{base}/daily/{d}.parquet" for d in dates]
    cur = get_con(base.startswith("http")).cursor()
    try:
        expr = " + ".join(f'"{c}"' for c in cols)
        files = "[" + ",".join(sql_str(p) for p in paths) + "]"
        sql = (f"SELECT grid_id, SUM({expr}) AS s "
               f"FROM read_parquet({files}, union_by_name=true) "
               f"WHERE hour BETWEEN {int(h0)} AND {int(h1)} GROUP BY grid_id")
        return cur.execute(sql).df()
    finally:
        cur.close()


def age_sort_key(label):
    return int(re.findall(r"\d+", label)[0])


# ==========================================
# 2. 사이드바
# ==========================================
st.sidebar.title("📌 분석 설정")

try:
    manifest = load_manifest(DATA_BASE)
except Exception as e:
    st.title("🗺️ 서울시 250m 격자 타겟 생활인구 지도")
    st.error(f"데이터 목록(manifest.json)을 불러오지 못했습니다: {e}")
    st.caption(f"데이터 위치: {DATA_BASE}")
    st.stop()

if not manifest.get("dates") or not manifest.get("columns"):
    st.title("🗺️ 서울시 250m 격자 타겟 생활인구 지도")
    st.warning("변환된 인구 데이터가 아직 없습니다. (manifest.json의 dates가 비어 있음)\n\n"
               "build_data.py를 실행해 data/daily/*.parquet 과 manifest.json 을 생성한 뒤 다시 확인하세요.")
    st.caption(f"데이터 위치: {DATA_BASE}")
    st.stop()

if not os.path.exists(GRID_PATH):
    st.title("🗺️ 서울시 250m 격자 타겟 생활인구 지도")
    st.error("grid_coords.csv가 저장소에 없습니다. build_data.py로 생성해 업로드하세요.")
    st.stop()

all_dates = pd.to_datetime(manifest["dates"], format="%Y%m%d")
colmap = {c: tuple(v) for c, v in manifest["columns"].items()}
st.sidebar.success(f"💾 {len(all_dates)}일 데이터 ({all_dates.min():%Y-%m-%d} ~ {all_dates.max():%Y-%m-%d})")

# --- 기간 / 요일 ---
mode = st.sidebar.radio("일자 선택 방식", ["단일 일자", "기간 평균"], horizontal=True)
if mode == "단일 일자":
    d = st.sidebar.date_input("분석 일자", value=all_dates.max().date(),
                              min_value=all_dates.min().date(), max_value=all_dates.max().date())
    picked = all_dates[all_dates.normalize() == pd.Timestamp(d)]
    selected_days = list(picked)
else:
    default_start = max(all_dates.min(), all_dates.max() - pd.Timedelta(days=13))
    rng = st.sidebar.date_input("분석 기간", value=(default_start.date(), all_dates.max().date()),
                                min_value=all_dates.min().date(), max_value=all_dates.max().date())
    if not isinstance(rng, (tuple, list)) or len(rng) != 2:
        st.sidebar.info("시작일과 종료일을 모두 선택하세요.")
        st.stop()
    wd = st.sidebar.multiselect("포함할 요일", WEEKDAYS, default=WEEKDAYS)
    wd_idx = [WEEKDAYS.index(w) for w in wd]
    in_range = all_dates[(all_dates >= pd.Timestamp(rng[0])) & (all_dates <= pd.Timestamp(rng[1]))]
    selected_days = [x for x in in_range if x.weekday() in wd_idx]

selected_dates = tuple(x.strftime("%Y%m%d") for x in selected_days)

# --- 시간 / 성별 / 연령 ---
time_range = st.sidebar.slider("시간대 범위 (시)", 0, 23, (8, 23))
selected_gender = st.sidebar.selectbox("성별", ["전체", "여성", "남성"])
age_options = sorted({a for _, a in colmap.values()}, key=age_sort_key)
default_ages = [a for a in DEFAULT_AGES if a in age_options] or age_options
selected_ages = st.sidebar.multiselect("연령대", age_options, default=default_ages)

agg_mode = st.sidebar.radio("집계 방식", ["시간당 평균 인구", "일평균 시간대 합계 (연인원)"],
                            help="평균: 선택 시간대·일자의 시간당 평균 인구 / 합계: 하루 기준 선택 시간대 인구를 모두 더한 값")

st.sidebar.divider()
st.sidebar.markdown("### 🗺️ 지도 옵션")
viz_type = st.sidebar.radio("표시 방식", ["2D 도트 (Scatter)", "3D 기둥 (Column)", "2D 열지도 (Heatmap)"])
dot_size = st.sidebar.slider("도트 크기 / 열지도 반경", 1, 10, 3)
theme_map = {"기본 (Base)": "Base", "야간 (Midnight)": "midnight",
             "위성 (Satellite)": "Satellite", "백지도 (White)": "white"}
theme = theme_map[st.sidebar.selectbox("배경 테마", list(theme_map))]

api_key = get_setting("VWORLD_API_KEY")
if not api_key:
    api_key = st.sidebar.text_input("브이월드 API 키", type="password")

# ==========================================
# 3. 집계
# ==========================================
st.title("🗺️ 서울시 250m 격자 타겟 생활인구 지도")
st.caption("성별·연령·시간대·기간 조건으로 타겟 인구를 집계해 브이월드 배경지도 위에 표시합니다.")

if not selected_dates:
    st.warning("선택한 조건에 해당하는 일자가 없습니다.")
    st.stop()
if len(selected_dates) > MAX_DAYS:
    st.warning(f"한 번에 최대 {MAX_DAYS}일까지 집계할 수 있습니다. (현재 {len(selected_dates)}일) 기간을 줄여주세요.")
    st.stop()
if not selected_ages:
    st.warning("연령대를 1개 이상 선택하세요.")
    st.stop()

target_cols = tuple(sorted(c for c, (g, a) in colmap.items()
                           if (selected_gender == "전체" or g == selected_gender) and a in selected_ages))
if not target_cols:
    st.warning("선택한 성별·연령대에 해당하는 컬럼이 없습니다.")
    st.stop()

try:
    df_pop = query_target(DATA_BASE, selected_dates, target_cols, time_range[0], time_range[1])
except Exception as e:
    st.error(f"데이터 집계 중 오류: {e}")
    st.stop()

n_days = len(selected_dates)
n_hours = time_range[1] - time_range[0] + 1
if agg_mode.startswith("시간당"):
    df_pop["target_pop"] = df_pop["s"] / (n_days * n_hours)
    unit = "시간당 평균"
else:
    df_pop["target_pop"] = df_pop["s"] / n_days
    unit = "일평균 연인원"
df_pop["grid_id"] = df_pop["grid_id"].astype(str).str.strip()

merged = df_pop[["grid_id", "target_pop"]].merge(load_grid(), on="grid_id", how="inner")
if merged.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. (격자ID 불일치 가능)")
    st.stop()

merged["level"] = pd.qcut(merged["target_pop"].rank(method="first"), 5, labels=False) + 1
merged["color"] = merged["level"].map(LEVEL_COLORS)
merged["pop_label"] = merged["target_pop"].round(0).astype(int)

with st.expander("🔎 데이터 점검 정보"):
    st.write(f"데이터 위치: `{DATA_BASE}`")
    st.write(f"집계 일수: {n_days}일 / 시간대: {time_range[0]}~{time_range[1]}시 ({n_hours}시간)")
    st.write(f"사용 컬럼 {len(target_cols)}개: {', '.join(target_cols)}")
    st.write(f"인구 격자 {len(df_pop):,}개 중 좌표 매칭 {len(merged):,}개")

c1, c2, c3 = st.columns(3)
c1.metric("표시 격자 수", f"{len(merged):,}")
c2.metric(f"타겟 인구 총합 ({unit})", f"{merged['target_pop'].sum():,.0f}명")
c3.metric("격자당 최대", f"{merged['target_pop'].max():,.0f}명")

legend = "".join(
    f'<span style="background:{LEVEL_HEX[i]};color:{LEVEL_TEXT[i]};padding:4px 10px;'
    f'border-radius:4px;margin-right:6px;">{i}단계 ({(i-1)*20}-{i*20}%)</span>'
    for i in range(1, 6)
)
st.markdown(f'<div style="margin:8px 0 12px 0;">{legend}</div>', unsafe_allow_html=True)

# ==========================================
# 4. 지도 (deck.gl + 브이월드 래스터 타일)
# ==========================================
# st.pydeck_chart 는 래스터 타일(TileLayer)을 그리는 사용자 정의 렌더러를 지원하지 않아
# 브이월드 배경이 흰 화면으로 나옵니다. 그래서 deck.gl을 iframe에 직접 로드합니다.
MAP_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  html,body{margin:0;height:100%;background:#dfe3e8;font-family:sans-serif}
  #map{position:absolute;inset:0}
  #msg{position:absolute;left:8px;bottom:8px;max-width:90%;padding:6px 10px;border-radius:4px;
       background:rgba(180,30,30,.92);color:#fff;font-size:12px;display:none;z-index:5}
  #info{position:absolute;right:8px;bottom:8px;padding:2px 6px;border-radius:3px;
        background:rgba(255,255,255,.75);color:#333;font-size:11px;z-index:4}
</style>
<script src="https://cdn.jsdelivr.net/npm/deck.gl@8.9.36/dist.min.js"
        onerror="loadFallback()"></script>
</head><body>
<div id="map"></div><div id="msg"></div><div id="info">__INFO__</div>
<script>
const DATA = __DATA__;
const CFG = __CFG__;
let started = false;
function show(t){const m=document.getElementById('msg');m.style.display='block';m.textContent=t;}
window.onerror = function(m){show('지도 오류: '+m);};
function loadFallback(){
  const s=document.createElement('script');
  s.src='https://unpkg.com/deck.gl@8.9.36/dist.min.js';
  s.onload=start;
  s.onerror=function(){show('deck.gl 라이브러리를 불러오지 못했습니다. (사내망/보안 설정으로 CDN이 막혔을 수 있습니다)');};
  document.head.appendChild(s);
}
function start(){
  if(started) return; started = true;
  const layers = [];
  let tileErrors = 0;
  if(CFG.tileUrl){
    layers.push(new deck.TileLayer({
      id:'vworld', data:CFG.tileUrl, minZoom:6, maxZoom:19, tileSize:256,
      onTileError:function(){
        tileErrors++;
        if(tileErrors===3) show('브이월드 타일을 불러오지 못했습니다. API 키와 허용 도메인(streamlit.app 주소) 등록을 확인하세요.');
      },
      renderSubLayers:function(props){
        const t=props.tile;
        let b;
        if(t.bbox && t.bbox.west!==undefined){ b=[t.bbox.west,t.bbox.south,t.bbox.east,t.bbox.north]; }
        else { const bb=t.boundingBox; b=[bb[0][0],bb[0][1],bb[1][0],bb[1][1]]; }
        return new deck.BitmapLayer(props,{data:null,image:props.data,bounds:b});
      }
    }));
  }
  if(CFG.viz==='scatter'){
    layers.push(new deck.ScatterplotLayer({
      id:'pts', data:DATA, pickable:true,
      getPosition:d=>[d.x,d.y], getFillColor:d=>d.c,
      getRadius:CFG.dot, radiusUnits:'pixels', radiusMinPixels:1
    }));
  } else if(CFG.viz==='column'){
    layers.push(new deck.ColumnLayer({
      id:'cols', data:DATA, pickable:true, extruded:true, diskResolution:12,
      radius:110, getPosition:d=>[d.x,d.y], getElevation:d=>d.e, getFillColor:d=>d.c
    }));
  } else {
    layers.push(new deck.HeatmapLayer({
      id:'heat', data:DATA, getPosition:d=>[d.x,d.y], getWeight:d=>d.w,
      radiusPixels:CFG.heat
    }));
  }
  new deck.Deck({
    parent:document.getElementById('map'),
    initialViewState:{longitude:126.978,latitude:37.5665,zoom:10.5,pitch:CFG.pitch,bearing:0},
    controller:true,
    layers:layers,
    getTooltip:function(o){
      const d=o.object;
      if(!d || d.i===undefined) return null;
      return {html:'격자ID: '+d.i+'<br>타겟 인구: '+Number(d.p).toLocaleString()+'명',
              style:{backgroundColor:'rgba(30,30,30,.9)',color:'#fff',fontSize:'12px'}};
    }
  });
}
window.addEventListener('load', function(){ if(typeof deck!=='undefined') start(); });
</script></body></html>"""


def build_map_html(df, viz, tile_url, dot, pitch, info):
    cols = ["grid_id", "lon", "lat", "pop_label", "target_pop", "color"]
    if viz == "column":
        cols.append("elevation")
    records = []
    for r in df[cols].itertuples(index=False):
        rec = {"i": r[0], "x": round(float(r[1]), 6), "y": round(float(r[2]), 6),
               "p": int(r[3]), "w": round(float(r[4]), 2), "c": [int(v) for v in r[5]]}
        if viz == "column":
            rec["e"] = round(float(r[6]), 1)
        records.append(rec)
    cfg = {"viz": viz, "tileUrl": tile_url, "dot": dot, "heat": dot * 10, "pitch": pitch}
    safe = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (MAP_HTML.replace("__DATA__", safe(records))
            .replace("__CFG__", safe(cfg))
            .replace("__INFO__", info))


tile_url = None
if api_key:
    ext = "jpeg" if theme == "Satellite" else "png"
    tile_url = f"https://api.vworld.kr/req/wmts/1.0.0/{api_key}/{theme}/{{z}}/{{y}}/{{x}}.{ext}"
else:
    st.info("브이월드 API 키가 없어 배경지도 없이 표시합니다.")

if "Scatter" in viz_type:
    viz_key = "scatter"
elif "Column" in viz_type:
    viz_key = "column"
    max_pop = merged["target_pop"].max() or 1
    merged["elevation"] = merged["target_pop"] / max_pop * 3000
else:
    viz_key = "heatmap"

map_html = build_map_html(merged, viz_key, tile_url, dot_size,
                          45 if viz_key == "column" else 0, "© VWorld" if tile_url else "")
if hasattr(st, "iframe"):
    st.iframe(map_html, height=650)
else:
    import streamlit.components.v1 as components
    components.html(map_html, height=650)

# ==========================================
# 5. 상위 20개 격자
# ==========================================
st.markdown("### 📊 타겟 인구 상위 20개 격자")
top20 = merged.sort_values("target_pop", ascending=False).head(20)
st.dataframe(
    top20[["grid_id", "pop_label", "lon", "lat"]].rename(
        columns={"grid_id": "격자 ID", "pop_label": "타겟 인구(명)", "lon": "경도", "lat": "위도"}),
    hide_index=True,
)
