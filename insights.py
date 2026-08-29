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
