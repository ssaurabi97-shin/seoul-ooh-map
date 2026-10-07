"""
[내 PC에서 실행하는 데이터 변환 스크립트]  (Streamlit 앱에는 올리지 않아도 됩니다)

일별 원본 CSV(약 50MB)를 앱이 빠르게 읽는 압축 Parquet(일별 1개)으로 변환합니다.
  - 이미 변환된 날짜는 건너뜁니다 -> 새 CSV만 추가해서 계속 실행하면 됩니다.
  - data/daily/YYYYMMDD.parquet  : 일별 데이터 (외부 저장소에 업로드)
  - data/manifest.json           : 날짜 목록 + 컬럼 정보 (외부 저장소에 업로드)
  - grid_coords.csv              : 격자ID, lon, lat (GitHub 앱 저장소에 업로드)

사용법 (예: 2024-07-01 ~ 2026-08-31 구간만 변환)
  pip install pandas pyarrow            # grid_coords.csv 가 이미 있으면 이것만으로 충분
  python build_data.py --csv-dir "C:/원본CSV폴더" --start 20240701 --end 20260831

  - grid_coords.csv 가 없으면 Shapefile로 새로 만듭니다 (pip install geopandas pyogrio shapely, --shp 지정)
  - --decimals 1 : 인구수를 소수 1자리로 반올림해 저장 (용량이 크게 줄어듭니다. 끄려면 --decimals -1)
"""
import argparse
import glob
import json
import os
import re

import pandas as pd

ID_CANDS = ["250M격자", "250m격자", "격자ID", "격자id", "GRID_ID", "격자코드", "집계구코드", "TOT_REG_CD",
            "ADSTRD_CODE_SE", "GID", "ID"]
TIME_CANDS = ["시간대", "시간대구분", "TMZON", "TMZON_PD_SE", "HOUR", "시간"]
GENDER_CANDS = ["성별", "GENDER", "SEX"]
AGE_CANDS = ["연령대", "AGE_GRP", "AGE"]
POP_CANDS = ["인구수", "POP_CNT", "생활인구수", "LVPOP_CO"]


# ---------------- 파싱 유틸 ----------------
def find_col(columns, candidates):
    lowered = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


def read_csv_auto(path):
    last = None
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            head = pd.read_csv(path, encoding=enc, nrows=0)
            id_col = find_col(list(head.columns), ID_CANDS)
            dtype = {id_col: str} if id_col else None  # 격자ID 앞자리 0 보존
            return pd.read_csv(path, encoding=enc, low_memory=False, dtype=dtype, thousands=",")
        except UnicodeDecodeError as e:
            last = e
    raise last


def clean_id(s):
    return s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def norm_gender(v):
    s = str(v).strip().upper()
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


def std_name(g, a):
    """(여성, '20-24세') -> 'f_20_24' / (남성, '70세 이상') -> 'm_70p'"""
    prefix = "f" if g == "여성" else "m"
    if a == "70세 이상":
        return f"{prefix}_70p"
    lo, hi = re.findall(r"\d+", a)[:2]
    return f"{prefix}_{lo}_{hi}"


def parse_demo_col(name):
    s = str(name).strip()
    u = s.upper()
    if "TOT" in u or "합계" in s or s.startswith("총"):
        return None
    g, a = norm_gender(s), norm_age(s)
    return (g, a) if g and a else None


# ---------------- CSV -> 표준 와이드 DataFrame ----------------
def convert_csv(path):
    raw = read_csv_auto(path)
    cols = list(raw.columns)
    id_col = find_col(cols, ID_CANDS)
    t_col = find_col(cols, TIME_CANDS)
    if id_col is None or t_col is None:
        raise ValueError(f"격자ID/시간대 컬럼 인식 실패. 실제 컬럼: {cols}")

    g_col, a_col, p_col = (find_col(cols, GENDER_CANDS), find_col(cols, AGE_CANDS),
                           find_col(cols, POP_CANDS))
    if g_col and a_col and p_col:  # 롱 포맷
        df = raw[[id_col, t_col, g_col, a_col, p_col]].copy()
        df.columns = ["grid_id", "hour", "g", "a", "pop"]
        df["g"], df["a"] = df["g"].map(norm_gender), df["a"].map(norm_age)
        df = df.dropna(subset=["g", "a"])
        df["pop"] = pd.to_numeric(df["pop"], errors="coerce").fillna(0)
        df["key"] = [std_name(g, a) for g, a in zip(df["g"], df["a"])]
        out = df.pivot_table(index=["grid_id", "hour"], columns="key", values="pop",
                             aggfunc="sum", fill_value=0).reset_index()
        meta = {std_name(g, a): [g, a] for g, a in set(zip(df["g"], df["a"]))}
    else:  # 와이드 포맷 (같은 성별·연령으로 매핑되는 컬럼은 합산)
        groups = {}
        for c in cols:
            if c in (id_col, t_col):
                continue
            parsed = parse_demo_col(c)
            if parsed:
                groups.setdefault(parsed, []).append(c)
        if not groups:
            raise ValueError(f"성별·연령 인구 컬럼 인식 실패. 실제 컬럼: {cols}")
        out = pd.DataFrame({"grid_id": raw[id_col], "hour": raw[t_col]})
        meta = {}
        for (g, a), src in groups.items():
            name = std_name(g, a)
            out[name] = raw[src].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
            meta[name] = [g, a]

    out["grid_id"] = clean_id(out["grid_id"])
    out["hour"] = pd.to_numeric(out["hour"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    out = out.dropna(subset=["hour"])
    out["hour"] = out["hour"].astype("int8")
    pop_cols = sorted(meta)
    out = out.groupby(["grid_id", "hour"], as_index=False)[pop_cols].sum()
    out[pop_cols] = out[pop_cols].astype("float32")
    return out.sort_values(["hour", "grid_id"]).reset_index(drop=True), meta


# ---------------- 격자 좌표 ----------------
def build_grid_coords(shp_path, sample_ids, out_csv):
    import geopandas as gpd  # 격자 좌표를 새로 만들 때만 필요

    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:  # .prj 누락 대비: 좌표 범위로 추정
        minx, miny, maxx, maxy = gdf.total_bounds
        if abs(maxx) <= 180 and abs(maxy) <= 90:
            gdf = gdf.set_crs(epsg=4326)
        elif miny > 1_000_000:
            gdf = gdf.set_crs(epsg=5179)
        else:
            gdf = gdf.set_crs(epsg=5186)
        print(f"[경고] Shapefile 좌표계 정보가 없어 {gdf.crs}로 가정했습니다.")
    gdf = gdf.to_crs(epsg=5179)
    cent = gpd.GeoSeries(gdf.geometry.centroid, crs=5179).to_crs(epsg=4326)
    attrs = pd.DataFrame(gdf.drop(columns="geometry"))

    ids, best, best_n = set(sample_ids), None, 0
    for c in attrs.columns:
        n = len(ids & set(clean_id(attrs[c])))
        if n > best_n:
            best, best_n = c, n
    if best is None:
        raise ValueError("CSV 격자ID와 일치하는 Shapefile 컬럼이 없습니다.\n"
                         f"CSV 예시: {list(ids)[:5]}\nSHP 컬럼: {list(attrs.columns)}")
    grid = pd.DataFrame({"grid_id": clean_id(attrs[best]), "lon": cent.x.values, "lat": cent.y.values})
    grid = grid.drop_duplicates("grid_id")
    grid.to_csv(out_csv, index=False)
    print(f"격자 좌표 저장: {out_csv} ({len(grid):,}개, ID 컬럼 '{best}', 일치 {best_n:,}개)")


# ---------------- 메인 ----------------
def save_manifest(path, manifest):
    manifest["dates"] = sorted(set(manifest["dates"]))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", default=".", help="원본 일별 CSV 폴더")
    ap.add_argument("--out-dir", default="data", help="Parquet 출력 폴더")
    ap.add_argument("--shp", default="match.shp", help="격자 Shapefile 경로 (grid_coords.csv가 없을 때만 사용)")
    ap.add_argument("--grid-out", default="grid_coords.csv")
    ap.add_argument("--start", default=None, help="변환 시작일 YYYYMMDD (포함)")
    ap.add_argument("--end", default=None, help="변환 종료일 YYYYMMDD (포함)")
    ap.add_argument("--decimals", type=int, default=1, help="인구수 반올림 자리수 (음수면 반올림 안 함)")
    args = ap.parse_args()

    daily_dir = os.path.join(args.out_dir, "daily")
    os.makedirs(daily_dir, exist_ok=True)
    manifest_path = os.path.join(args.out_dir, "manifest.json")
    manifest = {"dates": [], "columns": {}}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

    files = {}
    for p in glob.glob(os.path.join(args.csv_dir, "*.csv")):
        name = os.path.basename(p)
        m = re.search(r"(\d{8})", name)
        if not m or name.lower() == "grid_coords.csv":
            continue
        d = m.group(1)
        if (args.start and d < args.start) or (args.end and d > args.end):
            continue
        files[d] = p

    total = len(files)
    print(f"대상 CSV {total}개" + (f" ({min(files)} ~ {max(files)})" if files else ""))
    first_ids, done, skipped = None, 0, 0
    try:
        for n, (date, path) in enumerate(sorted(files.items()), 1):
            out_path = os.path.join(daily_dir, f"{date}.parquet")
            if os.path.exists(out_path) and manifest["columns"]:
                if date not in manifest["dates"]:  # 이전 실행이 중간에 끊긴 경우 복구
                    manifest["dates"].append(date)
                skipped += 1
                continue
            print(f"[{n}/{total}] 변환 중: {os.path.basename(path)}")
            df, meta = convert_csv(path)
            pop_cols = [c for c in df.columns if c not in ("grid_id", "hour")]
            if args.decimals >= 0:
                df[pop_cols] = df[pop_cols].round(args.decimals).astype("float32")

            if manifest["columns"]:  # 기존 컬럼 구성에 맞춤
                known = list(manifest["columns"])
                for c in known:
                    if c not in df.columns:
                        df[c] = 0.0
                extra = [c for c in meta if c not in manifest["columns"]]
                if extra:
                    print(f"  [경고] 기존에 없던 컬럼 무시: {extra}")
                df = df[["grid_id", "hour"] + known]
            else:
                manifest["columns"] = meta
            # row group을 작게 나눠 시간대 필터 시 필요한 부분만 읽도록 함
            df.to_parquet(out_path, compression="zstd", index=False, row_group_size=60000)
            print(f"  -> {os.path.getsize(out_path)/1e6:.1f}MB, {len(df):,}행")
            if first_ids is None:
                first_ids = df["grid_id"].unique()
            if date not in manifest["dates"]:
                manifest["dates"].append(date)
            done += 1
            if done % 20 == 0:  # 중간 저장: 끊겨도 진행분이 보존됨
                save_manifest(manifest_path, manifest)
    finally:
        save_manifest(manifest_path, manifest)

    size_mb = sum(os.path.getsize(f) for f in glob.glob(os.path.join(daily_dir, "*.parquet"))) / 1e6
    print(f"완료: 이번 변환 {done}개, 건너뜀 {skipped}개 / manifest 총 {len(manifest['dates'])}일 "
          f"/ Parquet 총 {size_mb:,.0f}MB")

    if not os.path.exists(args.grid_out):
        if first_ids is None and manifest["dates"]:
            first_ids = pd.read_parquet(os.path.join(daily_dir, f"{manifest['dates'][0]}.parquet"),
                                        columns=["grid_id"])["grid_id"].unique()
        if first_ids is not None:
            build_grid_coords(args.shp, first_ids, args.grid_out)


if __name__ == "__main__":
    main()
