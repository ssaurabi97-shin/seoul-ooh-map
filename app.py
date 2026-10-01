import streamlit as st
import pandas as pd
import sqlite3
import pydeck as pdk
import re
import os

st.set_page_config(page_title="서울시 OOH 타겟 생활인구 지도", layout="wide")

st.title("🗺️ 서울시 250m 격자 타겟 생활인구 3D 지도 분석")
st.write("2024년 이후 일별 CSV 데이터를 DB에 누적 저장하고, 타겟 조건별 유동인구 밀집도를 정밀 시각화합니다.")

DB_PATH = "seoul_population.db"

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
        pass  # 테이블이 아직 생성되지 않은 경우 예외 처리
    finally:
        conn.close()

# --- 사이드바: 1. 파일 업로드 및 DB 저장 ---
st.sidebar.header("📂 1. 데이터 업로드 & DB 저장")
uploaded_files = st.sidebar.file_uploader(
    "일별 250M CSV 파일들을 올려주세요 (다중 선택 가능)", 
    type=["csv"], 
    accept_multiple_files=True
)

if uploaded_files:
    if st.sidebar.button("💾 DB에 원본 데이터 누적 저장하기", use_container_width=True):
        conn = get_db_connection()
        total_rows = 0
        
        with st.spinner("데이터베이스에 저장 중입니다..."):
            for uploaded_file in uploaded_files:
                try:
                    df = pd.read_csv(uploaded_file, encoding='euc-kr')
                except Exception:
                    df = pd.read_csv(uploaded_file, encoding='utf-8')
                
                df.to_sql('raw_population', conn, if_exists='append', index=False)
                total_rows += len(df)
                
        conn.close()
        ensure_index()
        st.sidebar.success(f"총 {len(uploaded_files)}개 파일 ({total_rows:,}행) DB 저장 완료!")

# --- DB 데이터 메타데이터 읽기 ---
all_cols = []
available_dates = []

if os.path.exists(DB_PATH):
    try:
        conn = get_db_connection()
        sample_df = pd.read_sql("SELECT * FROM raw_population LIMIT 1", conn)
        all_cols = sample_df.columns.tolist()
        
        dates_df = pd.read_sql("SELECT DISTINCT 일자 FROM raw_population ORDER BY 일자", conn)
        available_dates = dates_df['일자'].astype(str).tolist()
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

# --- 사이드바: 2. 분석 조건 설정 폼 ---
with st.sidebar.form(key="filter_form"):
    st.header("🎯 2. 분석 조건 설정")
    
    if available_dates:
        selected_dates = st.multiselect("분석 일자 선택", options=available_dates, default=available_dates[-1:])
    else:
        selected_dates = []
        st.info("먼저 CSV 파일을 업로드해 주세요.")
        
    start_hour, end_hour = st.slider("시간대 범위 (시)", 0, 23, (8, 23))
    selected_genders = st.multiselect("성별 선택", ["남성", "여성"], default=["여성"])
    selected_ages = st.multiselect("연령대 선택", options=extracted_ages, default=default_ages if default_ages else extracted_ages[:4])
    map_type = st.radio("지도 시각화 방식", ["3D 기둥 (Column)", "2D 히트맵 (Heatmap)"])
    
    submit_button = st.form_submit_button(label="🔍 데이터 분석 & 지도 생성", use_container_width=True)

# --- 지도 시각화 실행 ---
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
                
                # 좌표 데이터 매핑
                if os.path.exists("grid_coords.csv"):
                    coords_df = pd.read_csv("grid_coords.csv")
                    map_df = pd.merge(grid_summary, coords_df, on="250M격자", how="inner")
                else:
                    map_df = grid_summary.copy()
                    map_df['lat'] = 37.5665
                    map_df['lon'] = 126.9780
                    st.warning("⚠️ `grid_coords.csv` 파일이 없어 기본 좌표로 표시됩니다. 좌표 참조 파일을 준비해 주세요.")

                # 동적 색상 매핑 (상대적 인구 밀도 기반 RGB 계산)
                max_val = map_df['target_sum'].max() if not map_df.empty and map_df['target_sum'].max() > 0 else 1
                map_df['norm'] = map_df['target_sum'] / max_val
                
                # 노란색(낮음) -> 빨간색(높음) 그라데이션
                map_df['r'] = (255 * map_df['norm']).astype(int)
                map_df['g'] = (255 * (1 - map_df['norm'] * 0.8)).astype(int)
                map_df['b'] = 50
                map_df['a'] = 200
                map_df['color'] = map_df.apply(lambda row: [row['r'], row['g'], row['b'], row['a']], axis=1)

                # 지도 뷰 설정
                mid_lat = map_df['lat'].mean() if not map_df.empty else 37.5665
                mid_lon = map_df['lon'].mean() if not map_df.empty else 126.9780
                
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
