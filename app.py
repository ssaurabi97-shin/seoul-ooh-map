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
    
    # 1. 시간대 선택 (08시~23시)
    start_hour, end_hour = st.sidebar.slider("시간대 범위 (시)", 0, 23, (8, 23))
    
    # 2. 성별 선택 기능 추가 (남성, 여성)
    st.sidebar.subheader("1. 성별 선택")
    selected_genders = st.sidebar.multiselect(
        "분석할 성별을 선택하세요",
        ["남성", "여성"],
        default=["여성"]
    )
    
    # 선택된 성별 키워드 추출
    target_keywords = []
    if "남성" in selected_genders:
        target_keywords.extend(['남자', '남성'])
    if "여성" in selected_genders:
        target_keywords.extend(['여자', '여성'])
        
    # 성별 선택에 맞게 데이터 컬럼 자동 필터링
    available_cols = [
        col for col in df.columns 
        if any(keyword in col for keyword in target_keywords)
    ]
    
    # 기본 선택값 (2039 타겟) 자동 추출
    default_selected = [
        c for c in available_cols 
        if any(age in c for age in ['20~24세', '25~29세', '30~34세', '35~39세'])
    ]
    
    # 3. 세부 연령대 컬럼 선택
    st.sidebar.subheader("2. 세부 연령대 선택")
    selected_cols = st.sidebar.multiselect(
        "분석할 성/연령대 컬럼 선택", 
        options=available_cols,
        default=default_selected if default_selected else available_cols[:4]
    )
    
    # 데이터 필터링 및 집계
    df_filtered = df[(df['시간'] >= start_hour) & (df['시간'] <= end_hour)].copy()
    
    if selected_cols:
        # 숫자형 변환 및 결측치/특수문자(*) 처리
        for col in selected_cols:
            df_filtered[col] = pd.to_numeric(df_filtered[col].replace('*', 0), errors='coerce').fillna(0)
        
        # 선택한 성/연령대 합계 산출
        df_filtered['target_sum'] = df_filtered[selected_cols].sum(axis=1)
        
        # 격자별 시간대 평균 인구수 계산
        grid_summary = df_filtered.groupby('250M격자')['target_sum'].mean().reset_index()
        
        st.subheader("📊 격자별 타겟 유동인구 상위 지역")
        st.dataframe(grid_summary.sort_values(by='target_sum', ascending=False).head(20))
        
        st.info("💡 성별 선택(남성/여성) 변경 시 하단의 세부 연령대 선택 옵션이 즉시 업데이트됩니다.")
    else:
        st.warning("⚠️ 최소 하나 이상의 성별과 세부 연령대 컬럼을 선택해 주세요.")
