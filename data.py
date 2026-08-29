"""Доступ к данным дашборда: пагинация, кэш, единицы измерения.

Два дефекта, из-за которых этот слой появился отдельно от UI:

1. **Молчаливый срез на 1000 строк.** PostgREST в Supabase по умолчанию не
   отдаёт больше `db.max_rows` строк за запрос. Выборка визитов шла без
   пагинации и с сортировкой `timestamp desc` — то есть за месяц приходила
   свежая тысяча событий, а «Входов за месяц», тепловая карта и тренд
   считались по обрезанным данным. Никакой ошибки при этом не возникало:
   цифры просто были меньше правды.

2. **Семь сетевых запросов каждые 30 секунд на каждую открытую вкладку.**
   Ни одного кэша. Здесь всё закрыто `st.cache_data` с TTL.
"""

import os
import sys
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import config  # noqa: E402

TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
PAGE_SIZE = 1000          # шаг пагинации: столько PostgREST отдаёт за раз
MAX_ROWS = 200_000        # предохранитель: столько событий — это ~год работы магазина
CACHE_TTL_SEC = 60

# Демо-режим: HAVAS_DEMO=1 подставляет сгенерированные данные вместо Supabase.
# Нужен, чтобы смотреть на вёрстку и проверять выводы, не имея доступа к боевой
# базе, и чтобы показывать дашборд, не показывая чужую посещаемость.
DEMO = os.getenv("HAVAS_DEMO", "") == "1"

VISIT_COLUMNS = ["timestamp", "direction", "is_repeat", "visitor_id", "store"]


def empty_visits() -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
        "direction": pd.Series(dtype="object"),
        "is_repeat": pd.Series(dtype="bool"),
        "visitor_id": pd.Series(dtype="object"),
        "store": pd.Series(dtype="object"),
    })


def _demo_visits(since_local, until_local) -> pd.DataFrame:
    """Правдоподобная посещаемость: два пика в день, повторные, часть проходов без личности."""
    import numpy as np

    rng = np.random.default_rng(7)
    start = since_local or (datetime.now(TASHKENT_TZ) - timedelta(days=30))
    # Не выдумываем посещаемость в будущем: иначе «Сегодня» в 10 утра
    # показывает полный день, а сравнение с прошлым периодом разъезжается.
    end = min(until_local or datetime.now(TASHKENT_TZ), datetime.now(TASHKENT_TZ))
    rows = []
    regulars = [f"regular-{i}" for i in range(40)]

    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        weekday_factor = 1.35 if day.weekday() >= 5 else 1.0
        count = int(rng.normal(90, 15) * weekday_factor)
        for _ in range(max(count, 0)):
            hour = int(rng.choice(
                list(range(8, 24)),
                p=_hour_profile(),
            ))
            moment = day.replace(hour=hour, minute=int(rng.integers(0, 60)),
                                 second=int(rng.integers(0, 60)))
            if not (start <= moment <= end):
                continue
            unknown = rng.random() < 0.12
            repeat = (not unknown) and rng.random() < 0.34
            if unknown:
                visitor = f"{config.UNKNOWN_VISITOR_PREFIX}{rng.integers(0, 10**9)}"
            elif repeat:
                visitor = str(rng.choice(regulars))
            else:
                visitor = f"new-{rng.integers(0, 10**9)}"
            rows.append({"timestamp": moment, "direction": "IN", "is_repeat": repeat,
                         "visitor_id": visitor, "store": "demo_store"})
            if rng.random() < 0.85:
                exit_moment = moment + timedelta(minutes=int(rng.integers(4, 45)))
                if exit_moment <= end:
                    rows.append({"timestamp": exit_moment, "direction": "OUT", "is_repeat": repeat,
                                 "visitor_id": visitor, "store": "demo_store"})
        day += timedelta(days=1)

    if not rows:
        return empty_visits()
    df = pd.DataFrame(rows)
    return df.sort_values("timestamp", ascending=False).reset_index(drop=True)


def _hour_profile():
    """Профиль дня: утренний и вечерний пики, провал в середине."""
    weights = [3, 4, 6, 7, 9, 11, 10, 8, 7, 8, 11, 14, 15, 12, 8, 5]
    total = sum(weights)
    return [w / total for w in weights]


@st.cache_resource
def get_client():
    from supabase import create_client
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def _fetch_all_pages(query_builder, order_column: str = "timestamp") -> list:
    """Выбрать все строки постранично.

    query_builder — функция (offset, limit) → запрос. Идём по страницам, пока
    страница возвращается полной; последняя неполная означает конец.
    """
    rows = []
    offset = 0
    while offset < MAX_ROWS:
        page = query_builder(offset, PAGE_SIZE).execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_visits(
    since_local: Optional[datetime],
    until_local: Optional[datetime],
    store: str,
) -> pd.DataFrame:
    """Визиты за период в местном времени магазина.

    Supabase хранит время в UTC, поэтому границы переводятся в UTC только для
    запроса — всё, что ниже по течению, работает в ташкентском времени.
    """
    if DEMO:
        return _demo_visits(since_local, until_local)

    client = get_client()

    def build(offset: int, limit: int):
        q = (
            client.table("visits")
            .select(",".join(VISIT_COLUMNS))
            .eq("store", store)
            .order("timestamp", desc=False)
            .range(offset, offset + limit - 1)
        )
        if since_local is not None:
            q = q.gte("timestamp", since_local.astimezone(timezone.utc).isoformat())
        if until_local is not None:
            q = q.lte("timestamp", until_local.astimezone(timezone.utc).isoformat())
        return q

    rows = _fetch_all_pages(build)
    if not rows:
        return empty_visits()

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(TASHKENT_TZ)
    return df.sort_values("timestamp", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_heartbeat(store: str) -> Optional[dict]:
    if DEMO:
        return {
            "store": store, "last_seen": datetime.now(timezone.utc).isoformat(),
            "status": "ok", "fps": 4.2, "pending_events": 0, "dead_events": 0,
            "version": "demo", "seconds_since_frame": 0.3,
        }
    try:
        result = get_client().table("heartbeat").select("*").eq("store", store).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        st.error(f"Ошибка получения heartbeat: {e}")
        return None


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_incidents(store: str, limit: int = 50) -> list:
    if DEMO:
        now = datetime.now(timezone.utc)
        return [
            {"started_at": (now - timedelta(days=2, hours=3)).isoformat(),
             "ended_at": (now - timedelta(days=2, hours=2)).isoformat(),
             "duration_min": 63, "type": "camera"},
            {"started_at": (now - timedelta(days=5)).isoformat(),
             "ended_at": (now - timedelta(days=5) + timedelta(minutes=18)).isoformat(),
             "duration_min": 18, "type": "pipeline"},
        ]
    try:
        result = (
            get_client().table("incidents")
            .select("*")
            .eq("store", store)
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        st.error(f"Ошибка получения инцидентов: {e}")
        return []


@st.cache_data(ttl=300, show_spinner=False)
def fetch_stores() -> list:
    """Список магазинов — основа для мультистор-режима.

    Берётся из heartbeat: каждая точка пишет туда свою строку, так что список
    появляется сам собой по мере подключения магазинов к сети.
    """
    if DEMO:
        return ["demo_store", "havas_chilanzar", "havas_yunusabad"]
    try:
        rows = get_client().table("heartbeat").select("store").execute().data or []
        stores = sorted({row["store"] for row in rows if row.get("store")})
        return stores or [config.STORE_NAME]
    except Exception:
        return [config.STORE_NAME]


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_network_overview(since_local: datetime, until_local: datetime) -> pd.DataFrame:
    """Посещаемость и статус по всем магазинам сразу — строка на точку.

    Переключаться между магазинами по одному годится для пилота; сети нужен
    ответ на вопрос «где сегодня просело и где что-то сломалось» одним
    взглядом, без перебора.
    """
    stores = fetch_stores()
    rows = []
    for store in stores:
        visits = fetch_visits(since_local, until_local, store)
        entries = visits[visits["direction"] == "IN"] if not visits.empty else visits
        identified = entries[is_identified(entries["visitor_id"])] if len(entries) else entries
        heartbeat = fetch_heartbeat(store) or {}

        last_seen = heartbeat.get("last_seen")
        age_min = None
        if last_seen:
            age_min = (
                datetime.now(timezone.utc) - pd.to_datetime(last_seen, utc=True)
            ).total_seconds() / 60

        if age_min is None or age_min > 10:
            status = "down"
        elif heartbeat.get("status") in ("camera_down", "stalled"):
            status = heartbeat["status"]
        else:
            status = "ok"

        rows.append({
            "store": store,
            "entries": len(entries),
            "repeat_pct": (identified["is_repeat"].mean() * 100) if len(identified) else 0.0,
            "identified_pct": (len(identified) / len(entries) * 100) if len(entries) else 0.0,
            "status": status,
            "fps": heartbeat.get("fps"),
            "pending": heartbeat.get("pending_events") or 0,
            "version": heartbeat.get("version"),
        })
    return pd.DataFrame(rows).sort_values("entries", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_devices() -> pd.DataFrame:
    """Реестр точек: что где стоит, какая версия и когда отзывалось.

    Появляется после migrations/003_devices_and_commands.sql; до неё таблицы
    нет, и раздел просто не показывается.
    """
    if DEMO:
        now = datetime.now(timezone.utc)
        return pd.DataFrame([
            {"device_id": "a1b2c3d4-demo", "store": "demo_store", "hostname": "havas-pc-01",
             "platform": "windows", "version": "b63ecc9",
             "last_seen": (now - timedelta(seconds=20)).isoformat(), "status": "ok",
             "config": {"line_position": 0.55, "entrance_roi": [[0.2, 0.1], [0.8, 0.1], [0.9, 0.95], [0.1, 0.95]]}},
            {"device_id": "e5f6a7b8-demo", "store": "havas_chilanzar", "hostname": "havas-pc-02",
             "platform": "windows", "version": "b63ecc9",
             "last_seen": (now - timedelta(minutes=3)).isoformat(), "status": "camera_down",
             "config": {}},
            {"device_id": "c9d0e1f2-demo", "store": "havas_yunusabad", "hostname": "havas-nuc-03",
             "platform": "linux", "version": "3eff829",
             "last_seen": (now - timedelta(hours=5)).isoformat(), "status": "ok", "config": {}},
        ])
    try:
        rows = get_client().table("devices").select("*").order("store").execute().data or []
        return pd.DataFrame(rows)
    except Exception:
        # Таблицы ещё нет — это не ошибка, а «миграция 003 не применена».
        return pd.DataFrame()


def _demo_hourly(since_local, until_local) -> pd.DataFrame:
    visits = _demo_visits(since_local, until_local)
    return aggregate_hourly(visits)


def aggregate_hourly(visits: pd.DataFrame) -> pd.DataFrame:
    """Свернуть сырые события в почасовые строки — тот же формат, что у view.

    Держится рядом с запросом намеренно: это тот самый расчёт, который делает
    представление в базе, и когда представление недоступно, дашборд не должен
    получать данные другой формы.
    """
    if visits.empty:
        return pd.DataFrame(columns=[
            "store", "hour_local", "entries", "exits", "repeat_entries", "identified_entries",
        ])
    df = visits.copy()
    df["hour_local"] = df["timestamp"].dt.floor("h")
    df["is_in"] = df["direction"] == "IN"
    df["identified"] = is_identified(df["visitor_id"])
    grouped = df.groupby(["store", "hour_local"], as_index=False).agg(
        entries=("is_in", "sum"),
        exits=("is_in", lambda values: int((~values).sum())),
        repeat_entries=("is_repeat", lambda values: 0),
    )
    # Повторные и опознанные считаются только среди входов, поэтому отдельным
    # проходом: агрегировать их одной строкой с exits нельзя.
    entries_only = df[df["is_in"]]
    extra = entries_only.groupby(["store", "hour_local"], as_index=False).agg(
        repeat_entries=("is_repeat", "sum"),
        identified_entries=("identified", "sum"),
    )
    result = grouped.drop(columns=["repeat_entries"]).merge(
        extra, on=["store", "hour_local"], how="left"
    ).fillna({"repeat_entries": 0, "identified_entries": 0})
    for column in ("entries", "exits", "repeat_entries", "identified_entries"):
        result[column] = result[column].astype(int)
    return result


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_hourly_stats(
    since_local: datetime, until_local: datetime, store: str
) -> pd.DataFrame:
    """Почасовая посещаемость: из представления, иначе из сырых событий.

    Представление появляется после migrations/005_hourly_stats.sql. До неё
    дашборд работает ровно как раньше — просто медленнее на больших periodах.
    """
    if DEMO:
        return _demo_hourly(since_local, until_local)

    try:
        rows = (
            get_client().table("hourly_stats")
            .select("*")
            .eq("store", store)
            .gte("hour_local", since_local.astimezone(timezone.utc).isoformat())
            .lte("hour_local", until_local.astimezone(timezone.utc).isoformat())
            .order("hour_local")
            .execute().data
        ) or []
        if rows:
            df = pd.DataFrame(rows)
            # В представлении час уже местный, но приходит без зоны —
            # обозначаем её явно, иначе сравнения с now() разъедутся.
            df["hour_local"] = pd.to_datetime(df["hour_local"]).dt.tz_localize(
                TASHKENT_TZ, nonexistent="shift_forward", ambiguous=False
            )
            return df
        return aggregate_hourly(empty_visits())
    except Exception:
        # Представления нет (миграция не применена) или запрос не прошёл —
        # считаем на клиенте по сырым событиям.
        return aggregate_hourly(fetch_visits(since_local, until_local, store))


def day_bounds(d: date) -> Tuple[datetime, datetime]:
    """Границы календарных суток в местном времени."""
    start = datetime.combine(d, datetime.min.time(), tzinfo=TASHKENT_TZ)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start, end


def is_identified(visitor_ids: pd.Series) -> pd.Series:
    """Маска «проход опознан».

    Проходы, для которых ReID не смог получить пригодный кадр, пишутся с
    префиксом `unknown-` и уникальным id: посещаемость они формируют честно,
    но в метриках уникальности и повторности их учитывать нельзя — иначе
    каждый такой проход выглядит как новый уникальный человек.
    """
    return ~visitor_ids.fillna("").str.startswith(config.UNKNOWN_VISITOR_PREFIX)
