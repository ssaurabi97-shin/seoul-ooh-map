import glob
import os
import pandas as pd

print("--- Parquet 파일 변환 시작 ---")

# 저장될 폴더 생성
out_dir = os.path.join("data", "daily")
os.makedirs(out_dir, exist_ok=True)

# 현재 폴더의 CSV 파일 찾기 (grid_coords.csv 제외)
csv_files = [f for f in glob.glob("*.csv") if f != "grid_coords.csv"]

if not csv_files:
    print(
        "CSV 파일을 찾을 수 없습니다. D:\\heat_map 폴더 안에 CSV 파일이 있는지 확인해 주세요."
    )
else:
    for f in csv_files:
        filename = os.path.basename(f)
        # 파일명에서 8자리 날짜(예: 20260818) 추출
        digits = "".join([c for c in filename if c.isdigit()])
        if len(digits) >= 8:
            date_name = digits[-8:] + ".parquet"
        else:
            date_name = filename.replace(".csv", "") + ".parquet"

        out_path = os.path.join(out_dir, date_name)
        print(f"변환 중: {filename} -> {out_path}")

        # CSV 읽기 및 Parquet 저장
        df = pd.read_csv(f)
        df.to_parquet(out_path, index=False)

    print(
        f"\n성공! 총 {len(csv_files)}개의 Parquet 파일이 data/daily 폴더에 생성되었습니다."
    )