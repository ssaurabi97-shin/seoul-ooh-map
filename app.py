import streamlit as st
import pandas as pd
import sqlite3
import pydeck as pdk
import re
import os
import tempfile
import zipfile
import shapefile
from pyproj import Transformer

st.set_page_config(page_title="서울시 OOH 타겟 생활인구 지도", layout="wide")

st.title("🗺️ 서울시 250m 격자 타겟 생활인구 3D 지도 분석")
st.write("2024년 이후 일별 CSV 데이터를 DB에 누적 저장하고, 타겟 조건별 유동인구 밀집도를 정밀 시각화합니다.")

DB_PATH = "seoul_population.db"
COORDS_PATH = "grid_coords.csv"

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def ensure_index():
    """DB에 인덱스를 생성하여 날짜/시간 조회 속도 최적화"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_date_time ON raw_population (일자, 시간)")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

# ===================================================================
# --- 데이터 및 좌표 상태 확인 함수 ---
# ===================================================================
def get_db_status():
    if not os.path.exists(DB_PATH):
        return False, 0, []
    try:
        conn = get_db_connection()
        count_df = pd.read_sql("SELECT COUNT(*) as cnt FROM raw_population", conn)
        dates_df = pd.read_sql("SELECT DISTINCT 일자 FROM raw_population ORDER BY 일자", conn)
        conn.close()
        total_cnt = count_df['cnt'].iloc[0]
        date_list = dates_df['일자'].astype(str).tolist()
        return True, total_cnt, date_list
    except Exception:
        return False, 0, []

def get_coords_status():
    if os.path.exists(COORDS_PATH):
        try:
            df = pd.read_csv(COORDS_PATH)
            return True, len(df)
        except Exception:
            return False, 0
    return False, 0

# ===================================================================
# --- 사이드바: 1. 데이터 현황 및 누적 관리 ---
# ===================================================================
st.sidebar.header("📂 1. 저장된 데이터 현황")

has_db, db_row_count, available_dates = get_db_status()
has_coords, coords_count = get_coords_status()

# 현재 상태 요약 표시
if has_db and db_row_count > 0:
    st.sidebar.success(f"💾 **인구 DB:** 총 {db_row_count:,}행 ({len(available_dates)}개 일자 누적됨)")
else:
    st.sidebar.warning("💾 **인구 DB:** 데이터 없음 (CSV 업로드 필요)")

if has_coords and coords_count > 0:
    st.sidebar.success(f"📐 **격자 좌표:** {coords_count:,}개 격자 좌표 준비 완료")
else:
    st.sidebar.warning("📐 **격자 좌표:** 데이터 없음 (Shapefile 업로드 필요)")

# 접이식 신규 파일 업로드 섹션 (매번 업로드할 필요 없도록 접어둠)
with st.sidebar.expander("➕ 신규 데이터 추가 / 좌표 업데이트"):
    st.markdown("#### 📄 일별 생활인구 CSV 추가")
    uploaded_files = st.file_uploader(
        "추가할 CSV 파일들을 올려주세요", 
        type=["csv"], 
        accept_multiple_files=True,
        key="csv_uploader"
    )

    if uploaded_files:
        if st.button("💾 DB에 추가 누적 저장하기", use_container_width=True):
            conn = get_db_connection()
            total_rows = 0
            
            with st.spinner("데이터베이스에 추가 저장 중입니다..."):
                for uploaded_file in uploaded_files:
                    try:
                        df = pd.read_csv(uploaded_file, encoding='euc-kr')
                    except Exception:
                        df = pd.read_csv(uploaded_file, encoding='utf-8')
                    
                    df.to_sql('raw_population', conn, if_exists='append', index=False)
                    total_rows += len(df)
                    
            conn.close()
            ensure_index()
            st.success(f"총 {len(uploaded_files)}개 파일 ({total_rows:,}행) DB 추가 저장 완료!")
            st.rerun()

    st.markdown("---")
    st.markdown("#### 📐 격자 Shapefile (.zip) 업로드")
    shapefile_zip = st.file_uploader(
        "서울시 격자 Shapefile 패키지 (.zip)", 
        type=["zip"],
        key="zip_uploader"
    )

    if shapefile_zip:
        if st.button("⚙️ 위경도 좌표 재생성하기", use_container_width=True):
            with st.spinner("Shapefile 파싱 및 위경도 좌표 변환 중..."):
                try:
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        zip_path = os.path.join(tmp_dir, "grid_upload.zip")
                        with open(zip_path, "wb") as f:
                            f.write(shapefile_zip.getbuffer())
                        
                        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                            zip_ref.extractall(tmp_dir)
                        
                        shp_path = None
                        prj_path = None
                        for root, dirs, files in os.walk(tmp_dir):
                            for file in files:
                                if file.endswith('.shp'):
                                    shp_path = os.path.join(root, file)
                                elif file.endswith('.prj'):
                                    prj_path = os.path.join(root, file)
                        
                        if not shp_path:
                            st.error("⚠️️ .zip 파일 내에서 .shp 파일을 찾을 수 없습니다.")
                        else:
                            src_crs = "EPSG:5181"
                            if prj_path:
                                try:
                                    with open(prj_path, 'r', encoding='utf-8', errors='ignore') as pf:
                                        prj_txt = pf.read()
                                        if "5179" in prj_txt or "UTM-K" in prj_txt:
                                            src_crs = "EPSG:5179"
                                        elif "5181" in prj_txt or "Central Belt" in prj_txt:
                                            src_crs = "EPSG:5181"
                                        elif "5186" in prj_txt:
                                            src_crs = "EPSG:5186"
                                except Exception:
                                    pass
                            
                            transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
                            
                            sf = shapefile.Reader(shp_path)
                            fields = [f[0] for f in sf.fields[1:]]
                            grid_col_idx = next((i for i, c in enumerate(fields) if '격자' in c or 'GRID' in c.upper() or 'ID' in c.upper()), 0)
                            
                            grid_records = []
                            for shape_rec in sf.shapeRecords():
                                grid_id = shape_rec.record[grid_col_idx]
                                bbox = shape_rec.shape.bbox
                                center_x = (bbox[0] + bbox[2]) / 2.0
                                center_y = (bbox[1] + bbox[3]) / 2.0
                                lon, lat = transformer.transform(center_x, center_y)
                                grid_records.append({
                                    '250M격자': str(grid_id),
                                    'lat': lat,
                                    'lon': lon
                                })
                            
                            coords_df = pd.DataFrame(grid_records).drop_duplicates()
                            coords_df.to_csv(COORDS_PATH, index=False, encoding='utf-8-sig')
                            st.success(f"✅ 총 {len(coords_df):,}개 격자의 좌표 추출 완료!")
                            st.rerun()
                            
                except Exception as e:
                    st.error(f"❌ 파일 처리 중 오류 발생: {e}")

# 데이터 초기화 옵션
with st.sidebar.expander("🛠️ 데이터 초기화 / 관리"):
    st.caption("저장된 DB 및 좌표 파일을 초기화하고 처음 상태로 되돌립니다.")
    if st.button("🗑️ 전체 저장 데이터 삭제", use_container_width=True):
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        if os.path.exists(COORDS_PATH):
            os.remove(COORDS_PATH)
        st.success("저장된 데이터가 삭제되었습니다.")
        st.rerun()

# ===================================================================
# --- DB 데이터 메타데이터 읽기 ---
# ===================================================================
all_cols = []

if has_db:
    try:
        conn = get_db_connection()
        sample_df = pd.read_sql("SELECT * FROM raw_population LIMIT 1", conn)
        all_cols = sample_df.columns.tolist()
        conn.close()
    except Exception:
        pass

# 성별/연령대 컬럼 파싱
extracted_ages = []
for col in all_cols:
    if any(keyword in col for keyword in ['남자', '여자', '남성', '여성']):
        cleaned = re.sub(r'남자|여자|남성|여성', '', col).strip()
        if cleaned and cleaned not in extracted_ages:
            extracted_ages.append(cleaned)

default_ages = [a for a in extracted_ages if any(age in a for age in ['20~24', '25~29', '30~34', '35~39'])]

# ===================================================================
# --- 사이드바: 2. 분석 조건 설정 폼 ---
# ===================================================================
with st.sidebar.form(key="filter_form"):
    st.header("🎯 2. 분석 조건 설정")
    
    if available_dates:
        selected_dates = st.multiselect("분석 일자 선택", options=available_dates, default=available_dates[-1:])
    else:
        selected_dates = []
        st.info("먼저 데이터를 업로드해 주세요.")
        
    start_hour, end_hour = st.slider("시간대 범위 (시)", 0, 23, (8, 23))
    selected_genders = st.multiselect("성별 선택", ["남성", "여성"], default=["여성"])
    selected_ages = st.multiselect("연령대 선택", options=extracted_ages, default=default_ages if default_ages else extracted_ages[:4])
    map_type = st.radio("지도 시각화 방식", ["3D 기둥 (Column)", "2D 히트맵 (Heatmap)"])
    
    submit_button = st.form_submit_button(label="🔍 데이터 분석 & 지도 생성", use_container_width=True)

# ===================================================================
# --- 지도 시각화 실행 ---
# ===================================================================
if submit_button:
    if not selected_dates:
        st.warning("⚠️ 최소 하나 이상의 일자를 선택해 주세요.")
    else:
        gender_keywords = []
        if "남성" in selected_genders:
            gender_keywords.extend(['남자', '남성'])
        if "여성" in selected_genders:
            gender_keywords.extend(['여자', '여성'])
            
        selected_cols = [
            col for col in all_cols
            if any(gk in col for gk in gender_keywords) and any(ak in col for ak in selected_ages)
        ]
                
        if not selected_cols:
            st.warning("⚠️ 선택한 조건에 해당하는 데이터 컬럼이 없습니다.")
        else:
            with st.spinner("데이터 조회 및 시각화 준비 중..."):
                conn = get_db_connection()
                date_str = "', '".join([str(d) for d in selected_dates])
                cols_sql = ", ".join([f"`{c}`" for c in selected_cols])
                
                query = f"""
                    SELECT `250M격자`, {cols_sql}
                    FROM raw_population
                    WHERE 일자 IN ('{date_str}')
                      AND 시간 >= {start_hour} AND 시간 <= {end_hour}
                """
                
                df_res = pd.read_sql(query, conn)
                conn.close()
                
                for c in selected_cols:
                    df_res[c] = pd.to_numeric(df_res[c].replace('*', 0), errors='coerce').fillna(0)
                
                df_res['target_sum'] = df_res[selected_cols].sum(axis=1)
                grid_summary = df_res.groupby('250M격자')['target_sum'].mean().reset_index()
                grid_summary['250M격자'] = grid_summary['250M격자'].astype(str)
                
                # 좌표 데이터 매핑
                if os.path.exists(COORDS_PATH):
                    coords_df = pd.read_csv(COORDS_PATH)
                    coords_df['250M격자'] = coords_df['250M격자'].astype(str)
                    map_df = pd.merge(grid_summary, coords_df, on="250M격자", how="inner")
                else:
                    map_df = grid_summary.copy()
                    map_df['lat'] = 37.5665
                    map_df['lon'] = 126.9780
                    st.warning("⚠️️ 격자 좌표 파일이 없습니다. 사이드바의 '신규 데이터 추가' 메뉴에서 Shapefile(.zip)을 올려 좌표를 생성해 주세요.")

                if map_df.empty:
                    st.error("⚠️ 인구 데이터와 좌표 데이터 간 일치하는 격자 ID가 없습니다.")
                else:
                    # 동적 색상 매핑
                    max_val = map_df['target_sum'].max() if map_df['target_sum'].max() > 0 else 1
                    map_df['norm'] = map_df['target_sum'] / max_val
                    
                    map_df['r'] = (255 * map_df['norm']).astype(int)
                    map_df['g'] = (255 * (1 - map_df['norm'] * 0.8)).astype(int)
                    map_df['b'] = 50
                    map_df['a'] = 200
                    map_df['color'] = map_df.apply(lambda row: [row['r'], row['g'], row['b'], row['a']], axis=1)

                    # 지도 뷰 설정
                    mid_lat = map_df['lat'].mean()
                    mid_lon = map_df['lon'].mean()
                    
                    view_state = pdk.ViewState(
                        latitude=mid_lat,
                        longitude=mid_lon,
                        zoom=11,
                        pitch=45 if map_type.startswith("3D") else 0
                    )
                    
                    if map_type.startswith("3D"):
                        layer = pdk.Layer(
                            "ColumnLayer",
                            data=map_df,
                            get_position=["lon", "lat"],
                            get_elevation="target_sum",
                            elevation_scale=0.3,
                            radius=80,
                            get_fill_color="color",
                            pickable=True,
                            auto_highlight=True
                        )
                    else:
                        layer = pdk.Layer(
                            "HeatmapLayer",
                            data=map_df,
                            get_position=["lon", "lat"],
                            get_weight="target_sum",
                            radius_pixels=25
                        )
                    
                    deck = pdk.Deck(
                        layers=[layer],
                        initial_view_state=view_state,
                        tooltip={"html": "<b>격자 ID:</b> {250M격자}<br/><b>평균 타겟 인구:</b> {target_sum:.1f}명"}
                    )
                    
                    st.subheader("🗺️ 서울시 250m 격자 타겟 생활인구 지도")
                    st.pydeck_chart(deck)
                    
                    st.subheader("📊 타겟 유동인구 상위 20개 격자")
                    st.dataframe(grid_summary.sort_values(by='target_sum', ascending=False).head(20), use_container_width=True)
