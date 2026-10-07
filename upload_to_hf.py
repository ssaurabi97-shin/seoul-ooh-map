"""
[내 PC에서 실행] build_data.py 가 만든 data 폴더를 Hugging Face 데이터셋 저장소에 올립니다.
이미 올라간 파일은 건너뛰고, 새로 생긴 일별 Parquet와 갱신된 manifest.json 만 올라갑니다.

준비
  1) https://huggingface.co 가입 -> 오른쪽 위 프로필 -> Settings -> Access Tokens 에서 'Write' 토큰 발급
  2) pip install huggingface_hub
  3) (Windows PowerShell)  $env:HF_TOKEN="발급받은토큰"

실행
  python upload_to_hf.py --repo 내아이디/seoul-ooh-data

Streamlit Cloud Secrets 에 아래 값을 등록하세요.
  DATA_BASE_URL = "https://huggingface.co/datasets/내아이디/seoul-ooh-data/resolve/main"
"""
import argparse
import os

from huggingface_hub import HfApi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="예: myid/seoul-ooh-data")
    ap.add_argument("--folder", default="data", help="build_data.py 출력 폴더 (manifest.json, daily/ 포함)")
    ap.add_argument("--private", action="store_true", help="비공개 저장소로 생성 (앱에서 읽으려면 추가 설정 필요)")
    args = ap.parse_args()

    if not os.path.exists(os.path.join(args.folder, "manifest.json")):
        raise SystemExit(f"{args.folder}/manifest.json 이 없습니다. build_data.py를 먼저 실행하세요.")

    api = HfApi(token=os.environ.get("HF_TOKEN"))
    api.create_repo(args.repo, repo_type="dataset", private=args.private, exist_ok=True)
    api.upload_large_folder(folder_path=args.folder, repo_id=args.repo, repo_type="dataset")
    print("업로드 완료.")
    print(f'DATA_BASE_URL = "https://huggingface.co/datasets/{args.repo}/resolve/main"')


if __name__ == "__main__":
    main()
