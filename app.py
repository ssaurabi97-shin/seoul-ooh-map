import streamlit as st
import pandas as pd
import pydeck as pdk
import re

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
    
    # 데이터 컬럼에서 성별 키워드를 제거하고 순수 연령대 목록만 자동 추출
    all_cols = df.columns.tolist()
    extracted_ages = []
    for col in all_cols:
        if any(keyword in col for keyword in ['남자', '여자', '남성', '여성']):
            cleaned = re.sub(r'남자|여자|남성|여성', '', col).strip()
            if cleaned and cleaned not in extracted_ages:
                extracted_ages.append(cleaned)
    
    # 기본 선택값 (20~39세)
    default_ages = [a for a in extracted_ages if any(age in a for age in ['20~24', '25~29', '30~34', '35~39'])]
    
    # [개선] 사이드바 폼(Form) 생성 - 버튼을 누를 때만 데이터 분석 실행
    with st.sidebar.form(key="filter_form"):
        st.header("🎯 타겟 조건 설정")
        
        # 1. 시간대 선택
        start_hour, end_hour = st.slider("시간대 범위 (시)", 0, 23, (8, 23))
        
        # 2. 성별 선택
        st.subheader("1. 성별 선택")
        selected_genders = st.multiselect(
            "분석할 성별을 선택하세요",
            ["남성", "여성"],
            default=["여성"]
        )
        
        # 3. 세부 연령대 선택
        st.subheader("2. 세부 연령대 선택")
        selected_ages = st.multiselect(
            "분석할 연령대를 선택하세요", 
            options=extracted_ages,
            default=default_ages if default_ages else extracted_ages[:4]
        )
        
        # 데이터 분석 실행 버튼
        submit_button = st.form_submit_button(label="🔍 데이터 분석 시작", use_container_width=True)
    
    # 버튼이 클릭되었을 때만 데이터 분석 및 결과 출력 수행
    if submit_button:
        # 선택된 성별 + 연령대에 매칭되는 실제 데이터 컬럼 추출
        gender_keywords = []
        if "남성" in selected_genders:
            gender_keywords.extend(['남자', '남성'])
        if "여성" in selected_genders:
            gender_keywords.extend(['여자', '여성'])
            
        selected_cols = []
        for col in all_cols:
            has_gender = any(gk in col for gk in gender_keywords)
            has_age = any(ak in col for ak in selected_ages)
            if has_gender and has_age:
                selected_cols.append(col)
        
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
            st.caption(f"✓ 분석 대상 컬럼 ({len(selected_cols)}개): {', '.join(selected_cols)}")
            st.dataframe(grid_summary.sort_values(by='target_sum', ascending=False).head(20))
            
            st.info("💡 조건을 변경한 후 **'🔍 데이터 분석 시작'** 버튼을 누르면 새 결과로 재계산됩니다.")
        else:
            st.warning("⚠️ 최소 하나 이상의 성별과 연령대를 각각 선택한 후 분석 버튼을 눌러주세요.")
    else:
        st.info("👈 사이드바에서 원하는 조건(시간대, 성별, 연령대)을 모두 설정한 후 **'🔍 데이터 분석 시작'** 버튼을 클릭해 주세요.")
