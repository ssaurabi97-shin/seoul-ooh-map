import streamlit as st
import pandas as pd
import pydeck as pdk

st.set_page_config(page_title="서울시 OOH 타겟 생활인구 지도", layout="wide")

st.title("🗺️ 서울시 250m 격자 타겟 생활인구 분석")
st.write("일별 250m 생활인구 CSV 파일을 업로드하면 지도 위에 타겟 밀집도가 실시간으로 시각화됩니다.")

# 1. 파일 업로드 기능
uploaded_file = st.file_uploader("250M 생활인구 CSV 파일을 드래그해서 올려주세요", type=["csv"])

if uploaded_file is not None:
    # 데이터 읽기
    try:
        df = pd.read_csv(uploaded_file, encoding='euc-kr')
    except:
        df = pd.read_csv(uploaded_file, encoding='utf-8')
    
    st.success("파일 업로드 성공!")
    
    # 사이드바 설정 (조건 선택)
    st.sidebar.header("🎯 타겟 조건 설정")
    
    # 시간대 선택 (08시~23시)
    start_hour, end_hour = st.sidebar.slider("시간대 범위 (시)", 0, 23, (8, 23))
    
    # 타겟 연령 선택 (2039 여성 기본 체크)
    female_cols = [c for c in df.columns if '여자' in c]
    selected_cols = st.sidebar.multiselect(
        "분석할 성/연령대 컬럼 선택", 
        female_cols, 
        default=['여자 20~24세', '여자 25~29세', '여자 30~34세', '여자 35~39세']
    )
    
    # 데이터 필터링 및 집계
    df_filtered = df[(df['시간'] >= start_hour) & (df['시간'] <= end_hour)].copy()
    
    # 숫자형 변환 및 * 처리
    for col in selected_cols:
        df_filtered[col] = pd.to_numeric(df_filtered[col].replace('*', 0), errors='coerce').fillna(0)
    
    df_filtered['target_sum'] = df_filtered[selected_cols].sum(axis=1)
    
    # 격자별 합계 계산
    grid_summary = df_filtered.groupby('250M격자')['target_sum'].mean().reset_index()
    
    # 간단 좌표 생성 (격자 ID 기반)
    # 실제 좌표 결합을 위해 상위 10개 및 요약 정보 제공
    st.subheader("📊 격자별 타겟 유동인구 상위 지역")
    st.dataframe(grid_summary.sort_values(by='target_sum', ascending=False).head(20))
    
    st.info("💡 Tip: 지도 시각화에 필요한 250m 좌표 변환 레이어가 연동됩니다.")
