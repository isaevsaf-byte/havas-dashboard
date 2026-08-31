"""Havas Analytics — дашборд посещаемости.

Структура: data.py — выборки с пагинацией и кэшем, insights.py — выводы по
данным, theme.py — палитра и карточки, здесь — сборка страницы.

Порядок блоков отражает то, как на дашборд смотрят: сначала «всё ли работает»,
потом «что изменилось» словами, потом главная цифра, и только затем графики и
детали.
"""

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from html import escape

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

def secret(key: str, default: str = "") -> str:
    """Значение из st.secrets, если секреты вообще настроены.

    Обращение к st.secrets на машине без secrets.toml бросает
    StreamlitSecretNotFoundError — не пустое значение, а исключение, которое
    роняет страницу целиком ещё до первой отрисовки. В облаке файл есть всегда,
    поэтому дефект не был виден, а локальный запуск был невозможен.
    """
    try:
        return st.secrets.get(key, default)
    except Exception:
        return os.getenv(key, default)


for _key in ("SUPABASE_URL", "SUPABASE_KEY"):
    _value = secret(_key)
    if _value:
        os.environ[_key] = _value

import config  # noqa: E402
import data as dashboard_data  # noqa: E402
import insights  # noqa: E402
import theme  # noqa: E402
from data import TASHKENT_TZ, day_bounds, is_identified  # noqa: E402

WORK_HOURS = list(range(8, 24))  # 08:00–23:59, часы работы магазина

st.set_page_config(page_title="Havas Analytics", layout="wide", page_icon="🛒")

palette = theme.Palette(theme.is_dark())
theme.inject_css(palette)

if not config.SUPABASE_URL and not dashboard_data.DEMO:
    st.warning("Настройте Supabase в config.py (или запустите с HAVAS_DEMO=1 для демо-данных)")
    st.stop()

# --- Доступ ---------------------------------------------------------------
# Пароль включается наличием ключа в st.secrets: без него дашборд открыт, как
# и раньше, а на бою достаточно добавить секрет, не трогая код.
DASHBOARD_PASSWORD = secret("DASHBOARD_PASSWORD")
if DASHBOARD_PASSWORD:
    if not st.session_state.get("authenticated"):
        st.markdown("### Havas Analytics")
        entered = st.text_input("Пароль", type="password")
        if entered and entered == DASHBOARD_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        elif entered:
            st.error("Неверный пароль")
        st.stop()

# --- Шапка и выбор магазина ------------------------------------------------
stores = dashboard_data.fetch_stores()
header_left, header_right = st.columns([3, 1])
with header_left:
    st.markdown('<h1 id="havas-analytics">Havas Analytics</h1>', unsafe_allow_html=True)
with header_right:
    if len(stores) > 1:
        store = st.selectbox(
            "Магазин", stores,
            index=stores.index(config.STORE_NAME) if config.STORE_NAME in stores else 0,
        )
    else:
        store = stores[0]
st.markdown(f'<div class="havas-subtitle">Учёт посетителей · {store}</div>', unsafe_allow_html=True)


def format_duration(minutes):
    if minutes is None:
        return "ещё длится"
    if minutes < 60:
        return f"{minutes} мин"
    hours, mins = divmod(minutes, 60)
    return f"{hours} ч {mins} мин"


# --- Статус системы --------------------------------------------------------
heartbeat = dashboard_data.fetch_heartbeat(store)
if heartbeat is None:
    theme.status_banner("⚪", "Нет данных от системы", palette.text_muted, palette)
else:
    last_seen = pd.to_datetime(heartbeat["last_seen"], utc=True)
    age = datetime.now(timezone.utc) - last_seen
    last_seen_local = last_seen.tz_convert(TASHKENT_TZ)
    if age < timedelta(minutes=10):
        status = heartbeat.get("status")
        if status == "camera_down":
            theme.status_banner("🟡", "Сервис работает, но камера недоступна", palette.warning, palette)
        elif status == "stalled":
            # Худший из отказов: всё «зелёное», а посетители не считаются.
            idle = heartbeat.get("seconds_since_frame")
            idle_text = f" — {round(idle / 60)} мин без кадров" if idle else ""
            theme.status_banner(
                "🟠", f"Сервис работает, но НЕ считает посетителей{idle_text}",
                palette.critical, palette,
            )
        else:
            theme.status_banner("🟢", "Система работает", palette.good, palette)

        # Показатели живости пайплайна появляются после
        # migrations/002_heartbeat_health.sql — до неё колонок просто нет.
        health_bits = []
        if heartbeat.get("fps") is not None:
            health_bits.append(f"обработка {heartbeat['fps']:.1f} кадр/с")
        if heartbeat.get("pending_events"):
            health_bits.append(f"не отправлено событий: {heartbeat['pending_events']}")
        if heartbeat.get("dead_events"):
            health_bits.append(f"🚨 отброшено безвозвратно: {heartbeat['dead_events']}")
        if heartbeat.get("version"):
            health_bits.append(f"версия {heartbeat['version']}")
        if health_bits:
            st.caption(" · ".join(health_bits))
    else:
        theme.status_banner(
            "🔴",
            f"Система не отвечает — последний сигнал: {last_seen_local.strftime('%d.%m.%Y %H:%M')}",
            palette.critical, palette,
        )

# --- Период ----------------------------------------------------------------
today_local = datetime.now(TASHKENT_TZ).date()
period_choice = st.radio(
    "Период", ["Сегодня", "Вчера", "Неделя", "Месяц", "Свой период"], horizontal=True
)

if period_choice == "Сегодня":
    period_start, period_end = day_bounds(today_local)
    hourly_mode = True
elif period_choice == "Вчера":
    period_start, period_end = day_bounds(today_local - timedelta(days=1))
    hourly_mode = True
elif period_choice == "Неделя":
    period_start, _ = day_bounds(today_local - timedelta(days=6))
    period_end = datetime.now(TASHKENT_TZ)
    hourly_mode = False
elif period_choice == "Месяц":
    period_start, _ = day_bounds(today_local - timedelta(days=29))
    period_end = datetime.now(TASHKENT_TZ)
    hourly_mode = False
else:
    picked = st.date_input(
        "Диапазон дат",
        value=(today_local - timedelta(days=6), today_local),
        max_value=today_local,
    )
    if isinstance(picked, tuple) and len(picked) == 2:
        period_start, _ = day_bounds(picked[0])
        _, period_end = day_bounds(picked[1])
    else:
        period_start, period_end = day_bounds(today_local)
    hourly_mode = period_start.date() == period_end.date()

df_period = dashboard_data.fetch_visits(period_start, period_end, store)
df_in = insights.entries(df_period)

# Сравнение с предыдущим периодом — по тому же отрезку времени, а не с целым
# прошлым периодом: иначе незавершённое «Сегодня» всегда проигрывает полному
# «Вчера» и выглядит падением.
now_local = datetime.now(TASHKENT_TZ)
effective_end = min(now_local, period_end)
elapsed = effective_end - period_start
nominal_length = period_end - period_start
prev_start = period_start - nominal_length
prev_end = prev_start + elapsed
df_prev = dashboard_data.fetch_visits(prev_start, prev_end, store)
df_prev_in = insights.entries(df_prev)

# Фиксированное окно 30 дней, независимое от выбора периода — тепловой карте,
# тренду и прогнозу нужна стабильная история.
window_start = datetime.now(TASHKENT_TZ) - timedelta(days=30)
window_end = datetime.now(TASHKENT_TZ)
# Сырые события нужны только там, где требуется группировка по посетителю
# (глубина визитов, прогноз). Всё остальное считается из агрегатов.
df_30 = dashboard_data.fetch_visits(window_start, window_end, store)
hourly_30 = dashboard_data.fetch_hourly_stats(window_start, window_end, store)

# Гранулярность: час для одного дня, день до двух недель, дальше — неделя.
# 30 столбиков за месяц нечитаемы и не отвечают ни на один вопрос владельца.
period_span_days = (period_end.date() - period_start.date()).days
if hourly_mode:
    granularity = "hour"
elif period_span_days <= 14:
    granularity = "day"
else:
    granularity = "week"

# --- Выводы словами --------------------------------------------------------
period_label = {
    "Сегодня": "вчера в это же время",
    "Вчера": "позавчера",
    "Неделя": "неделей раньше",
    "Месяц": "месяцем раньше",
}.get(period_choice, "в прошлом периоде")

identified_mask = (
    is_identified(df_period["visitor_id"]) if not df_period.empty else pd.Series(dtype=bool)
)
summary_facts = insights.build_summary(
    df_period, df_prev, identified_mask, period_label, now_local, range(8, 24)
)
theme.summary_block(summary_facts, palette)

# --- KPI -------------------------------------------------------------------
total_in = len(df_in)
identified_in = df_in[is_identified(df_in["visitor_id"])] if total_in else df_in
new_count = int((~identified_in["is_repeat"]).sum()) if len(identified_in) else 0
repeat_count = int(identified_in["is_repeat"].sum()) if len(identified_in) else 0
identified_total = new_count + repeat_count
new_pct = (new_count / identified_total * 100) if identified_total else 0
repeat_pct = (repeat_count / identified_total * 100) if identified_total else 0
unknown_count = total_in - identified_total

delta_pct = insights.change_pct(total_in, len(df_prev_in))

hero_col, side_col = st.columns([1.4, 2])
with hero_col:
    forecast = (
        insights.forecast_day_total(df_period, df_30, now_local)
        if period_choice == "Сегодня" else None
    )
    # «Проходов», а не «посетителей»: человек, вышедший покурить через
    # входную дверь и вернувшийся, даёт два прохода. На точке таких выходов
    # 44 при 234 входах — расхождение между цифрой и числом людей доходит до
    # пятой части. Проходы реальны, но подпись не должна обещать большего.
    # Возвраты опознаются по личности, а не по числу событий OUT: человек
    # мог выйти и не вернуться, а неопознанный проход про себя ничего не
    # говорит. Считаем только те входы, где тот же visitor_id вошёл вскоре
    # после того, как вышел.
    returns = insights.count_returns(df_period)
    # Персонал отличается от покупателей не внешностью, а частотой: продавец
    # проходит через дверь десятки раз за день, покупатель один-два. Этого
    # достаточно, чтобы вычесть его из трафика без распознавания лиц —
    # частота прохода не биометрия и не требует ни согласия, ни хранения
    # данных внутри страны.
    staff = insights.staff_candidates(df_period)
    breakdown = insights.traffic_breakdown(total_in, returns, staff["passes"])

    hints = []
    if forecast:
        hints.append(f"Прогноз до конца дня: ~{forecast}")
    if returns or staff["passes"]:
        parts = []
        if returns:
            parts.append(f"{returns} возвратов")
        if staff["passes"]:
            parts.append(f"{staff['passes']} проходов персонала")
        hints.append(f"из них {', '.join(parts)} · посетителей ≈ {breakdown['visitors']}")

    theme.hero_metric(
        f"Проходов внутрь · {period_choice.lower()}", str(total_in), palette,
        delta=f"{delta_pct:+.0f}% vs {period_label}" if delta_pct is not None else None,
        delta_positive=delta_pct is not None and delta_pct >= 0,
        hint=" · ".join(hints) if hints else None,
    )

    if breakdown["share_removed"] >= 0.1:
        # Десятая часть и больше — уже не мелочь: показываем разбивку явно,
        # иначе цифра «проходов» читается как «посетителей».
        theme.traffic_note(breakdown, staff, palette)

with side_col:
    k1, k2, k3 = st.columns(3)
    with k1:
        theme.metric_card(
            "Новые", f"{new_count} ({new_pct:.0f}%)", palette,
            hint="от опознанных проходов" if unknown_count else None,
        )
    with k2:
        theme.metric_card(
            "Повторные", f"{repeat_count} ({repeat_pct:.0f}%)", palette,
            hint="от опознанных проходов" if unknown_count else None,
        )
    with k3:
        # Качество данных на виду: без этой цифры низкая доля повторных
        # читается как поведение покупателей, хотя может быть отказом ReID.
        identified_share = (identified_total / total_in * 100) if total_in else 0
        theme.metric_card(
            "Опознано проходов", f"{identified_share:.0f}%", palette,
            hint=f"{unknown_count} без личности" if unknown_count else "все проходы с личностью",
        )

# --- План и факт -----------------------------------------------------------
# Цель задаётся секретом DAILY_GOAL и пересчитывается на выбранный период:
# держать её в коде значило бы править дашборд ради каждой новой цифры.
# Для незавершённого дня судим по темпу, а не по факту — иначе утром любой
# план выглядит проваленным.
period_days = max((period_end - period_start).total_seconds() / 86400, 0.01)
elapsed_share = 1.0
if period_choice == "Сегодня":
    day_start, day_end = day_bounds(today_local)
    elapsed_share = min(
        max((datetime.now(TASHKENT_TZ) - day_start).total_seconds()
            / (day_end - day_start).total_seconds(), 0.01), 1.0,
    )

try:
    daily_goal = int(secret("DAILY_GOAL", "0") or 0)
except ValueError:
    daily_goal = 0
goal = insights.goal_progress(total_in, daily_goal, period_days, elapsed_share)
goal_line = insights.describe_goal(goal, period_choice.lower())

if goal:
    theme.goal_bar(goal, goal_line, palette)

# --- Сводка по сети --------------------------------------------------------
# Показывается только когда магазинов больше одного: на пилоте из одной точки
# это была бы строка, дублирующая всё, что уже сказано выше.
if len(stores) > 1:
    st.subheader("Сеть магазинов")
    network = dashboard_data.fetch_network_overview(period_start, period_end)
    problem_stores = network[network["status"] != "ok"]
    if len(problem_stores):
        theme.status_banner(
            "🔴", f"Точек с проблемами: {len(problem_stores)} из {len(network)}",
            palette.critical, palette,
        )
    for _, row in network.iterrows():
        theme.store_row(row, palette, is_current=(row["store"] == store))
    st.caption(
        f"→ всего по сети {int(network['entries'].sum())} входов за период. "
        f"Клик по магазину вверху страницы переключает детальный разбор."
    )
    st.divider()

st.divider()

# --- Динамика + сравнение с прошлым периодом -------------------------------
col_left, col_right = st.columns(2)

with col_left:
    if df_in.empty:
        st.subheader("Входы по времени")
        st.info("Нет данных за этот период")
    elif granularity == "hour":
        st.subheader("Входы по часам")
        df_hourly = df_in.copy()
        df_hourly["hour"] = df_hourly["timestamp"].dt.hour
        df_hourly["Тип"] = df_hourly["is_repeat"].map({False: "Новые", True: "Повторные"})
        hourly = df_hourly.groupby(["hour", "Тип"]).size().reset_index(name="count")
        all_combos = pd.MultiIndex.from_product(
            [WORK_HOURS, ["Новые", "Повторные"]], names=["hour", "Тип"]
        ).to_frame(index=False)
        hourly = all_combos.merge(hourly, on=["hour", "Тип"], how="left").fillna(0)
        fig = px.bar(hourly, x="hour", y="count", color="Тип",
                     labels={"hour": "Час", "count": "Входов"},
                     color_discrete_map={"Новые": palette.new, "Повторные": palette.repeat})
        # Прошлый период тем же разрезом — линией поверх столбцов. Одна дельта
        # в карточке говорит «меньше», но не отвечает «в какие часы меньше».
        if not df_prev_in.empty:
            prev_hourly = df_prev_in.groupby(df_prev_in["timestamp"].dt.hour).size()
            fig.add_scatter(
                x=WORK_HOURS, y=[int(prev_hourly.get(h, 0)) for h in WORK_HOURS],
                mode="lines", name=period_label,
                line=dict(color=palette.text_muted, width=2, dash="dot"),
            )
        fig.update_xaxes(dtick=1)
        st.plotly_chart(theme.style_fig(fig, palette), width="stretch")
        st.caption(f"→ пик в {df_hourly.groupby('hour').size().idxmax()}:00")
    elif granularity == "day":
        st.subheader("Входы по дням")
        df_daily = df_in.copy()
        df_daily["date"] = df_daily["timestamp"].dt.date
        df_daily["Тип"] = df_daily["is_repeat"].map({False: "Новые", True: "Повторные"})
        daily = df_daily.groupby(["date", "Тип"]).size().reset_index(name="count")
        fig = px.bar(daily, x="date", y="count", color="Тип",
                     labels={"date": "Дата", "count": "Входов"},
                     color_discrete_map={"Новые": palette.new, "Повторные": palette.repeat})
        st.plotly_chart(theme.style_fig(fig, palette), width="stretch")
        by_day = df_daily.groupby("date").size()
        st.caption(f"→ лучший день — {by_day.idxmax().strftime('%d.%m')} ({by_day.max()} входов)")
    else:
        st.subheader("Входы по неделям")
        df_weekly = df_in.copy()
        # Одно определение недели на весь дашборд (пн–вс). Раньше здесь была
        # W-MON, а в тренде — %U, и недели на двух графиках не совпадали.
        df_weekly["week_start"] = df_weekly["timestamp"].dt.to_period("W-MON").dt.start_time.dt.date
        df_weekly["Тип"] = df_weekly["is_repeat"].map({False: "Новые", True: "Повторные"})
        weekly = df_weekly.groupby(["week_start", "Тип"]).size().reset_index(name="count")
        fig = px.bar(weekly, x="week_start", y="count", color="Тип",
                     labels={"week_start": "Неделя с", "count": "Входов"},
                     color_discrete_map={"Новые": palette.new, "Повторные": palette.repeat})
        st.plotly_chart(theme.style_fig(fig, palette), width="stretch")
        by_week = df_weekly.groupby("week_start").size()
        trend_arrow = "↑" if len(by_week) >= 2 and by_week.iloc[-1] >= by_week.iloc[-2] else "↓"
        st.caption(
            f"→ лучшая неделя — с {by_week.idxmax().strftime('%d.%m')} "
            f"({by_week.max()} входов), последняя неделя {trend_arrow}"
        )

with col_right:
    st.subheader("Глубина повторных визитов")
    if hourly_mode:
        # За один день почти каждый — «1 визит» независимо от лояльности.
        st.info("За 1 день почти все заходят один раз — это не про лояльность. "
                "Смотрите на Неделе или Месяце.")
    elif len(identified_in):
        visit_counts = identified_in.groupby("visitor_id").size()

        def _bucket(n):
            if n == 1:
                return "1 визит"
            if n == 2:
                return "2 визита"
            if n <= 5:
                return "3-5 визитов"
            return "6+ визитов"

        buckets_order = ["1 визит", "2 визита", "3-5 визитов", "6+ визитов"]
        bucket_counts = (
            visit_counts.map(_bucket).value_counts()
            .reindex(buckets_order, fill_value=0).reset_index()
        )
        bucket_counts.columns = ["Визитов", "Людей"]
        fig_depth = px.bar(bucket_counts, x="Визитов", y="Людей",
                           color="Визитов", color_discrete_sequence=palette.ordinal)
        fig_depth.update_layout(showlegend=False, xaxis_title=None)
        st.plotly_chart(theme.style_fig(fig_depth, palette), width="stretch")
        loyal_pct = 100 * int((visit_counts >= 3).sum()) / len(visit_counts)
        one_time_pct = 100 * int((visit_counts == 1).sum()) / len(visit_counts)
        st.caption(
            f"→ {loyal_pct:.0f}% — постоянные (3+ визита), {one_time_pct:.0f}% зашли один раз. "
            f"Считается только по опознанным проходам."
        )
    else:
        st.info("Нет опознанных проходов за этот период")

st.divider()

# --- Тепловая карта --------------------------------------------------------
st.subheader("Тепловая карта загруженности (последние 30 дней)")
weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
weekday_ru = {"Monday": "Пн", "Tuesday": "Вт", "Wednesday": "Ср", "Thursday": "Чт",
              "Friday": "Пт", "Saturday": "Сб", "Sunday": "Вс"}
if not hourly_30.empty:
    # Тепловая карта строится из почасовых агрегатов, а не из сырых событий:
    # это те же числа, но ~480 строк вместо десятков тысяч.
    df_hm = hourly_30.copy()
    df_hm["weekday"] = df_hm["hour_local"].dt.day_name()
    df_hm["hour"] = df_hm["hour_local"].dt.hour
    heat = df_hm.groupby(["weekday", "hour"], as_index=False)["entries"].sum()
    heat = heat.rename(columns={"entries": "count"})
    pivot = heat.pivot(index="weekday", columns="hour", values="count").reindex(
        index=weekday_order, columns=WORK_HOURS
    ).fillna(0)
    pivot.index = [weekday_ru[d] for d in pivot.index]
    pivot = pivot.loc[(pivot != 0).any(axis=1), :]
    pivot_display = pivot.replace(0, np.nan)  # пустая клетка вместо сплошного цвета для нуля
    fig_heat = px.imshow(pivot_display, labels=dict(x="Час", y="День недели", color="Входов"),
                         color_continuous_scale=palette.sequential,
                         range_color=(0, pivot.values.max()), aspect="auto", text_auto=True)
    st.plotly_chart(theme.style_fig(fig_heat, palette, height=400), width="stretch")
    peak_cell = pivot.stack().idxmax()
    st.caption(f"→ самое загруженное время — {peak_cell[0]}, {peak_cell[1]}:00. "
               f"Полезно для планирования смен.")
else:
    st.info("Нет данных за последние 30 дней")

st.divider()

# --- Тренд повторных -------------------------------------------------------
st.subheader("Доля повторных по неделям (30 дней)")
if hourly_30.empty or not hourly_30["identified_entries"].sum():
    st.info("Нет опознанных проходов за последние 30 дней")
else:
    # Доля повторных считается от опознанных проходов: события без личности
    # (unknown-*) — это не «новые посетители», а отсутствие данных.
    trend_source = hourly_30.copy()
    trend_source["week"] = (
        trend_source["hour_local"].dt.to_period("W-MON").dt.start_time.dt.date
    )
    weekly_trend = trend_source.groupby("week", as_index=False)[
        ["repeat_entries", "identified_entries"]
    ].sum()
    weekly_trend = weekly_trend[weekly_trend["identified_entries"] > 0]
    weekly_trend["repeat_pct"] = (
        weekly_trend["repeat_entries"] / weekly_trend["identified_entries"] * 100
    )

    if len(weekly_trend) < 2:
        st.info("Пока только одна неделя данных — тренд появится, "
                "когда накопится история за несколько недель")
    else:
        fig_trend = px.line(weekly_trend, x="week", y="repeat_pct",
                            labels={"week": "Неделя с", "repeat_pct": "% повторных"},
                            markers=True, color_discrete_sequence=[palette.trend])
        fig_trend.update_traces(line=dict(width=2), marker=dict(size=8))
        st.plotly_chart(theme.style_fig(fig_trend, palette), width="stretch")
        change = weekly_trend["repeat_pct"].iloc[-1] - weekly_trend["repeat_pct"].iloc[-2]
        direction = "выросла" if change >= 0 else "упала"
        st.caption(f"→ доля повторных за последнюю неделю {direction} на {abs(change):.0f} п.п.")

st.divider()

# --- Надёжность ------------------------------------------------------------
TYPE_LABEL = {
    "camera": "📷 камера",
    "service": "🖥️ сервис/интернет",
    "pipeline": "⚙️ обработка встала",
}
REPORT_WINDOW_DAYS = 7


def compute_incident_report(incidents, window_days=REPORT_WINDOW_DAYS):
    """Uptime и простой по причинам за фиксированное окно.

    Инцидент обрезается окном: начавшийся раньше, но закончившийся внутри,
    считается только пересечением, а не всей длиной.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    window_total_min = window_days * 24 * 60
    by_type = defaultdict(lambda: {"count": 0, "minutes": 0.0})
    for inc in incidents:
        started = pd.to_datetime(inc["started_at"], utc=True).to_pydatetime()
        ended = (pd.to_datetime(inc["ended_at"], utc=True).to_pydatetime()
                 if inc.get("ended_at") else now)
        if ended < cutoff:
            continue
        minutes = (ended - max(started, cutoff)).total_seconds() / 60
        if minutes <= 0:
            continue
        kind = inc.get("type") or "unknown"
        by_type[kind]["count"] += 1
        by_type[kind]["minutes"] += minutes
    total_down = sum(v["minutes"] for v in by_type.values())
    return 100 * (1 - total_down / window_total_min), total_down, by_type


incidents = dashboard_data.fetch_incidents(store)
with st.expander("🛡️ Надёжность системы"):
    if incidents:
        uptime_pct, total_down_min, by_type = compute_incident_report(incidents)
        r1, r2, r3 = st.columns(3)
        with r1:
            theme.metric_card(f"Uptime ({REPORT_WINDOW_DAYS} дней)", f"{uptime_pct:.1f}%", palette)
        with r2:
            theme.metric_card(
                "Простой всего",
                format_duration(round(total_down_min)) if total_down_min else "0 мин", palette,
            )
        with r3:
            rows = [
                (TYPE_LABEL.get(t, t), f'{format_duration(round(v["minutes"]))} ({v["count"]})')
                for t, v in sorted(by_type.items(), key=lambda kv: -kv[1]["minutes"])
            ]
            theme.breakdown_card(f"Простой по причине ({REPORT_WINDOW_DAYS} дней)", rows, palette)

        st.markdown("**История простоев**")
        # Одна строка HTML на запись: парсер Markdown в Streamlit принимает
        # многострочный f-string с отступом за блок кода.
        rows_html = []
        for inc in incidents[:20]:
            started = pd.to_datetime(inc["started_at"], utc=True).tz_convert(TASHKENT_TZ)
            duration = format_duration(inc.get("duration_min"))
            type_label = TYPE_LABEL.get(inc.get("type"), inc.get("type") or "неизвестно")
            ongoing = inc.get("ended_at") is None
            accent = palette.critical if ongoing else palette.text_muted
            status_text = "идёт сейчас" if ongoing else f"простой {duration}"
            rows_html.append(
                f'<div style="display:grid;grid-template-columns:150px 1fr auto;align-items:center;'
                f'gap:12px;padding:10px 14px;margin-bottom:6px;border-radius:8px;'
                f'background:{palette.surface};border-left:3px solid {accent}">'
                f'<span style="color:{palette.text_primary};font-weight:600;font-size:14px;'
                f'font-variant-numeric:tabular-nums">{started.strftime("%d.%m.%Y %H:%M")}</span>'
                f'<span style="color:{palette.text_secondary};font-size:13px">{status_text}</span>'
                f'<span style="background:{palette.rgba(palette.text_muted, 0.12)};'
                f'color:{palette.text_secondary};padding:3px 10px;border-radius:12px;font-size:12px;'
                f'font-weight:600;white-space:nowrap;justify-self:end">{type_label}</span></div>'
            )
        st.markdown("".join(rows_html), unsafe_allow_html=True)
    else:
        st.success("Простоев за последнее время не зафиксировано")

# --- Реестр устройств ------------------------------------------------------
devices = dashboard_data.fetch_devices()
if not devices.empty:
    with st.expander(f"🖥️ Устройства сети ({len(devices)})"):
        st.caption(
            "Что где стоит, какая версия и когда точка последний раз отзывалась. "
            "Пустая калибровка означает, что на этой камере считается весь кадр."
        )
        rows_html = []
        for _, dev in devices.iterrows():
            last_seen = pd.to_datetime(dev["last_seen"], utc=True)
            age_min = (datetime.now(timezone.utc) - last_seen).total_seconds() / 60
            silent = age_min > 10
            accent = palette.critical if silent else palette.good
            age_text = (
                f"{age_min:.0f} мин назад" if age_min < 90 else f"{age_min / 60:.0f} ч назад"
            )
            calibrated = bool((dev.get("config") or {}).get("entrance_roi"))
            calib_text = "откалибровано" if calibrated else "⚠ без зоны входа"
            calib_color = palette.text_muted if calibrated else palette.warning
            rows_html.append(
                f'<div style="display:grid;grid-template-columns:1.2fr 1fr auto auto auto;'
                f'align-items:center;gap:12px;padding:10px 14px;margin-bottom:6px;'
                f'border-radius:8px;background:{palette.surface};border:1px solid {palette.border};'
                f'border-left:3px solid {accent}">'
                f'<span style="color:{palette.text_primary};font-weight:600;font-size:14px">'
                f'{escape(str(dev["store"]))}</span>'
                f'<span style="color:{palette.text_secondary};font-size:13px">'
                f'{escape(str(dev.get("hostname") or "—"))} · {escape(str(dev.get("platform") or "—"))}</span>'
                f'<span style="color:{calib_color};font-size:12px">{calib_text}</span>'
                f'<span style="color:{palette.text_muted};font-size:12px;'
                f'font-family:ui-monospace,monospace">{escape(str(dev.get("version") or "—"))}</span>'
                f'<span style="color:{palette.text_muted};font-size:12px;justify-self:end">'
                f'{age_text}</span></div>'
            )
        st.markdown("".join(rows_html), unsafe_allow_html=True)

with st.expander("⚠️ Что делать, чтобы сервис не падал"):
    st.markdown("""
1. **Не выключайте и не закрывайте крышку ноутбука** в магазине — сервис остановится, пока кто-то физически не включит его обратно.
2. **Ноутбук всегда должен быть подключён к питанию и к интернету/VPN.** Если кабель выдернут или пропала сеть — подключите обратно.
3. **Не открывайте и не редактируйте файлы в папке `havas-pilot`** на ноутбуке без разработчиков — там боевая конфигурация.
4. Если статус выше показывает 🔴 дольше часа — в Telegram уже должен был прийти автоматический алерт. Если алерта не было, а статус красный — напишите разработчикам.
""")

# --- Живая лента + экспорт -------------------------------------------------
if period_choice in ("Сегодня", "Вчера") and not df_period.empty:
    with st.expander(f"📋 Живая лента ({period_choice.lower()})"):
        table = df_period.head(15).copy()
        dir_colors = {"IN": palette.in_, "OUT": palette.out}
        # «Вернулся», а не «Вышел»: камера стоит над входом, а уходят из
        # магазина через другую дверь. Событие OUT здесь означает, что человек
        # вышел через входную дверь — покурить, передумал, — и почти всегда
        # вернётся. Подпись «Вышел» читалась бы как уход из магазина и врала.
        exit_seen = getattr(config, "EXIT_IN_FRAME", True)
        dir_labels = {"IN": "Вошёл", "OUT": "Вышел" if exit_seen else "Через вход наружу"}
        rows_html = [f'<div style="background:{palette.surface};border:1px solid {palette.border};'
                     f'border-radius:10px;overflow:hidden">']
        for i, (_, row) in enumerate(table.iterrows()):
            identified = not str(row["visitor_id"]).startswith(config.UNKNOWN_VISITOR_PREFIX)
            type_text = ("Повторный" if row["is_repeat"] else "Новый") if identified else "Без личности"
            type_color = ((palette.repeat if row["is_repeat"] else palette.new)
                          if identified else palette.text_muted)
            d_color = dir_colors.get(row["direction"], palette.text_muted)
            border = f"border-bottom:1px solid {palette.gridline};" if i < len(table) - 1 else ""
            rows_html.append(
                f'<div style="display:flex;align-items:center;justify-content:space-between;'
                f'padding:10px 14px;{border}">'
                f'<span style="color:{palette.text_secondary};font-size:14px">'
                f'{row["timestamp"].strftime("%d.%m.%Y %H:%M:%S")}</span>'
                f'<span style="background:{palette.rgba(d_color, 0.12)};color:{d_color};'
                f'padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600">'
                f'{dir_labels.get(row["direction"], row["direction"])}</span>'
                f'<span style="background:{palette.rgba(type_color, 0.12)};color:{type_color};'
                f'padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600">{type_text}</span>'
                f'</div>'
            )
        rows_html.append("</div>")
        st.markdown("".join(rows_html), unsafe_allow_html=True)

if not df_period.empty:
    csv_bytes = df_period.assign(
        Дата=df_period["timestamp"].dt.strftime("%d.%m.%Y"),
        Время=df_period["timestamp"].dt.strftime("%H:%M:%S"),
        Тип=df_period["is_repeat"].map({False: "Новый", True: "Повторный"}),
        Опознан=is_identified(df_period["visitor_id"]).map({True: "да", False: "нет"}),
    )[["Дата", "Время", "direction", "Тип", "Опознан", "visitor_id"]].to_csv(index=False).encode("utf-8")
    st.download_button(
        "Скачать CSV за период", data=csv_bytes,
        file_name=f"havas_{store}_{period_choice}.csv", mime="text/csv", key="csv_download",
    )

# --- Отчёт для отправки ----------------------------------------------------
# HTML, а не PDF: страница открывается на любом устройстве и печатается в PDF
# средствами браузера. Генерировать PDF здесь значит тянуть ещё одну
# библиотеку в облачную сборку ради формата, который браузер и так умеет.
st.divider()
report_col, hint_col = st.columns([1, 3])
with report_col:
    hourly_rows = []
    if not df_in.empty and hourly_mode:
        counts = df_in.groupby(df_in["timestamp"].dt.hour).size()
        hourly_rows = [(f"{hour:02d}:00", int(count)) for hour, count in counts.items()]

    report_html = insights.build_report(
        store=store,
        period_label=period_choice,
        generated_at=datetime.now(TASHKENT_TZ),
        kpis=[
            ("Входов", total_in),
            ("Новые", f"{new_count} ({new_pct:.0f}%)"),
            ("Повторные", f"{repeat_count} ({repeat_pct:.0f}%)"),
            ("Опознано проходов", f"{identified_share:.0f}%"),
        ],
        summary=summary_facts,
        hourly=hourly_rows,
        goal_line=goal_line,
    )
    st.download_button(
        "Скачать отчёт", report_html,
        file_name=f"havas_{store}_{datetime.now(TASHKENT_TZ):%Y%m%d}.html",
        mime="text/html", use_container_width=True,
    )
with hint_col:
    st.caption(
        "Одна страница с показателями за период и главными выводами. "
        "Открывается в браузере; чтобы получить PDF — «Печать» → «Сохранить как PDF»."
    )

# --- Автообновление --------------------------------------------------------
# Фрагмент вместо time.sleep(30) + st.rerun(): раньше скрипт-поток был занят
# сном, а по его истечении перерисовывалась вся страница целиком, заново
# выполняя все запросы. Здесь обновляется только строка со временем, а данные
# приходят из кэша с TTL.


@st.fragment(run_every=config.DASHBOARD_REFRESH_SEC)
def _refresh_marker():
    st.caption(f"Обновлено: {datetime.now(TASHKENT_TZ).strftime('%H:%M:%S')}")


_refresh_marker()
