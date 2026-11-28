"""【臨時】取りこぼしたコンテストの提出データを復元するスクリプト

ATCODER_SESSION 切れで取得できなかった期間 (AWC0158〜) を取り直す。

方針
  - 提出一覧を「古い順」で取得し、コンテスト時間 (20:00〜21:00) を過ぎたら打ち切る。
    → 有効な提出だけを最小限のページ数で取得できる。
  - 1ページ取得するごとに restore_work/AWCxxxx/page_NNN.csv として保存する。
    古い順なのでページ内容は後から変わらず、途中で止めても続きから再開できる。
  - コンテストを最後まで取り終えたら DONE ファイルを置く。
  - raw_data.csv への反映は merge コマンドで明示的に行う（二重反映は自動で防止）。

Usage:
    python scripts/restore_submissions.py status              # 進捗一覧（引数なしでも同じ）
    python scripts/restore_submissions.py run                 # 未完了のコンテストを順に取得
    python scripts/restore_submissions.py run --from 170 --to 180
    python scripts/restore_submissions.py run --minutes 30    # 30分たったら区切りのよいところで止める
    python scripts/restore_submissions.py run --sleep 5       # リクエスト間隔（秒, 既定 3）
    python scripts/restore_submissions.py merge               # 取得済みを raw_data.csv に反映
    python scripts/restore_submissions.py reset 170           # 指定コンテストの取得結果を消して取り直す

Ctrl+C でいつでも止めて構いません。次回 run で続きから再開します。
"""

import argparse
import csv
import json
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
import scrape_submissions as scraper  # noqa: E402
from utils import contest_date, contest_num_from_date, is_valid  # noqa: E402

ROOT = Path(__file__).parent.parent
WORK_DIR = ROOT / "restore_work"
RAW_DATA_FILE = scraper.RAW_DATA_FILE
FIELDNAMES = scraper.FIELDNAMES

DEFAULT_FROM = 158  # 取得が止まった最初のコンテスト
PAGE_SIZE = 20      # AtCoder の提出一覧 1ページあたりの件数


# ---------------------------------------------------------------- 基本情報

def cid(num: int) -> str:
    return f"AWC{num:04d}"


def default_to() -> int:
    """今日より前で最後に開催されたコンテスト番号"""
    d = date.today() - timedelta(days=1)
    while contest_num_from_date(d) is None:
        d -= timedelta(days=1)
    return contest_num_from_date(d)


def contest_dir(num: int) -> Path:
    return WORK_DIR / cid(num)


def page_files(num: int) -> list[Path]:
    return sorted(contest_dir(num).glob("page_*.csv"))


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_atomic(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def done_info(num: int) -> dict | None:
    p = contest_dir(num) / "DONE"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def merged_contests() -> dict[str, int]:
    """raw_data.csv に既に入っているコンテストIDと件数"""
    counts: dict[str, int] = {}
    if RAW_DATA_FILE.exists():
        with open(RAW_DATA_FILE, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                counts[r["contest_id"]] = counts.get(r["contest_id"], 0) + 1
    return counts


# ---------------------------------------------------------------- status

def cmd_status(args) -> None:
    lo, hi = args.from_, args.to
    merged = merged_contests()
    summary = {"反映済": 0, "取得済": 0, "取得中": 0, "未着手": 0}

    print(f"対象: {cid(lo)}〜{cid(hi)}  （作業フォルダ: {WORK_DIR.relative_to(ROOT)}）\n")
    print(f"  {'コンテスト':<9} {'開催日':<11} {'状態':<6} {'件数':>6}  備考")
    print("  " + "-" * 52)
    for n in range(lo, hi + 1):
        pages = page_files(n)
        info = done_info(n)
        if cid(n) in merged:
            state, count, note = "反映済", merged[cid(n)], ""
        elif info:
            state, count, note = "取得済", info["rows"], f"{info['pages']}ページ"
        elif pages:
            rows = sum(len(read_csv(p)) for p in pages)
            last = read_csv(pages[-1])
            t = last[-1]["submitted_at"][11:] if last else "-"
            state, count, note = "取得中", rows, f"{len(pages)}ページまで / {t} まで取得"
        else:
            state, count, note = "未着手", 0, ""
        summary[state] += 1
        mark = {"反映済": "✔", "取得済": "●", "取得中": "▶", "未着手": "・"}[state]
        print(f"{mark} {cid(n):<9} {contest_date(n)}  {state:<6} {count:>6}  {note}")

    total = hi - lo + 1
    print("  " + "-" * 52)
    print("  " + " / ".join(f"{k} {v}" for k, v in summary.items()) + f"  （全{total}）")
    if summary["取得中"] + summary["未着手"]:
        print("\n次: python scripts/restore_submissions.py run  で続きを取得")
    elif summary["取得済"]:
        print("\n次: python scripts/restore_submissions.py merge  で raw_data.csv に反映")
    else:
        print("\nすべて反映済みです。python scripts/analyze.py でランキングを更新してください。")


# ---------------------------------------------------------------- run

def fetch_page(num: int, page: int, sleep: float) -> tuple[list[dict], int]:
    """古い順で1ページ取得し、(パース結果, 表の行数) を返す（一時的なエラーは待って再試行）

    CE などセル数の少ない行は scrape_submissions が読み飛ばす（日次取得と同じ挙動）ため、
    最終ページの判定にはパース件数ではなく表の行数を使う。
    """
    url = (f"https://atcoder.jp/contests/{cid(num).lower()}/submissions"
           f"?orderBy=created&desc=false&page={page}")
    for attempt in range(4):
        try:
            html, _ = scraper.fetch_html(url)
            tbody = BeautifulSoup(html, "html.parser").select_one("table.table-bordered tbody")
            n_tr = len(tbody.find_all("tr")) if tbody else 0
            return scraper.scrape_submissions(html), n_tr
        except (requests.ConnectionError, requests.Timeout) as e:
            err = str(e)
        except requests.HTTPError as e:
            code = e.response.status_code
            if code == 404:
                raise SystemExit(f"エラー: {cid(num)} が見つかりません (404)。")
            if code not in (429, 500, 502, 503, 504):
                raise
            err = f"HTTP {code}"
        wait = sleep * 10 * (attempt + 1)
        print(f"    ⚠ {err} → {wait:.0f}秒待って再試行 ({attempt + 1}/3)")
        time.sleep(wait)
    raise SystemExit(f"エラー: {url} の取得に失敗しました。時間をおいて再実行してください。")


def restore_contest(num: int, sleep: float, deadline: float | None) -> bool:
    """1コンテスト分を取得。最後まで取れたら True、時間切れで中断したら False"""
    d = contest_dir(num)
    d.mkdir(parents=True, exist_ok=True)
    valid_dates = {cid(num): contest_date(num).strftime("%Y-%m-%d")}
    window_end = f"{valid_dates[cid(num)]} 21:00:00"

    pages = page_files(num)
    page = len(pages) + 1
    total = sum(len(read_csv(p)) for p in pages)
    if pages:
        print(f"  {page - 1}ページ目まで取得済み（{total}件）→ {page}ページ目から再開")

    while True:
        if deadline and time.time() > deadline:
            return False

        rows, n_tr = fetch_page(num, page, sleep)
        valid = [r for r in rows if r["submitted_at"] and is_valid(r, valid_dates)]
        reached_end = (
            n_tr < PAGE_SIZE                                        # 最終ページ
            or any(r["submitted_at"] >= window_end for r in rows)   # コンテスト時間を過ぎた
        )

        # 有効な提出が0件でも保存する（ページ番号 = ファイル数 で再開位置を決めるため）
        write_csv_atomic(d / f"page_{page:03d}.csv", valid)
        total += len(valid)
        if valid:
            t = valid[-1]["submitted_at"][11:]
            pct = min(100, (int(t[:2]) - 20) * 60 + int(t[3:5])) * 100 // 60
            bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
            print(f"  p{page:<3} +{len(valid):>2}件  [{bar}] {t} まで  計{total}件")

        if reached_end:
            n_pages = len(page_files(num))
            (d / "DONE").write_text(json.dumps({
                "rows": total, "pages": n_pages,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False), encoding="utf-8")
            print(f"  ✔ {cid(num)} 完了: {total}件 / {n_pages}ページ")
            return True

        page += 1
        time.sleep(sleep)


def cmd_run(args) -> None:
    if not os.environ.get("ATCODER_SESSION"):
        raise SystemExit("エラー: ATCODER_SESSION が未設定です（.env を確認してください）。")

    merged = merged_contests()
    todo = [n for n in range(args.from_, args.to + 1)
            if cid(n) not in merged and not done_info(n)]
    if not todo:
        print("取得が必要なコンテストはありません。status / merge を確認してください。")
        return

    deadline = time.time() + args.minutes * 60 if args.minutes else None
    started = time.time()
    print(f"取得対象: {len(todo)}コンテスト（{cid(todo[0])}〜{cid(todo[-1])}）"
          f"  間隔 {args.sleep}秒" + (f"  制限 {args.minutes}分" if args.minutes else ""))
    print("Ctrl+C で止めても、次回 run で続きから再開できます。\n")

    try:
        for i, n in enumerate(todo, 1):
            elapsed = time.time() - started
            eta = ""
            if i > 1:
                remain = elapsed / (i - 1) * (len(todo) - i + 1)
                eta = f"  残り目安 {remain / 60:.0f}分"
            print(f"[{i}/{len(todo)}] {cid(n)} ({contest_date(n)})  経過 {elapsed / 60:.0f}分{eta}")
            if not restore_contest(n, args.sleep, deadline):
                print(f"\n⏸ 制限時間 {args.minutes}分 に達したので中断しました。次回 run で続きから再開します。")
                return
            time.sleep(args.sleep)
    except KeyboardInterrupt:
        print("\n⏸ 中断しました。次回 run で続きから再開します。")
        return

    print("\n✔ 対象コンテストをすべて取得しました。次: merge で raw_data.csv に反映")


# ---------------------------------------------------------------- merge

def cmd_merge(args) -> None:
    merged = merged_contests()
    targets = [n for n in range(args.from_, args.to + 1)
               if done_info(n) and cid(n) not in merged]
    if not targets:
        print("反映できるコンテストはありません（取得済みかつ未反映のものがない）。")
        return

    with open(RAW_DATA_FILE, "rb") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) != b"\n":
            raise SystemExit("エラー: raw_data.csv の末尾が改行で終わっていません。確認してください。")

    added = 0
    with open(RAW_DATA_FILE, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        for n in targets:
            rows = [r for p in page_files(n) for r in read_csv(p)]
            # 既存データ（日次取得）と同じく新しい順に並べる
            rows.sort(key=lambda r: int(r["submission_id"]), reverse=True)
            w.writerows(rows)
            added += len(rows)
            print(f"  ✔ {cid(n)}: {len(rows)}件")
    print(f"\n{len(targets)}コンテスト / {added}件 を raw_data.csv に追加しました。")
    print("次: python scripts/analyze.py でランキングを更新")


# ---------------------------------------------------------------- reset

def cmd_reset(args) -> None:
    d = contest_dir(args.num)
    if cid(args.num) in merged_contests():
        raise SystemExit(f"{cid(args.num)} は raw_data.csv に反映済みです。reset では消せません。")
    if d.exists():
        shutil.rmtree(d)
        print(f"{cid(args.num)} の取得結果を削除しました。")
    else:
        print(f"{cid(args.num)} の取得結果はありません。")


# ---------------------------------------------------------------- main

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description="取りこぼした提出データの臨時復元")
    sub = parser.add_subparsers(dest="cmd")

    def add_range(p):
        p.add_argument("--from", dest="from_", type=int, default=DEFAULT_FROM)
        p.add_argument("--to", type=int, default=default_to())

    add_range(sub.add_parser("status", help="進捗一覧"))
    p_run = sub.add_parser("run", help="未完了のコンテストを取得")
    add_range(p_run)
    p_run.add_argument("--sleep", type=float, default=3.0, help="リクエスト間隔（秒）")
    p_run.add_argument("--minutes", type=float, help="この時間を過ぎたら中断")
    add_range(sub.add_parser("merge", help="取得済みを raw_data.csv に反映"))
    p_reset = sub.add_parser("reset", help="指定コンテストを取り直す")
    p_reset.add_argument("num", type=int)

    args = parser.parse_args()
    if args.cmd is None:
        args = parser.parse_args(["status"])
    {"status": cmd_status, "run": cmd_run, "merge": cmd_merge, "reset": cmd_reset}[args.cmd](args)


if __name__ == "__main__":
    main()
