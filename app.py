import os
import glob
import sqlite3
import pandas as pd
import geopandas as gpd
import streamlit as st
import pydeck as pdk

# ==========================================
# 0. 페이지 기본 설정 및 상수
# ==========================================
st.set_page_config(
    page_title="서울시 250m 격자 타겟 생활인구 3D 지도 분석",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded"
)

VWORLD_API_KEY = "7B6105E1-F578-4901-B9FF-555769B5D382"
DB_PATH = "seoul_population.db"
GRID_PATH = "grid_coords.csv"

# ==========================================
# 1. DB 및 격자 좌표 데이터 자동 구축/로드
# ==========================================
@st.cache_resource
def init_data_store():
    """CSV 데이터 및 Shapefile을 읽어 SQLite DB와 격자 좌표 CSV를 자동 생성"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 인구 DB 테이블 생성
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS population (
            stdr_de TEXT,
            tmzon INTEGER,
            grid_id TEXT,
            gender TEXT,
            age_grp TEXT,
            pop_cnt REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pop ON population(stdr_de, tmzon, gender, age_grp)")
    conn.commit()

    # 1-1. CSV 데이터 자동 임포트
    csv_files = glob.glob("*.csv")
    data_csvs = [f for f in csv_files if f != GRID_PATH]
    
    cursor.execute("SELECT DISTINCT stdr_de FROM population")
    existing_dates = set(row[0] for row in cursor.fetchall())
    
    for csv_file in data_csvs:
        try:
            df_temp = pd.read_csv(csv_file, nrows=5)
            if '기준일자' in df_temp.columns or 'STDR_DE' in df_temp.columns:
                df = pd.read_csv(csv_file)
                # 컬럼명 통일
                date_col = '기준일자' if '기준일자' in df.columns else 'STDR_DE'
                time_col = '시간대' if '시간대' in df.columns else 'TMZON'
                grid_col = '격자ID' if '격자ID' in df.columns else 'GRID_ID'
                gender_col = '성별' if '성별' in df.columns else 'GENDER'
                age_col = '연령대' if '연령대' in df.columns else 'AGE_GRP'
                pop_col = '인구수' if '인구수' in df.columns else 'POP_CNT'
                
                df_clean = df[[date_col, time_col, grid_col, gender_col, age_col, pop_col]].copy()
                df_clean.columns = ['stdr_de', 'tmzon', 'grid_id', 'gender', 'age_grp', 'pop_cnt']
                df_clean['stdr_de'] = df_clean['stdr_de'].astype(str)
                
                file_dates = set(df_clean['stdr_de'].unique())
                if not file_dates.issubset(existing_dates):
                    df_clean.to_sql('population', conn, if_exists='append', index=False)
                    existing_dates.update(file_dates)
        except Exception as e:
            pass

    # 1-2. Shapefile 기반 격자 중심점 좌표(WGS84) 자동 생성
    if not os.path.exists(GRID_PATH):
        shp_files = glob.glob("*.shp")
        if shp_files:
            try:
                gdf = gpd.read_file(shp_files[0])
                gdf_wgs84 = gdf.to_crs(epsg=4326)
                gdf_wgs84['lon'] = gdf_wgs84.geometry.centroid.x
                gdf_wgs84['lat'] = gdf_wgs84.geometry.centroid.y
                
                id_col = [c for c in gdf_wgs84.columns if 'id' in c.lower() or 'grid' in c.lower() or 'code' in c.lower()][0]
                gdf_wgs84[[id_col, 'lon', 'lat']].rename(columns={id_col: 'grid_id'}).to_csv(GRID_PATH, index=False)
            except Exception as e:
                pass
                
    conn.close()

init_data_store()

# ==========================================
# 2. 데이터 현황 조회 함수
# ==========================================
def get_db_status():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM population")
    total_rows = cursor.fetchone()[0]
    
    cursor.execute("SELECT DISTINCT stdr_de FROM population ORDER BY stdr_de")
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    grid_count = 0
    if os.path.exists(GRID_PATH):
        grid_df = pd.read_csv(GRID_PATH)
        grid_count = len(grid_df)
        
    return total_rows, dates, grid_count

total_rows, available_dates, grid_count = get_db_status()

# ==========================================
# 3. 사이드바 UI
# ==========================================
st.sidebar.title("📌 데이터 현황 및 분석 설정")

# 3-1. 저장된 데이터 현황
st.sidebar.markdown("### 📊 1. 저장된 데이터 현황")
if total_rows > 0:
    st.sidebar.success(f"💾 **인구 DB**: 총 {total_rows:,}행 ({len(available_dates)}개 일자 누적됨)")
else:
    st.sidebar.warning("💾 **인구 DB**: 저장된 데이터 없음")

if grid_count > 0:
    st.sidebar.success(f"📐 **격자 좌표**: {grid_count:,}개 격자 좌표 준비 완료")
else:
    st.sidebar.error("📐 **격자 좌표**: CSV/Shapefile 좌표 없음")

st.sidebar.divider()

# 3-2. 분석 조건 설정
st.sidebar.markdown("### 🎯 2. 분석 조건 설정")

selected_date = st.sidebar.selectbox("분석 일자 선택", available_dates if available_dates else ["데이터 없음"])
time_range = st.sidebar.slider("시간대 범위 (시)", 0, 23, (8, 23))

gender_options = ["전체", "여성", "남성"]
selected_gender = st.sidebar.selectbox("성별 선택", gender_options)

age_options = ["전체", "20-24세", "25-29세", "30-34세", "35-39세", "40-44세", "45-49세", "50-54세", "55-59세", "60-64세", "65-69세", "70세 이상"]
selected_ages = st.sidebar.multiselect("연령대 선택", age_options, default=["20-24세", "25-29세", "30-34세", "35-39세"])

st.sidebar.divider()

# 3-3. 브이월드 지도 시각화 옵션
st.sidebar.markdown("### 🗺️ 브이월드 지도 시각화 옵션")
viz_type = st.sidebar.radio("표시 방식", ["2D 도트 지도 (Scatter)", "3D 기둥 (Column)", "2D 번짐 열지도 (Heatmap)"])
dot_radius = st.sidebar.slider("도트 크기 / 반지름 (픽셀)", 1, 10, 3)

vworld_theme_map = {
    "브이월드 기본 지도 (Base)": "Base",
    "브이월드 야간 지도 (Midnight)": "midnight",
    "브이월드 위성 지도 (Satellite)": "Satellite",
    "브이월드 백지도 (White)": "white"
}
selected_theme_label = st.sidebar.selectbox("브이월드 배경 테마", list(vworld_theme_map.keys()))
selected_theme = vworld_theme_map[selected_theme_label]

btn_analyze = st.sidebar.button("🔍 데이터 분석 & 지도 생성", use_container_width=True)

# ==========================================
# 4. 메인 화면 - 타겟 인구 집계 & 지도 표출
# ==========================================
st.title("🗺️ 서울시 250m 격자 타겟 생활인구 3D 지도 분석")
st.caption("2024년 이후 일별 CSV 데이터를 DB에 누적 저장하고, 브이월드(VWorld) 배경 지도에 타겟 유동인구 밀집도를 정밀 시각화합니다.")

if btn_analyze or True:
    if total_rows == 0 or not os.path.exists(GRID_PATH):
        st.info("데이터베이스 또는 격자 좌표 데이터가 준비되지 않았습니다.")
    else:
        conn = sqlite3.connect(DB_PATH)
        
        # SQL 쿼리 조건 구성
        query_conditions = ["stdr_de = ?", "tmzon BETWEEN ? AND ?"]
        params = [selected_date, time_range[0], time_range[1]]
        
        if selected_gender != "전체":
            query_conditions.append("gender = ?")
            params.append(selected_gender)
            
        if "전체" not in selected_ages and selected_ages:
            placeholders = ",".join(["?"] * len(selected_ages))
            query_conditions.append(f"age_grp IN ({placeholders})")
            params.extend(selected_ages)
            
        where_clause = " WHERE " + " AND ".join(query_conditions)
        
        query = f"""
            SELECT grid_id, SUM(pop_cnt) as target_pop
            FROM population
            {where_clause}
            GROUP BY grid_id
        """
        
        df_pop = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df_pop.empty:
            st.warning("선택한 조건에 해당하는 데이터가 없습니다.")
        else:
            # 좌표 데이터 결합
            grid_coords = pd.read_csv(GRID_PATH)
            merged_df = pd.merge(df_pop, grid_coords, on='grid_id', how='inner')
            
            # 인구수에 따른 분위수(1~5단계) 및 색상 매핑
            merged_df['quantile'] = pd.qcut(merged_df['target_pop'], q=5, labels=[1, 2, 3, 4, 5], duplicates='drop')
            
            color_map = {
                1: [68, 1, 84, 160],     # 1단계 (파란 계열)
                2: [59, 82, 139, 180],
                3: [33, 145, 140, 200],  # 3단계 (노랑 계열)
                4: [94, 201, 98, 220],   # 4단계 (주황 계열)
                5: [253, 231, 37, 240]   # 5단계 (빨강/핫스팟)
            }
            merged_df['color'] = merged_df['quantile'].map(color_map)

            # 지도 범례
            st.markdown("### 🗺️ 서울시 250m 격자 타겟 생활인구 지도 (브이월드 타일 적용)")
            st.markdown("""
                <div style="display: flex; gap: 10px; margin-bottom: 15px;">
                    <span style="background-color: #440154; color: white; padding: 4px 8px; border-radius: 4px;">🔵 1단계 (0-20%)</span>
                    <span style="background-color: #3B528B; color: white; padding: 4px 8px; border-radius: 4px;">🟢 2단계 (20-40%)</span>
                    <span style="background-color: #21918C; color: white; padding: 4px 8px; border-radius: 4px;">🟡 3단계 (40-60%)</span>
                    <span style="background-color: #5EC962; color: white; padding: 4px 8px; border-radius: 4px;">🟠 4단계 (60-80%)</span>
                    <span style="background-color: #FDE725; color: black; padding: 4px 8px; border-radius: 4px;">🔴 5단계 (핫스팟 80-100%)</span>
                </div>
            """, unsafe_allow_html=True)

            # ----------------------------------------------------
            # 핵심 핵심: 브이월드 WMTS 타일 레이어 생성
            # Python f-string 이스케이프: {{z}}/{{y}}/{{x}} 사용
            # ----------------------------------------------------
            vworld_tile_url = f"https://api.vworld.kr/req/wmts/1.0.0/{VWORLD_API_KEY}/{selected_theme}/{{z}}/{{y}}/{{x}}.png"

            vworld_layer = pdk.Layer(
                "TileLayer",
                data=vworld_tile_url,
                min_zoom=0,
                max_zoom=19,
                tile_size=256,
            )

            # 데이터 시각화 레이어 설정
            if "Scatter" in viz_type:
                data_layer = pdk.Layer(
                    "ScatterplotLayer",
                    merged_df,
                    get_position=["lon", "lat"],
                    get_color="color",
                    get_radius=dot_radius * 15,
                    pickable=True,
                    opacity=0.8,
                )
            elif "Column" in viz_type:
                max_pop = merged_df['target_pop'].max()
                merged_df['elevation'] = (merged_df['target_pop'] / max_pop) * 2000
                data_layer = pdk.Layer(
                    "ColumnLayer",
                    merged_df,
                    get_position=["lon", "lat"],
                    get_elevation="elevation",
                    elevation_scale=1,
                    radius=100,
                    get_fill_color="color",
                    pickable=True,
                    extruded=True,
                )
            else:  # Heatmap
                data_layer = pdk.Layer(
                    "HeatmapLayer",
                    merged_df,
                    get_position=["lon", "lat"],
                    get_weight="target_pop",
                    radius_pixels=dot_radius * 10,
                )

            # View State 설정 (서울 중심)
            view_state = pdk.ViewState(
                longitude=126.9780,
                latitude=37.5665,
                zoom=11,
                pitch=40 if "Column" in viz_type else 0,
                bearing=0
            )

            # ----------------------------------------------------
            # 핵심: map_style=None 지정하여 Mapbox 기본 배경을 제거하고 브이월드만 노출
            # ----------------------------------------------------
            r = pdk.Deck(
                layers=[vworld_layer, data_layer],
                initial_view_state=view_state,
                map_style=None,
                tooltip={"text": "격자ID: {grid_id}\n타겟 인구수: {target_pop}명"}
            )

            st.pydeck_chart(r, use_container_width=True)

            # 상위 20개 격자 데이터 출력
            st.markdown("### 📊 타겟 유동인구 상위 20개 격자")
            top20_df = merged_df.sort_values(by='target_pop', ascending=False).head(20)
            st.dataframe(
                top20_df[['grid_id', 'target_pop', 'lon', 'lat']].rename(
                    columns={'grid_id': '격자 ID', 'target_pop': '타겟 인구수(명)', 'lon': '경도', 'lat': '위도'}
                ),
                use_container_width=True
            )
