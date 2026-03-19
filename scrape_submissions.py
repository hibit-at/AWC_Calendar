"""AtCoder AWC コンテストの提出一覧をスクレイピングしてCSVに保存するスクリプト

Usage:
    python scrape_submissions.py 29
    python scrape_submissions.py 29 --pages 3
"""

import csv
import io
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

RAW_DATA_FILE = Path(__file__).parent / "raw_data.csv"
FIELDNAMES = [
    "contest_id", "submitted_at", "problem", "username",
    "language", "result", "execution_time", "submission_id",
]


def check_existing_contest(contest_id: str) -> None:
    """同一コンテストのデータが既にあれば警告する"""
    if not RAW_DATA_FILE.exists() or RAW_DATA_FILE.stat().st_size == 0:
        return
    with open(RAW_DATA_FILE, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        count = sum(1 for r in reader if r["contest_id"] == contest_id)
    if count > 0:
        print(f"  ⚠ {contest_id} のデータが既に{count}件あります", file=sys.stderr)


def append_rows(rows: list[dict]):
    """既存CSVに行を追記する。ファイルがなければヘッダー付きで新規作成。"""
    file_exists = RAW_DATA_FILE.exists() and RAW_DATA_FILE.stat().st_size > 0
    with open(RAW_DATA_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def fetch_html(url: str) -> tuple[str, requests.Response]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.9",
    }
    cookies = {}
    session_val = os.environ.get("ATCODER_SESSION")
    if session_val:
        cookies["REVEL_SESSION"] = session_val
    else:
        print("  ⚠ ATCODER_SESSION 環境変数が未設定（ログインなしでアクセス）", file=sys.stderr)

    resp = requests.get(url, headers=headers, cookies=cookies, timeout=30)
    resp.raise_for_status()
    return resp.text, resp


def parse_contest_id(soup: BeautifulSoup) -> str | None:
    script_tag = soup.find("script", string=re.compile(r"contestScreenName"))
    if script_tag:
        m = re.search(r'contestScreenName\s*=\s*"([^"]+)"', script_tag.string)
        if m:
            return m.group(1).upper()
    link = soup.select_one('a[href*="/contests/awc"]')
    if link:
        m = re.search(r"/contests/(awc\d+)", link["href"])
        if m:
            return m.group(1).upper()
    return None


def diagnose_html(html: str, resp: requests.Response) -> None:
    """レスポンスの内容を診断してログ出力する"""
    print(f"  HTTP {resp.status_code} | Content-Length: {len(html)} bytes", file=sys.stderr)

    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    title_text = title.get_text(strip=True) if title else "(titleタグなし)"
    print(f"  ページタイトル: {title_text}", file=sys.stderr)

    if "login" in resp.url.lower() or (title and "ログイン" in title_text):
        print("  ⚠ ログインページにリダイレクトされています", file=sys.stderr)

    if "cf-browser-verification" in html or "cf-challenge" in html:
        print("  ⚠ Cloudflare のbot検知ページが返されています", file=sys.stderr)
    elif "Just a moment" in html:
        print("  ⚠ Cloudflare の待機ページが返されています", file=sys.stderr)

    contest_name = parse_contest_id(soup)
    if contest_name:
        print(f"  コンテストID検出: {contest_name}", file=sys.stderr)
    else:
        print("  ⚠ コンテストIDが検出できません（AtCoderの提出ページではない可能性）", file=sys.stderr)

    table = soup.select_one("table.table-bordered tbody")
    if table:
        rows = table.find_all("tr")
        print(f"  提出テーブル: {len(rows)}行", file=sys.stderr)
    else:
        print("  ⚠ 提出テーブルが見つかりません", file=sys.stderr)
        body = soup.find("body")
        if body:
            text = body.get_text(strip=True)[:200]
            print(f"  body先頭: {text}", file=sys.stderr)


def scrape_submissions(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    contest_id = parse_contest_id(soup)

    table = soup.select_one("table.table-bordered tbody")
    if not table:
        return []

    submissions = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 10:
            continue

        time_tag = cells[0].find("time")
        submitted_at = ""
        if time_tag:
            raw = time_tag.get_text(strip=True)
            # "+0900" 等のタイムゾーン部分を除去（JST前提）
            submitted_at = re.sub(r"[+-]\d{4}$", "", raw)

        problem_link = cells[1].find("a")
        problem = ""
        if problem_link:
            m = re.match(r"^([A-Z])", problem_link.get_text(strip=True))
            if m:
                problem = m.group(1)

        user_link = cells[2].find("a")
        username = user_link.get_text(strip=True) if user_link else ""

        lang_link = cells[3].find("a")
        language = lang_link.get_text(strip=True) if lang_link else cells[3].get_text(strip=True)
        # "(GCC 15.2.0)" 等のバージョン情報を除去
        language = re.sub(r"\s*\(.*?\)\s*$", "", language)

        result_span = cells[6].find("span", class_="label")
        result = result_span.get_text(strip=True) if result_span else cells[6].get_text(strip=True)

        exec_raw = cells[7].get_text(strip=True)
        # "179 ms" → "179", "> 2000 ms" → "> 2000"
        execution_time = exec_raw.replace(" ms", "").strip()

        detail_link = cells[9].find("a")
        submission_id = ""
        if detail_link:
            m = re.search(r"/submissions/(\d+)", detail_link["href"])
            if m:
                submission_id = m.group(1)

        submissions.append({
            "contest_id": contest_id,
            "submitted_at": submitted_at,
            "problem": problem,
            "username": username,
            "language": language,
            "result": result,
            "execution_time": execution_time,
            "submission_id": submission_id,
        })

    return submissions


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <コンテスト番号> [--pages PAGES]", file=sys.stderr)
        print(f"  例: python {sys.argv[0]} 29             # 全ページ取得", file=sys.stderr)
        print(f"  例: python {sys.argv[0]} 29 --pages 5   # 1〜5ページ", file=sys.stderr)
        print(f"  例: python {sys.argv[0]} 29 --pages 3-8 # 3〜8ページ", file=sys.stderr)
        sys.exit(1)

    contest_num = sys.argv[1]
    contest_slug = f"awc{int(contest_num):04d}"
    base_url = f"https://atcoder.jp/contests/{contest_slug}/submissions"

    # ページ範囲の解析
    start_page = 1
    end_page = None  # None = ヒットがなくなるまで
    if "--pages" in sys.argv:
        idx = sys.argv.index("--pages")
        pages_arg = sys.argv[idx + 1]
        if "-" in pages_arg:
            start_page, end_page = map(int, pages_arg.split("-", 1))
        else:
            end_page = int(pages_arg)

    contest_id = contest_slug.upper()
    check_existing_contest(contest_id)

    total_added = 0
    page = start_page
    while end_page is None or page <= end_page:
        page_url = f"{base_url}?page={page}"

        print(f"取得中: {page_url}", file=sys.stderr)
        html, resp = fetch_html(page_url)
        diagnose_html(html, resp)
        submissions = scrape_submissions(html)

        if not submissions:
            print(f"  → 提出データ0件、終了", file=sys.stderr)
            break

        append_rows(submissions)
        total_added += len(submissions)

        print(f"  ページ {page}: {len(submissions)}件追加", file=sys.stderr)

        page += 1
        time.sleep(2)  # サーバーへの負荷軽減

    print(f"完了: {total_added}件追加 → {RAW_DATA_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
