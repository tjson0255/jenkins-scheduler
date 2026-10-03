"""標準5フィールドの cron 式（分 時 日 月 曜日）の解釈と、発火時刻の計算。

外部ライブラリを使わずに実装する。対応する書き方:
  *  数字  a-b（範囲）  a,b,c（列挙）  */n・a-b/n・a/n（間隔）
  月の英字名（JAN〜DEC）、曜日の英字名（SUN〜SAT）。曜日の 0 と 7 はどちらも日曜
日と曜日の両方を指定した場合は、どちらかに当てはまれば発火する（一般的な cron と同じ）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

MONTHS = {n: i for i, n in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
DAYS = {n: i for i, n in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}
# (最小, 最大, 英字名)
FIELDS = [(0, 59, {}), (0, 23, {}), (1, 31, {}), (1, 12, MONTHS), (0, 7, DAYS)]
# 見つからないまま探し続けないための上限（2月30日のように当てはまる日が無い式への対策）
MAX_SEARCH_DAYS = 366 * 5


class CronSyntaxError(ValueError):
    pass


def _value(tok: str, lo: int, hi: int, names: dict[str, int]) -> int:
    t = tok.upper()
    if t in names:
        return names[t]
    if not t.isdigit():
        raise CronSyntaxError(f"「{tok}」は使えません")
    v = int(t)
    if not lo <= v <= hi:
        raise CronSyntaxError(f"{v} は範囲外です（{lo}〜{hi}）")
    return v


def _field(text: str, lo: int, hi: int, names: dict[str, int]) -> tuple[set[int], bool]:
    """1フィールドを値の集合にする。2つ目の戻り値は「*（制限なし）」かどうか。"""
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronSyntaxError("カンマの前後が空です")
        rng, _, step_s = part.partition("/")
        step = 1
        if step_s:
            if not step_s.isdigit() or int(step_s) == 0:
                raise CronSyntaxError(f"間隔「{step_s}」が不正です")
            step = int(step_s)
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a_s, _, b_s = rng.partition("-")
            a, b = _value(a_s, lo, hi, names), _value(b_s, lo, hi, names)
            if a > b:
                raise CronSyntaxError(f"範囲「{rng}」が逆順です")
        else:
            a = _value(rng, lo, hi, names)
            b = hi if step_s else a  # 「5/15」は 5 から最大値まで 15 ごと
        values.update(range(a, b + 1, step))
    # 「*」（と同じ意味の「*/1」）を「制限なし」として扱う（日と曜日の組み合わせの判定に使う。「*/2」は制限あり）
    return values, text in ("*", "*/1")


class CronExpr:
    def __init__(self, expr: str):
        fields = expr.split()
        if len(fields) != 5:
            raise CronSyntaxError("cron は5フィールド（分 時 日 月 曜日）で指定してください")
        parsed = [_field(f, lo, hi, names) for f, (lo, hi, names) in zip(fields, FIELDS)]
        (self.minutes, _), (self.hours, _), (self.days, dom_any), (self.months, _), (dows, dow_any) = parsed
        self.dows = {d % 7 for d in dows}  # 7 も日曜
        self.dom_any, self.dow_any = dom_any, dow_any
        self._minutes = sorted(self.minutes)
        self._hours = sorted(self.hours)

    def matches_day(self, d: date) -> bool:
        if d.month not in self.months:
            return False
        dom = d.day in self.days
        dow = (d.isoweekday() % 7) in self.dows
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow
        if self.dow_any:
            return dom
        return dom or dow  # 両方指定したときはどちらか

    def iter_local(self, start: datetime):
        """start（naive のローカル時刻、分単位）以降の発火時刻を順に返す。start ちょうども含む。"""
        d = start.date()
        first = True
        for _ in range(MAX_SEARCH_DAYS):
            if self.matches_day(d):
                for h in self._hours:
                    if first and h < start.hour:
                        continue
                    for m in self._minutes:
                        if first and h == start.hour and m < start.minute:
                            continue
                        yield datetime.combine(d, time(h, m))
            d += timedelta(days=1)
            first = False


def is_valid(expr: str) -> bool:
    try:
        CronExpr(expr)
    except CronSyntaxError:
        return False
    return True
