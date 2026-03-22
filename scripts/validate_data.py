"""raw_data.csv のバリデーションスクリプト

チェック項目:
  1. コンテスト時間内（20:00〜21:00）の提出割合
  2. 提出IDの重複
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_valid_dates, is_valid

RAW_DATA_FILE = Path(__file__).parent.parent / "data" / "raw_data.csv"

def main():
    with open(RAW_DATA_FILE, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    print(f"総提出数: {len(rows)}")
    print()

    # --- 1. コンテスト時間内の割合 ---
    valid_dates = load_valid_dates(rows)
    total_by_contest = defaultdict(int)
    valid_by_contest = defaultdict(int)

    for row in rows:
        cid = row["contest_id"]
        total_by_contest[cid] += 1
        if is_valid(row, valid_dates):
            valid_by_contest[cid] += 1

    total_all = len(rows)
    valid_all = sum(valid_by_contest.values())

    print("=== コンテスト時間内の提出割合 ===")
    print(f"{'コンテスト':<12} {'総数':>6} {'有効':>6} {'有効率':>8}")
    print("-" * 36)
    for cid in sorted(total_by_contest):
        t = total_by_contest[cid]
        v = valid_by_contest[cid]
        print(f"{cid:<12} {t:>6} {v:>6} {v/t*100:>7.1f}%")
    print("-" * 36)
    print(f"{'合計':<12} {total_all:>6} {valid_all:>6} {valid_all/total_all*100:>7.1f}%")
    print()

    # --- 2. 提出IDの重複 ---
    print("=== 提出IDの重複チェック ===")
    seen = {}
    duplicates = []
    for row in rows:
        sid = row["submission_id"]
        if sid in seen:
            duplicates.append((sid, seen[sid], row))
        else:
            seen[sid] = row

    if duplicates:
        print(f"NG: 重複あり {len(duplicates)}件")
        for sid, first, second in duplicates:
            print(f"  submission_id={sid}  {first['contest_id']} / {first['username']}  vs  {second['contest_id']} / {second['username']}")
    else:
        print(f"OK: 重複なし（ユニーク提出ID {len(seen)}件）")

if __name__ == "__main__":
    main()
