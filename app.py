# 1-2. 격자 Shapefile (.zip) 동적 업로드 & 좌표 추출
st.sidebar.markdown("---")
st.sidebar.subheader("📐 격자 공간 데이터 (Shapefile)")
shapefile_zip = st.sidebar.file_uploader(
    "서울시 격자 Shapefile 패키지 (.zip) 업로드", 
    type=["zip"]
)

if shapefile_zip:
    if st.sidebar.button("⚙️ 공간 데이터에서 위경도 좌표 추출하기", use_container_width=True):
        with st.spinner("Shapefile 파싱 및 위경도 좌표 변환 중..."):
            try:
                import zipfile
                
                with tempfile.TemporaryDirectory() as tmp_dir:
                    zip_path = os.path.join(tmp_dir, "grid_upload.zip")
                    with open(zip_path, "wb") as f:
                        f.write(shapefile_zip.getbuffer())
                    
                    # 1. zip 압축 해제
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall(tmp_dir)
                    
                    # 2. 압축 내부 하위 폴더까지 탐색하여 .shp 파일 찾기
                    shp_files = [
                        os.path.join(root, file)
                        for root, dirs, files in os.walk(tmp_dir)
                        for file in files if file.endswith('.shp')
                    ]
                    
                    if not shp_files:
                        st.sidebar.error("⚠️ .zip 파일 내에서 .shp 파일을 찾을 수 없습니다.")
                    else:
                        shp_path = shp_files[0]
                        gdf = gpd.read_file(shp_path)
                        
                        # 3. 좌표계(CRS) 검증 및 WGS84(EPSG:4326) 변환
                        if gdf.crs is None:
                            # 좌표계 정보가 없는 경우 한국 기본 좌표계(EPSG:5181) 지정
                            gdf.set_crs(epsg=5181, inplace=True)
                            
                        gdf_4326 = gdf.to_crs(epsg=4326)
                        
                        # 4. 격자 중심점(Centroid) 계산하여 위도/경도 추출
                        gdf_4326['lon'] = gdf_4326.geometry.centroid.x
                        gdf_4326['lat'] = gdf_4326.geometry.centroid.y
                        
                        # 5. 격자 ID 컬럼 탐지
                        grid_col = next((c for c in gdf_4326.columns if '격자' in c or 'GRID' in c.upper() or 'ID' in c.upper()), None)
                        
                        if grid_col:
                            gdf_4326 = gdf_4326.rename(columns={grid_col: '250M격자'})
                            coords_df = gdf_4326[['250M격자', 'lat', 'lon']].drop_duplicates()
                            coords_df.to_csv("grid_coords.csv", index=False, encoding='utf-8-sig')
                            st.sidebar.success(f"✅ 총 {len(coords_df):,}개 격자의 좌표(`grid_coords.csv`) 추출 완료!")
                        else:
                            st.sidebar.error(f"⚠️ 격자 ID 컬럼을 찾을 수 없습니다. (발견된 컬럼: {list(gdf_4326.columns)})")
                            
            except Exception as e:
                st.sidebar.error(f"❌ 파일 처리 중 오류 발생: {e}")
