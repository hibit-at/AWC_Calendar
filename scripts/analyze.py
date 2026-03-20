"""AWC 提出データを解析して各種ランキングCSVを出力するスクリプト

出力:
  data/unique_ac_ranking.csv  - ユニークACランキング
  data/reach_ranking.csv      - 到達率ランキング（純粋な難問）
  data/trap_ranking.csv       - 罠率ランキング（AC率が低い問題）

バリデーション:
  - コンテスト開催日（AWC0001=2026-02-09起点の平日）の 20:00〜21:00 の提出のみ有効

ユニークACランキングの順位:
  - ユニークAC数降順 → 合計解答時間（分）昇順 → ユーザー名昇順
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_valid_dates, is_valid

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_DATA_FILE = DATA_DIR / "raw_data.csv"


def elapsed_minutes(row: dict, valid_dates: dict[str, str]) -> float:
    """コンテスト開始（20:00）からの経過分数を返す"""
    start = datetime.strptime(f"{valid_dates[row['contest_id']]} 20:00:00", "%Y-%m-%d %H:%M:%S")
    dt = datetime.strptime(row["submitted_at"], "%Y-%m-%d %H:%M:%S")
    return (dt - start).total_seconds() / 60


def analyze_unique_ac(valid_rows: list[dict], valid_dates: dict[str, str]) -> None:
    """ユニークACランキングを出力する"""
    user_best_time: dict[str, dict[tuple[str, str], float]] = {}

    for row in valid_rows:
        if row["result"] != "AC":
            continue
        username = row["username"]
        key = (row["contest_id"], row["problem"])
        minutes = elapsed_minutes(row, valid_dates)
        if username not in user_best_time:
            user_best_time[username] = {}
        if key not in user_best_time[username] or minutes < user_best_time[username][key]:
            user_best_time[username][key] = minutes

    entries = [
        {"rank": 0, "username": u, "unique_ac": len(problems), "total_minutes": round(sum(problems.values()), 2)}
        for u, problems in user_best_time.items()
    ]
    ranking = sorted(entries, key=lambda x: (-x["unique_ac"], x["total_minutes"], x["username"]))

    prev_key = None
    prev_rank = 0
    for i, entry in enumerate(ranking):
        cur_key = (entry["unique_ac"], entry["total_minutes"])
        if cur_key != prev_key:
            prev_rank = i + 1
            prev_key = cur_key
        entry["rank"] = prev_rank

    out = DATA_DIR / "unique_ac_ranking.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "username", "unique_ac", "total_minutes"])
        writer.writeheader()
        writer.writerows(ranking)
    print(f"ユニークACランキング: {len(ranking)}人 → {out}")


def analyze_difficulty(valid_rows: list[dict]) -> None:
    """到達率・罠率ランキングを出力する"""
    # タイプ1: 到達率
    contest_participants: dict[str, set[str]] = defaultdict(set)
    problem_solvers: dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in valid_rows:
        cid = row["contest_id"]
        contest_participants[cid].add(row["username"])
        if row["result"] == "AC":
            problem_solvers[(cid, row["problem"])].add(row["username"])

    reach_entries = []
    for (cid, problem), solvers in problem_solvers.items():
        participants = len(contest_participants[cid])
        reach_entries.append({
            "contest_id": cid,
            "problem": problem,
            "unique_solvers": len(solvers),
            "participants": participants,
            "reach_rate": round(len(solvers) / participants, 4) if participants > 0 else 0,
        })
    reach_entries.sort(key=lambda x: (x["reach_rate"], x["contest_id"], x["problem"]))

    out = DATA_DIR / "reach_ranking.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["contest_id", "problem", "unique_solvers", "participants", "reach_rate"])
        writer.writeheader()
        writer.writerows(reach_entries)
    print(f"到達率ランキング:     {len(reach_entries)}問 → {out}")

    # タイプ2: 罠率（ペナルティ = AC以外の提出）
    problem_ac:      dict[tuple[str, str], int] = defaultdict(int)
    problem_penalty: dict[tuple[str, str], int] = defaultdict(int)

    for row in valid_rows:
        key = (row["contest_id"], row["problem"])
        if row["result"] == "AC":
            problem_ac[key] += 1
        else:
            problem_penalty[key] += 1

    trap_entries = []
    for key in set(problem_ac) | set(problem_penalty):
        cid, problem = key
        ac, penalty = problem_ac[key], problem_penalty[key]
        total = ac + penalty
        trap_entries.append({
            "contest_id": cid,
            "problem": problem,
            "ac_count": ac,
            "penalty_count": penalty,
            "accuracy_rate": round(ac / total, 4) if total > 0 else 1.0,
        })
    trap_entries.sort(key=lambda x: (x["accuracy_rate"], -(x["ac_count"] + x["penalty_count"])))

    out = DATA_DIR / "trap_ranking.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["contest_id", "problem", "ac_count", "penalty_count", "accuracy_rate"])
        writer.writeheader()
        writer.writerows(trap_entries)
    print(f"罠率ランキング:       {len(trap_entries)}問 → {out}")


def main():
    with open(RAW_DATA_FILE, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    valid_dates = load_valid_dates(rows)
    valid_rows = [r for r in rows if is_valid(r, valid_dates)]

    analyze_unique_ac(valid_rows, valid_dates)
    analyze_difficulty(valid_rows)


if __name__ == "__main__":
    main()
