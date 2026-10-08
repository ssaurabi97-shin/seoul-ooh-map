import json
import os
import re
import urllib.request

import duckdb
import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
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
DONG_PATH = os.path.join(BASE_DIR, "grid_dong.csv")  # 격자ID -> 자치구/행정동 (build_grid_dong.py로 생성)
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
DEFAULT_AGES = ["20-24세", "25-29세", "30-34세", "35-39세"]

# 단계 경계(누적 비율)와 색상: 낮음(초록) -> 높음(빨강). 한강(파랑)과 구분되도록 파랑 계열은 쓰지 않음
LEVEL_BINS = [0, 0.25, 0.50, 0.80, 0.90, 1.0]
LEVEL_LABELS = {1: "0-25%", 2: "26-50%", 3: "51-80%", 4: "81-90%", 5: "91-100%"}
LEVEL_COLORS = {
    1: [143, 209, 117, 210],   # 연한 초록
    2: [46, 158, 79, 220],     # 진한 초록
    3: [254, 217, 118, 220],   # 노랑
    4: [253, 141, 60, 230],    # 주황
    5: [215, 25, 28, 240],     # 빨강
}
LEVEL_HEX = {1: "#8FD175", 2: "#2E9E4F", 3: "#FED976", 4: "#FD8D3C", 5: "#D7191C"}
LEVEL_TEXT = {1: "black", 2: "white", 3: "black", 4: "black", 5: "white"}


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
MAX_DAYS = int(get_setting("MAX_DAYS", "1000"))


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


@st.cache_data
def load_dong():
    if not os.path.exists(DONG_PATH):
        return None
    d = pd.read_csv(DONG_PATH, dtype=str).fillna("")
    d["grid_id"] = d["grid_id"].str.strip()
    return d


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


@st.cache_data(ttl=3600, show_spinner=False)
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

# --- 분석 조건 입력 폼: '실행하기'를 누를 때만 데이터를 조회합니다 ---
age_options = sorted({a for _, a in colmap.values()}, key=age_sort_key)
default_ages = [a for a in DEFAULT_AGES if a in age_options] or age_options
theme_map = {"기본 (Base)": "Base", "야간 (Midnight)": "midnight",
             "위성 (Satellite)": "Satellite", "백지도 (White)": "white"}
default_start = max(all_dates.min(), all_dates.max() - pd.Timedelta(days=13))
secret_key = get_setting("VWORLD_API_KEY")

with st.sidebar.form("filters"):
    st.markdown("### 🎯 분석 조건")
    rng = st.date_input("분석 기간 (하루만 보려면 시작일=종료일)",
                        value=(default_start.date(), all_dates.max().date()),
                        min_value=all_dates.min().date(), max_value=all_dates.max().date())
    wd = st.multiselect("포함할 요일", WEEKDAYS, default=WEEKDAYS)
    time_range = st.slider("시간대 범위 (시)", 0, 23, (8, 23))
    selected_gender = st.selectbox("성별", ["전체", "여성", "남성"])
    selected_ages = st.multiselect("연령대", age_options, default=default_ages)

    st.markdown("### 🗺️ 지도 옵션")
    viz_type = st.radio("표시 방식", ["2D 도트 (Scatter)", "3D 기둥 (Column)", "2D 열지도 (Heatmap)"])
    dot_size = st.slider("도트 크기 / 열지도 반경", 1, 10, 3)
    theme_label = st.selectbox("배경 테마", list(theme_map))
    form_key = "" if secret_key else st.text_input("브이월드 API 키", type="password")

    submitted = st.form_submit_button("🔍 실행하기", type="primary")

if submitted:
    st.session_state["params"] = dict(
        rng=rng, wd=wd, time_range=time_range, gender=selected_gender, ages=selected_ages,
        viz_type=viz_type, dot_size=dot_size, theme_label=theme_label,
        api_key=secret_key or form_key,
    )

# ==========================================
# 3. 집계
# ==========================================
st.title("🗺️ 서울시 250m 격자 타겟 생활인구 지도")
st.caption("왼쪽에서 조건을 모두 설정한 뒤 '실행하기'를 누르면 해당 조건의 타겟 인구를 집계해 지도에 표시합니다.")

P = st.session_state.get("params")
if P is None:
    st.info("👈 왼쪽 사이드바에서 분석 조건을 설정하고 **실행하기**를 눌러주세요.")
    st.stop()

time_range, selected_gender, selected_ages = P["time_range"], P["gender"], P["ages"]
viz_type, dot_size, api_key = P["viz_type"], P["dot_size"], P["api_key"]
theme = theme_map[P["theme_label"]]

# 기간·요일 -> 실제 데이터가 있는 일자 목록
rng_sel = P["rng"] if isinstance(P["rng"], (tuple, list)) else (P["rng"],)
if len(rng_sel) == 0:
    st.warning("분석 기간을 선택하세요.")
    st.stop()
d_start, d_end = rng_sel[0], rng_sel[-1]  # 하루만 고른 경우 시작일=종료일
wd_idx = [WEEKDAYS.index(w) for w in P["wd"]]
in_range = all_dates[(all_dates >= pd.Timestamp(d_start)) & (all_dates <= pd.Timestamp(d_end))]
selected_dates = tuple(x.strftime("%Y%m%d") for x in in_range if x.weekday() in wd_idx)

if not selected_dates:
    st.warning("선택한 조건에 해당하는 일자가 없습니다.")
    st.stop()
if len(selected_dates) > MAX_DAYS:
    st.warning(f"한 번에 최대 {MAX_DAYS}일까지 집계할 수 있습니다. (현재 {len(selected_dates)}일) 기간을 줄이거나 Secrets의 MAX_DAYS 값을 늘려주세요.")
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
    with st.spinner(f"{len(selected_dates)}일치 인구 데이터를 집계하는 중... (기간이 길면 1~2분 걸릴 수 있습니다)"):
        df_pop = query_target(DATA_BASE, selected_dates, target_cols, time_range[0], time_range[1])
except Exception as e:
    st.error(f"데이터 집계 중 오류: {e}")
    st.stop()

n_days = len(selected_dates)
n_hours = time_range[1] - time_range[0] + 1
df_pop["target_pop"] = df_pop["s"] / (n_days * n_hours)  # 지도 색상·높이 기준: 시간당 평균 인구
df_pop["grid_id"] = df_pop["grid_id"].astype(str).str.strip()

merged = df_pop[["grid_id", "s", "target_pop"]].merge(load_grid(), on="grid_id", how="inner")
if merged.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. (격자ID 불일치 가능)")
    st.stop()

# 인구가 적은 순서대로 순위를 매긴 뒤 누적 비율 구간(0-25-50-80-90-100%)으로 5단계 분류
pct = merged["target_pop"].rank(method="first") / len(merged)
merged["level"] = pd.cut(pct, bins=LEVEL_BINS, labels=[1, 2, 3, 4, 5], include_lowest=True).astype(int)
merged["color"] = merged["level"].map(LEVEL_COLORS)
merged["pop_label"] = merged["target_pop"].round(0).astype(int)

with st.expander("🔎 데이터 점검 정보"):
    st.write(f"데이터 위치: `{DATA_BASE}`")
    st.write(f"집계 일수: {n_days}일 / 시간대: {time_range[0]}~{time_range[1]}시 ({n_hours}시간)")
    st.write(f"사용 컬럼 {len(target_cols)}개: {', '.join(target_cols)}")
    st.write(f"인구 격자 {len(df_pop):,}개 중 좌표 매칭 {len(merged):,}개")

# 타겟 인구 총합: 지도에 표시된 격자 전체 기준
total_sum = float(merged["s"].sum())  # 선택 일자·시간대·격자의 인구를 모두 더한 값
m1, m2 = st.columns(2)
m1.metric("타겟 인구 총합 (시간당 평균)", f"{total_sum / (n_days * n_hours):,.0f}명",
          help="선택한 일자·시간대의 시간별 인구를 평균낸 값 (모든 격자 합계)")
m2.metric("타겟 인구 총합 (기간 총)", f"{total_sum:,.0f}명",
          help="선택한 모든 일자·시간대의 시간별 인구를 전부 더한 값 (연인원)")
wd_text = "전체 요일" if len(P["wd"]) == 7 else "·".join(P["wd"])
st.caption(f"적용 조건: {d_start:%Y-%m-%d} ~ {d_end:%Y-%m-%d} ({n_days}일, {wd_text}) · "
           f"{time_range[0]}~{time_range[1]}시 · 성별 {selected_gender} · 연령 {', '.join(selected_ages)}")

legend = "".join(
    f'<span style="background:{LEVEL_HEX[i]};color:{LEVEL_TEXT[i]};padding:4px 10px;'
    f'border-radius:4px;margin-right:6px;">{i}단계 ({LEVEL_LABELS[i]})</span>'
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
      return {html:'격자ID: '+d.i+'<br>시간당 평균 타겟 인구: '+Number(d.p).toLocaleString()+'명',
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
# 5. 타겟 인구 상위 지역 (주식 히트맵 스타일 트리맵)
# ==========================================
TOP_N = 20
TILE_SCALE = [[0.0, "#2E9E4F"], [0.35, "#FED976"], [0.7, "#FD8D3C"], [1.0, "#D7191C"]]


def tile_colors(values):
    """타일 면적과 같은 값(인구)을 초록->노랑->주황->빨강으로 매핑하고, 글자색(흑/백)도 함께 결정"""
    lo, hi = min(values), max(values)
    norm = [(v - lo) / (hi - lo) if hi > lo else 1.0 for v in values]
    colors = pc.sample_colorscale(TILE_SCALE, norm)
    fonts = []
    for c in colors:
        r, g, b = [int(x) for x in c.replace("rgb(", "").replace(")", "").split(",")]
        fonts.append("#1b1b1b" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff")
    return colors, fonts


def build_treemap(top, label_col, value_col, group_col, custom_col, hover_extra):
    """자치구(구분 헤더) > 행정동(타일) 구조의 트리맵. 타일 면적 = 타겟 인구"""
    colors, fonts = tile_colors(top[value_col].tolist())
    groups = top.groupby(group_col)[value_col].sum().sort_values(ascending=False)
    ids, labels, parents, values, node_colors, node_fonts, custom = [], [], [], [], [], [], []
    for g_name, g_val in groups.items():
        ids.append(f"G|{g_name}")
        labels.append(f"<b>{g_name}</b>")
        parents.append("")
        values.append(float(g_val))
        node_colors.append("#2b303b")
        node_fonts.append("#ffffff")
        custom.append(["", ""])
    for (_, row), c, f in zip(top.iterrows(), colors, fonts):
        ids.append(f"T|{row['tile_id']}")
        labels.append(row[label_col])
        parents.append(f"G|{row[group_col]}")
        values.append(float(row[value_col]))
        node_colors.append(c)
        node_fonts.append(f)
        custom.append([f"{row[value_col]:,.0f}명", row[custom_col]])
    fig = go.Figure(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
        marker=dict(colors=node_colors, line=dict(width=1.5, color="#14171f")),
        customdata=custom,
        texttemplate="<b>%{label}</b><br>%{customdata[0]}",
        textfont=dict(color=node_fonts, size=15),
        textposition="middle center",
        hovertemplate="<b>%{label}</b><br>시간당 평균 타겟 인구: %{customdata[0]}<br>" + hover_extra + "<extra></extra>",
        tiling=dict(pad=3), pathbar=dict(visible=False), sort=True,
    ))
    fig.update_layout(margin=dict(t=4, l=4, r=4, b=4), height=560,
                      paper_bgcolor="rgba(0,0,0,0)", font=dict(family="sans-serif"))
    return fig


st.markdown("### 📊 타겟 인구 상위 지역")
unit_choice = st.radio("표시 단위", ["격자 개별 (상위 20개 격자)", "행정동 합산 (상위 20개 행정동)"],
                       horizontal=True, key="rank_unit",
                       help="격자 개별: 250m 격자 하나가 타일 하나 / 행정동 합산: 같은 행정동의 격자를 모두 더해 타일 하나")

dong_df = load_dong()
base = merged[["grid_id", "target_pop"]].copy()
if dong_df is not None:
    base = base.merge(dong_df, on="grid_id", how="left")
else:
    base["sgg"], base["dong"] = "", ""
base["sgg"] = base["sgg"].fillna("").replace("", "구 미확인")
base["dong"] = base["dong"].fillna("")
base.loc[base["dong"] == "", "dong"] = base["grid_id"]  # 매핑이 없으면 격자ID로 대체
if dong_df is None:
    st.info("grid_dong.csv가 없어 행정동 대신 격자ID로 표시합니다. 저장소에 grid_dong.csv를 올려주세요.")

all_total = float(base["target_pop"].sum())
if unit_choice.startswith("격자"):
    top = base.nlargest(TOP_N, "target_pop").reset_index(drop=True)
    # 같은 행정동에 속한 격자가 여러 개면 (2), (3) ... 순번을 붙여 구분
    seq = top.groupby(["sgg", "dong"]).cumcount() + 1
    cnt = top.groupby(["sgg", "dong"])["dong"].transform("size")
    top["label"] = [d if c == 1 else f"{d} ({n})" for d, n, c in zip(top["dong"], seq, cnt)]
    top["tile_id"] = top["grid_id"]
    fig = build_treemap(top, "label", "target_pop", "sgg", "grid_id", "격자ID: %{customdata[1]}")
    detail = pd.DataFrame({
        "순위": range(1, len(top) + 1), "자치구": top["sgg"], "행정동": top["dong"],
        "격자ID": top["grid_id"], "시간당 평균 타겟 인구(명)": top["target_pop"].round(0).astype(int),
        "전체 대비 비중(%)": (top["target_pop"] / all_total * 100).round(2),
    })
else:
    agg = (base.groupby(["sgg", "dong"], as_index=False)
           .agg(target_pop=("target_pop", "sum"), n_grid=("grid_id", "size")))
    top = agg.nlargest(TOP_N, "target_pop").reset_index(drop=True)
    top["label"] = top["dong"]
    top["tile_id"] = top["sgg"] + "/" + top["dong"]
    top["info"] = top["n_grid"].astype(str) + "개 격자 합산"
    fig = build_treemap(top, "label", "target_pop", "sgg", "info", "%{customdata[1]}")
    detail = pd.DataFrame({
        "순위": range(1, len(top) + 1), "자치구": top["sgg"], "행정동": top["dong"],
        "합산 격자 수": top["n_grid"], "시간당 평균 타겟 인구(명)": top["target_pop"].round(0).astype(int),
        "전체 대비 비중(%)": (top["target_pop"] / all_total * 100).round(2),
    })

top_share = float(top["target_pop"].sum()) / all_total * 100 if all_total else 0
st.caption(f"타일 면적·색상 = 시간당 평균 타겟 인구 (초록 → 빨강: 많을수록 크고 붉게). "
           f"상위 {len(top)}개가 전체 타겟 인구의 {top_share:.1f}%를 차지합니다.")
st.plotly_chart(fig, theme=None, config={"displayModeBar": False})

with st.expander("📋 상세 표 보기"):
    st.dataframe(detail, hide_index=True)
