"""Автоматические выводы по данным — то, что дашборд должен говорить сам.

Графики отвечают на вопрос «как выглядят данные». Владелец магазина задаёт
другой: «что изменилось и что с этим делать». Раньше ответ на второй вопрос
приходилось собирать глазами из шести графиков — здесь он считается явно.

Все функции чистые (DataFrame → факты), поэтому проверяются тестами без
Streamlit и без сети.
"""

from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

MIN_BASE_FOR_COMPARISON = 10   # ниже этого числа входов проценты — шум, а не тренд
QUIET_HOURS_ALERT = 3          # часов подряд без единого входа в рабочее время


def entries(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["direction"] == "IN"]


def change_pct(current: float, previous: float) -> Optional[float]:
    if not previous:
        return None
    return (current - previous) / previous * 100


def describe_change(current: int, previous: int, label: str) -> Optional[str]:
    """«Входов на 18% больше, чем за тот же отрезок прошлого периода»."""
    if previous < MIN_BASE_FOR_COMPARISON:
        return None
    delta = change_pct(current, previous)
    if delta is None or abs(delta) < 5:
        return f"Посещаемость на уровне {label} ({current} против {previous})"
    direction = "больше" if delta > 0 else "меньше"
    return f"Входов на {abs(delta):.0f}% {direction}, чем {label} ({current} против {previous})"


def peak_hour_shift(df_now: pd.DataFrame, df_prev: pd.DataFrame) -> Optional[str]:
    """Сдвиг пикового часа — прямой сигнал для планирования смен."""
    now_entries, prev_entries = entries(df_now), entries(df_prev)
    if len(now_entries) < MIN_BASE_FOR_COMPARISON or len(prev_entries) < MIN_BASE_FOR_COMPARISON:
        return None

    now_peak = now_entries.groupby(now_entries["timestamp"].dt.hour).size().idxmax()
    prev_peak = prev_entries.groupby(prev_entries["timestamp"].dt.hour).size().idxmax()
    if now_peak == prev_peak:
        return f"Пик стабильно в {now_peak}:00"
    return f"Пик сместился с {prev_peak}:00 на {now_peak}:00"


def repeat_share_change(df_now: pd.DataFrame, df_prev: pd.DataFrame) -> Optional[str]:
    now_entries, prev_entries = entries(df_now), entries(df_prev)
    if len(now_entries) < MIN_BASE_FOR_COMPARISON or len(prev_entries) < MIN_BASE_FOR_COMPARISON:
        return None

    now_share = now_entries["is_repeat"].mean() * 100
    prev_share = prev_entries["is_repeat"].mean() * 100
    delta = now_share - prev_share
    if abs(delta) < 2:
        return f"Доля повторных держится на {now_share:.0f}%"
    verb = "выросла" if delta > 0 else "упала"
    return f"Доля повторных {verb} на {abs(delta):.0f} п.п. — до {now_share:.0f}%"


def busiest_day(df: pd.DataFrame) -> Optional[str]:
    df_in = entries(df)
    if df_in.empty:
        return None
    by_day = df_in.groupby(df_in["timestamp"].dt.date).size()
    if len(by_day) < 2:
        return None
    best_day = by_day.idxmax()
    weekday_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return (
        f"Самый людный день — {best_day.strftime('%d.%m')} "
        f"({weekday_ru[best_day.weekday()]}, {by_day.max()} входов)"
    )


def identification_quality(df: pd.DataFrame, identified_mask: pd.Series) -> Optional[str]:
    """Доля опознанных проходов — здоровье ReID, а не поведение покупателей.

    Без этой цифры низкая доля повторных читается как «люди не возвращаются»,
    хотя может означать «камера не даёт пригодных кадров».
    """
    df_in = entries(df)
    if df_in.empty:
        return None
    identified = identified_mask.loc[df_in.index]
    share = identified.mean() * 100
    if share >= 90:
        return None
    return (
        f"Опознано {share:.0f}% проходов — остальные посчитаны, но без личности, "
        f"поэтому доли новых/повторных занижены"
    )


def quiet_period(df: pd.DataFrame, work_hours: range, now: datetime) -> Optional[str]:
    """Долгая тишина в рабочее время — обычно отказ, а не отсутствие людей."""
    df_in = entries(df)
    if df_in.empty or now.hour not in work_hours:
        return None
    last_visit = df_in["timestamp"].max()
    idle_hours = (now - last_visit).total_seconds() / 3600
    if idle_hours < QUIET_HOURS_ALERT:
        return None
    return f"Ни одного входа уже {idle_hours:.0f} ч — стоит проверить камеру и сервис"


def forecast_day_total(df_today: pd.DataFrame, df_reference: pd.DataFrame, now: datetime) -> Optional[int]:
    """Прогноз входов до конца дня по профилю прошлых дней.

    Наивная экстраполяция «×24/прошедшие часы» врёт вдвое: посетители
    распределены по дню неравномерно. Берём долю дня, которая обычно набирается
    к этому часу, по историческим данным.
    """
    today_entries = entries(df_today)
    reference = entries(df_reference)
    if today_entries.empty or reference.empty:
        return None

    reference = reference.copy()
    reference["date"] = reference["timestamp"].dt.date
    reference["hour"] = reference["timestamp"].dt.hour
    days = reference["date"].nunique()
    if days < 3:
        return None

    per_day = reference.groupby("date").size()
    up_to_now = reference[reference["hour"] <= now.hour].groupby("date").size()
    shares = (up_to_now / per_day).dropna()
    if shares.empty:
        return None

    typical_share = float(shares.mean())
    if typical_share <= 0.05:
        return None
    return int(round(len(today_entries) / typical_share))


def build_summary(
    df_now: pd.DataFrame,
    df_prev: pd.DataFrame,
    identified_mask: pd.Series,
    period_label: str,
    now: datetime,
    work_hours: range,
) -> List[str]:
    """Три-четыре главные фразы о периоде — то, что читают первым."""
    facts = [
        describe_change(len(entries(df_now)), len(entries(df_prev)), period_label),
        repeat_share_change(df_now, df_prev),
        peak_hour_shift(df_now, df_prev),
        identification_quality(df_now, identified_mask),
        quiet_period(df_now, work_hours, now),
    ]
    return [fact for fact in facts if fact]


def goal_progress(actual: int, daily_goal: int, period_days: float,
                  elapsed_share: float = 1.0) -> Optional[dict]:
    """План/факт по посещаемости. None, когда цель не задана.

    Цель задаётся на день, а периоды бывают разной длины, поэтому она
    пересчитывается на выбранный отрезок. Для незавершённого периода
    (`elapsed_share` < 1) отдельно считается темп: «отстаём» на середине дня
    и «отстаём» вечером — разные новости, и путать их нельзя.
    """
    if not daily_goal or daily_goal <= 0 or period_days <= 0:
        return None
    target = daily_goal * period_days
    share = actual / target if target else 0
    result = {
        "target": int(round(target)),
        "actual": actual,
        "share": share,
        "gap": int(round(target - actual)),
    }
    if 0 < elapsed_share < 1:
        expected_by_now = target * elapsed_share
        result["on_track"] = actual >= expected_by_now
        result["pace"] = actual / expected_by_now if expected_by_now else 0
        result["projected"] = int(round(actual / elapsed_share))
    else:
        result["on_track"] = actual >= target
        result["pace"] = share
        result["projected"] = actual
    return result


def describe_goal(progress: Optional[dict], period_label: str) -> Optional[str]:
    """Фраза о выполнении плана — то, что уходит в отчёт руководству."""
    if not progress:
        return None
    if progress["projected"] != progress["actual"]:
        # Период не закончился: судить надо по темпу, а не по факту.
        verdict = "идём с опережением" if progress["on_track"] else "отстаём от плана"
        return (f"План {progress['target']} за {period_label}: {verdict}, "
                f"сейчас {progress['actual']}, к концу ожидается ~{progress['projected']}.")
    if progress["gap"] <= 0:
        return (f"План {progress['target']} за {period_label} выполнен: "
                f"{progress['actual']} ({progress['share'] * 100:.0f}%).")
    return (f"План {progress['target']} за {period_label} не выполнен: "
            f"{progress['actual']} ({progress['share'] * 100:.0f}%), "
            f"не хватило {progress['gap']}.")


REPORT_STYLE = """
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
color:#16181d;background:#fff;margin:0;padding:40px;max-width:820px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 12px;
padding-bottom:6px;border-bottom:1px solid #e6e8ec}
.sub{color:#6b7280;margin-bottom:28px}
.kpis{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 8px}
.kpi{flex:1;min-width:150px;border:1px solid #e6e8ec;border-radius:10px;padding:14px 16px}
.kpi .v{font-size:26px;font-weight:600;letter-spacing:-.02em}
.kpi .k{color:#6b7280;font-size:13px;margin-top:2px}
ul{padding-left:18px}li{margin:6px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #eef0f3}
th{color:#6b7280;font-weight:500}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.foot{margin-top:36px;color:#9aa1ab;font-size:12px}
@media print{body{padding:0}}
"""


def build_report(store: str, period_label: str, generated_at: datetime,
                 kpis: List[tuple], summary: List[str],
                 hourly: List[tuple] = None, goal_line: str = None) -> str:
    """Отчёт одной страницей — то, что отправляют руководству.

    HTML, а не PDF: страница открывается на любом устройстве и печатается в
    PDF средствами браузера. Генерировать PDF на стороне дашборда значит
    тянуть ещё одну библиотеку в облачную сборку ради формата, который
    браузер и так умеет.
    """
    def esc(text):
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    kpi_html = "".join(
        f'<div class="kpi"><div class="v">{esc(value)}</div>'
        f'<div class="k">{esc(name)}</div></div>'
        for name, value in kpis
    )
    summary_html = "".join(f"<li>{esc(fact)}</li>" for fact in summary) or "<li>Нет данных</li>"

    hourly_html = ""
    if hourly:
        rows = "".join(
            f"<tr><td>{esc(hour)}</td><td class='n'>{esc(count)}</td></tr>"
            for hour, count in hourly
        )
        hourly_html = f"""<h2>По часам</h2>
<table><tr><th>Час</th><th style="text-align:right">Входов</th></tr>{rows}</table>"""

    goal_html = f"<h2>План и факт</h2><p>{esc(goal_line)}</p>" if goal_line else ""

    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Посещаемость · {esc(store)} · {esc(period_label)}</title>
<style>{REPORT_STYLE}</style></head><body>
<h1>Посещаемость · {esc(store)}</h1>
<div class="sub">{esc(period_label)} · отчёт составлен {generated_at:%d.%m.%Y %H:%M}</div>
<div class="kpis">{kpi_html}</div>
{goal_html}
<h2>Главное</h2>
<ul>{summary_html}</ul>
{hourly_html}
<div class="foot">Havas Pilot · подсчёт посетителей по видео.
Проходы без опознания учитываются в посещаемости, но исключены из метрик
уникальности — доля опознанных указана среди показателей выше.</div>
</body></html>"""


RETURN_WINDOW_MIN = 45


def count_returns(df: pd.DataFrame, window_min: int = RETURN_WINDOW_MIN) -> int:
    """Сколько входов — это возвраты того же человека, а не новые посетители.

    На точке, где вход и выход разные двери, событие OUT означает не уход из
    магазина, а выход через входную дверь: покурить, к машине, передумал. За
    таким выходом почти всегда следует вход того же человека, и в цифре
    «проходов» он считается дважды.

    Возврат опознаётся по личности: тот же visitor_id вошёл после того, как
    вышел, в пределах окна. Неопознанные проходы (`unknown-`) не считаются
    возвратами — про них ничего не известно, и записывать их в возвраты
    значило бы занижать посещаемость наугад.

    Число нужно не чтобы вычесть его молча, а чтобы показать рядом: «столько
    проходов, из них столько повторных заходов». Разница между проходами и
    людьми доходила на точке до пятой части.
    """
    if df.empty or "visitor_id" not in df:
        return 0
    known = df[~df["visitor_id"].astype(str).str.startswith("unknown-")]
    if known.empty:
        return 0

    ordered = known.sort_values("timestamp")
    returns = 0
    for _, events in ordered.groupby("visitor_id"):
        last_out = None
        for _, row in events.iterrows():
            if row["direction"] == "OUT":
                last_out = row["timestamp"]
            elif last_out is not None:
                gap = (row["timestamp"] - last_out).total_seconds() / 60
                if 0 <= gap <= window_min:
                    returns += 1
                last_out = None
    return returns


def visitors_estimate(entries_count: int, returns: int) -> dict:
    """Проходы, возвраты и оценка числа людей.

    Оценка, а не измерение: неопознанные проходы могли быть возвратами и
    остаться неучтёнными, поэтому людей не больше названного, но может быть
    меньше. Так и подписано.
    """
    people = max(entries_count - returns, 0)
    return {
        "passes": entries_count,
        "returns": returns,
        "people": people,
        "share": returns / entries_count if entries_count else 0,
    }


STAFF_MIN_PASSES = 5


def staff_candidates(df: pd.DataFrame, min_passes: int = STAFF_MIN_PASSES) -> dict:
    """Кто из опознанных похож на сотрудника, а не на покупателя.

    Отличаются они не внешностью, а частотой: покупатель проходит через дверь
    один-два раза за день, продавец — десятки. Этого достаточно, чтобы
    вычесть персонал из трафика, не прибегая к распознаванию лиц: частота
    прохода — не биометрия, согласия не требует и хранения в стране тоже.

    🚨 Это оценка, а не факт. В неё не попадёт сотрудник, которого система
    не опознала (у нас 8-16% проходов без личности), и может попасть
    курьер, охранник или очень занятой покупатель. Поэтому число
    показывается отдельной строкой, а не вычитается из трафика молча.
    """
    if df.empty or "visitor_id" not in df:
        return {"ids": [], "passes": 0}

    known = df[
        (df["direction"] == "IN")
        & ~df["visitor_id"].astype(str).str.startswith("unknown-")
    ]
    if known.empty:
        return {"ids": [], "passes": 0}

    counts = known.groupby("visitor_id").size()
    staff = counts[counts >= min_passes]
    return {
        "ids": list(staff.index),
        "passes": int(staff.sum()),
        "people": len(staff),
        "top": int(staff.max()) if len(staff) else 0,
    }


def traffic_breakdown(entries_count: int, returns: int, staff_passes: int) -> dict:
    """Из чего складывается показываемое число проходов.

    Три слоя, и каждый надо назвать отдельно: проходы (что измерено),
    возвраты (тот же человек вошёл дважды), персонал (не покупатель вовсе).
    Оценка посетителей — то, что остаётся, и она не может быть отрицательной.
    """
    visitors = max(entries_count - returns - staff_passes, 0)
    return {
        "passes": entries_count,
        "returns": returns,
        "staff": staff_passes,
        "visitors": visitors,
        "share_removed": (returns + staff_passes) / entries_count if entries_count else 0,
    }
