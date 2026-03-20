"""共通ユーティリティ: バリデーションロジック"""

from datetime import date, datetime, time, timedelta

# AWC0001 の開催日（2026-02-09 月曜日）
AWC_START_DATE = date(2026, 2, 9)
AWC_START_NUM  = 1

VALID_START = time(20, 0, 0)
VALID_END   = time(21, 0, 0)


def contest_date(contest_num: int) -> date:
    """コンテスト番号から開催日（平日）を返す。
    AWC0001 = 2026-02-09 を起点に、(contest_num - 1) 営業日後を計算。
    """
    d = AWC_START_DATE
    remaining = contest_num - AWC_START_NUM
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:  # 0=Mon … 4=Fri
            remaining -= 1
    return d


def contest_num_from_date(d: date) -> int | None:
    """日付からコンテスト番号を返す。平日以外または開始前は None。"""
    if d < AWC_START_DATE or d.weekday() >= 5:  # 5=Sat, 6=Sun
        return None
    cur = AWC_START_DATE
    count = 0
    while cur < d:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            count += 1
    return AWC_START_NUM + count


def contest_num_from_id(contest_id: str) -> int:
    """'AWC0029' -> 29"""
    return int(contest_id.replace("AWC", "").lstrip("0") or "0")


def load_valid_dates(rows: list[dict]) -> dict[str, str]:
    """コンテストIDごとの有効日（YYYY-MM-DD）を開催日計算で返す"""
    dates: dict[str, str] = {}
    for row in rows:
        cid = row["contest_id"]
        if cid not in dates:
            num = contest_num_from_id(cid)
            dates[cid] = contest_date(num).strftime("%Y-%m-%d")
    return dates


def is_valid(row: dict, valid_dates: dict[str, str]) -> bool:
    """有効日の 20:00〜21:00 に含まれる提出かどうか"""
    valid_date = valid_dates.get(row["contest_id"])
    if not valid_date:
        return False
    dt = datetime.strptime(row["submitted_at"], "%Y-%m-%d %H:%M:%S")
    if dt.strftime("%Y-%m-%d") != valid_date:
        return False
    return VALID_START <= dt.time() < VALID_END
