import glob
import os
import re

import geopandas as gpd
import pandas as pd
import pydeck as pdk
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

# 컬럼명 후보 (실제 CSV 컬럼명이 다르면 여기에 추가)
ID_CANDS = ["격자ID", "격자id", "GRID_ID", "격자코드", "집계구코드", "TOT_REG_CD",
            "ADSTRD_CODE_SE", "GID", "ID"]
TIME_CANDS = ["시간대", "시간대구분", "TMZON", "TMZON_PD_SE", "HOUR", "시간"]
GENDER_CANDS = ["성별", "GENDER", "SEX"]
AGE_CANDS = ["연령대", "AGE_GRP", "AGE"]
POP_CANDS = ["인구수", "POP_CNT", "생활인구수", "LVPOP_CO"]

DEFAULT_AGES = ["20-24세", "25-29세", "30-34세", "35-39세"]

# 낮음(파랑) -> 높음(빨강)
LEVEL_COLORS = {
    1: [49, 130, 189, 170],
    2: [107, 174, 214, 190],
    3: [254, 217, 118, 210],
    4: [253, 141, 60, 225],
    5: [215, 25, 28, 240],
}
LEVEL_HEX = {1: "#3182BD", 2: "#6BAED6", 3: "#FED976", 4: "#FD8D3C", 5: "#D7191C"}
LEVEL_TEXT = {1: "white", 2: "black", 3: "black", 4: "black", 5: "white"}


# ==========================================
# 1. 유틸
# ==========================================
def find_col(columns, candidates):
    lowered = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


def read_csv_auto(path, **kwargs):
    """한글 CSV는 cp949인 경우가 많아 인코딩을 순서대로 시도"""
    last_err = None
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, encoding=enc, **kwargs)
        except UnicodeDecodeError as e:
            last_err = e
    raise last_err


def clean_id(series):
    return series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def norm_gender(value):
    s = str(value).strip().upper()
    if s.startswith("FEMALE") or "여" in s or s in ("F", "W"):
        return "여성"
    if s.startswith("MALE") or "남" in s or s == "M":
        return "남성"
    return None


def norm_age(text):
    nums = [int(n) for n in re.findall(r"\d+", str(text))]
    if not nums:
        return None
    lo = nums[0]
    if lo >= 70:
        return "70세 이상"
    hi = nums[1] if len(nums) > 1 else lo + 4
    return f"{lo}-{hi}세"


def age_sort_key(label):
    return int(re.findall(r"\d+", label)[0])


def parse_demo_col(name):
    """와이드 포맷 컬럼(예: 남자20세부터24세생활인구수, MALE_F20T24_LVPOP_CO) -> (성별, 연령대)"""
    s = str(name).strip()
    u = s.upper()
    if "TOT" in u or "합계" in s or s.startswith("총"):
        return None
    g = norm_gender(s)
    a = norm_age(s)
    if g and a:
        return g, a
    return None


# ==========================================
# 2. 데이터 로드 (일자 선택 시 해당 CSV만 로드)
# ==========================================
@st.cache_data
def list_data_files():
    files = {}
    for path in glob.glob(os.path.join(BASE_DIR, "*.csv")):
        name = os.path.basename(path)
        if name.lower() == "grid_coords.csv":
            continue
        m = re.search(r"(\d{8})", name)
        if m:
            files[m.group(1)] = path
    return dict(sorted(files.items()))


@st.cache_data(show_spinner="CSV 불러오는 중...")
def load_day(path):
    raw = read_csv_auto(path)
    cols = list(raw.columns)

    id_col = find_col(cols, ID_CANDS)
    t_col = find_col(cols, TIME_CANDS)
    if id_col is None or t_col is None:
        raise ValueError(f"격자ID/시간대 컬럼을 찾지 못했습니다. 실제 컬럼: {cols}")

    g_col = find_col(cols, GENDER_CANDS)
    a_col = find_col(cols, AGE_CANDS)
    p_col = find_col(cols, POP_CANDS)

    if g_col and a_col and p_col:
        # 롱 포맷: 행 = 격자·시간·성별·연령
        df = raw[[id_col, t_col, g_col, a_col, p_col]].copy()
        df.columns = ["grid_id", "hour", "g", "a", "pop"]
        df["g"] = df["g"].map(norm_gender)
        df["a"] = df["a"].map(norm_age)
        df["pop"] = pd.to_numeric(df["pop"], errors="coerce").fillna(0)
        df = df.dropna(subset=["g", "a"])
        df["key"] = df["g"] + "|" + df["a"]
        wide = df.pivot_table(index=["grid_id", "hour"], columns="key",
                              values="pop", aggfunc="sum", fill_value=0).reset_index()
        colmap = {k: tuple(k.split("|")) for k in wide.columns if "|" in str(k)}
        out = wide
    else:
        # 와이드 포맷: 컬럼 = 성별·연령대별 인구
        colmap = {}
        for c in cols:
            parsed = parse_demo_col(c)
            if parsed and c not in (id_col, t_col):
                colmap[c] = parsed
        if not colmap:
            raise ValueError(f"성별·연령 인구 컬럼을 인식하지 못했습니다. 실제 컬럼: {cols}")
        out = raw[[id_col, t_col] + list(colmap)].copy()
        out = out.rename(columns={id_col: "grid_id", t_col: "hour"})
        for c in colmap:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0)

    out["grid_id"] = clean_id(out["grid_id"])
    out["hour"] = pd.to_numeric(out["hour"].astype(str).str.extract(r"(\d+)")[0],
                                errors="coerce")
    out = out.dropna(subset=["hour"])
    out["hour"] = out["hour"].astype(int)
    return out, colmap


@st.cache_data(show_spinner="격자 좌표 계산 중...")
def load_grid():
    shp_files = glob.glob(os.path.join(BASE_DIR, "*.shp"))
    if not shp_files:
        raise FileNotFoundError("Shapefile(.shp)을 찾지 못했습니다.")
    gdf = gpd.read_file(shp_files[0])
    # 중심점은 투영좌표계(m 단위)에서 계산해야 정확함
    gdf = gdf.to_crs(epsg=5179)
    cent = gdf.geometry.centroid
    cent_wgs = gpd.GeoSeries(cent, crs=5179).to_crs(epsg=4326)
    attrs = pd.DataFrame(gdf.drop(columns="geometry"))
    attrs["lon"] = cent_wgs.x.values
    attrs["lat"] = cent_wgs.y.values
    return attrs


def pick_grid_id_col(grid_attrs, data_ids):
    """데이터의 격자ID와 가장 많이 겹치는 shapefile 컬럼을 자동 선택"""
    best, best_n = None, 0
    ids = set(data_ids)
    for c in grid_attrs.columns:
        if c in ("lon", "lat"):
            continue
        n = len(ids & set(clean_id(grid_attrs[c])))
        if n > best_n:
            best, best_n = c, n
    return best, best_n


# ==========================================
# 3. 사이드바
# ==========================================
st.sidebar.title("📌 분석 설정")

files = list_data_files()
if not files:
    st.title("🗺️ 서울시 250m 격자 타겟 생활인구 지도")
    st.error("날짜(YYYYMMDD)가 포함된 CSV 파일을 저장소에서 찾지 못했습니다.")
    st.stop()

st.sidebar.success(f"💾 일별 CSV {len(files)}개 인식 ({min(files)} ~ {max(files)})")

selected_date = st.sidebar.selectbox("분석 일자", list(files.keys()),
                                     format_func=lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}")

try:
    day_df, colmap = load_day(files[selected_date])
except Exception as e:
    st.title("🗺️ 서울시 250m 격자 타겟 생활인구 지도")
    st.error(f"CSV를 읽는 중 오류가 발생했습니다: {e}")
    st.stop()

hours_avail = sorted(day_df["hour"].unique())
h_min, h_max = int(min(hours_avail)), int(max(hours_avail))
time_range = st.sidebar.slider("시간대 범위 (시)", h_min, h_max, (max(8, h_min), h_max))

selected_gender = st.sidebar.selectbox("성별", ["전체", "여성", "남성"])

age_options = sorted({a for _, a in colmap.values()}, key=age_sort_key)
default_ages = [a for a in DEFAULT_AGES if a in age_options] or age_options
selected_ages = st.sidebar.multiselect("연령대", age_options, default=default_ages)

agg_mode = st.sidebar.radio("집계 방식", ["시간대 평균 인구", "시간대 합계 (연인원)"],
                            help="평균: 선택 시간대의 시간당 평균 인구 / 합계: 시간대별 인구를 모두 더한 값")

st.sidebar.divider()
st.sidebar.markdown("### 🗺️ 지도 옵션")
viz_type = st.sidebar.radio("표시 방식", ["2D 도트 (Scatter)", "3D 기둥 (Column)", "2D 열지도 (Heatmap)"])
dot_size = st.sidebar.slider("도트 크기 / 열지도 반경", 1, 10, 3)

theme_map = {
    "기본 (Base)": "Base",
    "야간 (Midnight)": "midnight",
    "위성 (Satellite)": "Satellite",
    "백지도 (White)": "white",
}
theme_label = st.sidebar.selectbox("배경 테마", list(theme_map))
theme = theme_map[theme_label]

# API 키: Streamlit secrets > 환경변수 > 직접 입력
api_key = ""
try:
    api_key = st.secrets.get("VWORLD_API_KEY", "")
except Exception:
    pass
api_key = api_key or os.environ.get("VWORLD_API_KEY", "")
if not api_key:
    api_key = st.sidebar.text_input("브이월드 API 키", type="password")

# ==========================================
# 4. 집계
# ==========================================
st.title("🗺️ 서울시 250m 격자 타겟 생활인구 지도")
st.caption("일별 CSV의 성별·연령·시간대 조건으로 타겟 인구를 집계해 브이월드 배경지도 위에 표시합니다.")

if not selected_ages:
    st.warning("연령대를 1개 이상 선택하세요.")
    st.stop()

target_cols = [c for c, (g, a) in colmap.items()
               if (selected_gender == "전체" or g == selected_gender) and a in selected_ages]
if not target_cols:
    st.warning("선택한 성별·연령대에 해당하는 컬럼이 없습니다.")
    st.stop()

sub = day_df[(day_df["hour"] >= time_range[0]) & (day_df["hour"] <= time_range[1])]
sub = sub.assign(pop=sub[target_cols].sum(axis=1))
n_hours = max(sub["hour"].nunique(), 1)
df_pop = sub.groupby("grid_id", as_index=False)["pop"].sum()
if agg_mode.startswith("시간대 평균"):
    df_pop["pop"] = df_pop["pop"] / n_hours
df_pop = df_pop.rename(columns={"pop": "target_pop"})

try:
    grid_attrs = load_grid()
except Exception as e:
    st.error(f"격자 Shapefile 처리 오류: {e}")
    st.stop()

id_col, overlap = pick_grid_id_col(grid_attrs, df_pop["grid_id"])
if id_col is None:
    st.error("CSV의 격자ID와 일치하는 Shapefile 컬럼이 없습니다. 아래 정보를 확인하세요.")
    st.write("CSV 격자ID 예시:", df_pop["grid_id"].head(5).tolist())
    st.write("Shapefile 컬럼:", [c for c in grid_attrs.columns if c not in ("lon", "lat")])
    st.stop()

coords = pd.DataFrame({
    "grid_id": clean_id(grid_attrs[id_col]),
    "lon": grid_attrs["lon"],
    "lat": grid_attrs["lat"],
}).drop_duplicates("grid_id")

merged = df_pop.merge(coords, on="grid_id", how="inner")
if merged.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다.")
    st.stop()

# 5단계 등급 (동률이 있어도 오류 없도록 rank 기반)
merged["level"] = pd.qcut(merged["target_pop"].rank(method="first"), 5, labels=False) + 1
merged["color"] = merged["level"].map(LEVEL_COLORS)
merged["pop_label"] = merged["target_pop"].round(0).astype(int)

with st.expander("🔎 데이터 점검 정보"):
    st.write(f"CSV 행 수(해당 일자): {len(day_df):,} / 시간대: {h_min}~{h_max}시")
    st.write(f"인식된 성별·연령 컬럼 수: {len(colmap)} / 선택 조건에 쓰인 컬럼 수: {len(target_cols)}")
    st.write(f"Shapefile 격자ID 컬럼: `{id_col}` (일치 {overlap:,}개 / 데이터 격자 {len(df_pop):,}개 / 좌표 격자 {len(coords):,}개)")

c1, c2, c3 = st.columns(3)
unit = "시간당 평균" if agg_mode.startswith("시간대 평균") else "시간대 합계"
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
# 5. 지도
# ==========================================
layers = []
if api_key:
    ext = "jpeg" if theme == "Satellite" else "png"
    tile_url = f"https://api.vworld.kr/req/wmts/1.0.0/{api_key}/{theme}/{{z}}/{{y}}/{{x}}.{ext}"
    layers.append(pdk.Layer("TileLayer", data=tile_url, min_zoom=6, max_zoom=19, tile_size=256))
else:
    st.info("브이월드 API 키가 없어 배경지도 없이 표시합니다.")

if "Scatter" in viz_type:
    layers.append(pdk.Layer(
        "ScatterplotLayer", merged,
        get_position=["lon", "lat"], get_fill_color="color",
        get_radius=dot_size, radius_units="pixels",
        radius_min_pixels=1, pickable=True,
    ))
elif "Column" in viz_type:
    max_pop = merged["target_pop"].max() or 1
    merged["elevation"] = merged["target_pop"] / max_pop * 3000
    layers.append(pdk.Layer(
        "ColumnLayer", merged,
        get_position=["lon", "lat"], get_elevation="elevation",
        get_fill_color="color", radius=110, extruded=True, pickable=True,
    ))
else:
    layers.append(pdk.Layer(
        "HeatmapLayer", merged,
        get_position=["lon", "lat"], get_weight="target_pop",
        radius_pixels=dot_size * 10,
    ))

view_state = pdk.ViewState(
    longitude=126.9780, latitude=37.5665, zoom=10.5,
    pitch=45 if "Column" in viz_type else 0, bearing=0,
)

deck = pdk.Deck(
    layers=layers,
    initial_view_state=view_state,
    map_provider=None,
    map_style=None,
    tooltip={"text": "격자ID: {grid_id}\n타겟 인구: {pop_label}명"},
)
st.pydeck_chart(deck)

# ==========================================
# 6. 상위 20개 격자
# ==========================================
st.markdown("### 📊 타겟 인구 상위 20개 격자")
top20 = merged.sort_values("target_pop", ascending=False).head(20)
st.dataframe(
    top20[["grid_id", "pop_label", "lon", "lat"]].rename(
        columns={"grid_id": "격자 ID", "pop_label": "타겟 인구(명)", "lon": "경도", "lat": "위도"}
    ),
    hide_index=True,
)
