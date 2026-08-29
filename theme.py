"""Палитра, CSS и карточки дашборда.

Три вещи, которых не хватало:

* **Тёмная тема.** Цвета были захардкожены под светлую — у пользователя с
  тёмной темой Streamlit получался светлый прямоугольник на тёмном фоне.
* **Экранирование.** Карточки собираются как HTML с `unsafe_allow_html`, и в
  них подставлялись значения из базы (тип инцидента, имя магазина). Пока туда
  пишет только наш воркер, это безобидно; с публикуемым ключом — вектор XSS.
* **Иерархия.** Все блоки были одного визуального веса, поэтому взгляду не за
  что зацепиться. Здесь введены три уровня: главная цифра, вспомогательные
  карточки, детали.
"""

from html import escape
from typing import List, Optional, Tuple

import streamlit as st

FONT_STACK = "system-ui, -apple-system, 'Segoe UI', sans-serif"


def is_dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


class Palette:
    """Цвета под текущую тему. Категориальные оттенки подобраны так, чтобы
    сохранять контраст и на светлом, и на тёмном фоне."""

    def __init__(self, dark: bool):
        self.dark = dark
        if dark:
            self.new = "#5b9cf0"
            self.repeat = "#f0894f"
            self.out = "#8b7ce8"
            self.trend = "#2fc99a"
            self.good = "#3ec13e"
            self.warning = "#ffc740"
            self.critical = "#ff5f5f"
            self.surface = "#1b1c1f"
            self.page_bg = "#121316"
            self.text_primary = "#f2f2f0"
            self.text_secondary = "#b8b7b2"
            self.text_muted = "#8a8985"
            self.gridline = "#2e3035"
            self.border = "rgba(255,255,255,0.12)"
            self.ordinal = ["#1d4e8f", "#2a78d6", "#5b9cf0", "#a4c8f7"]
            self.sequential = [(0.0, "#152b47"), (0.35, "#1f5596"), (0.65, "#2f80d8"), (1.0, "#8ec1f8")]
        else:
            self.new = "#2a78d6"
            self.repeat = "#eb6834"
            self.out = "#4a3aa7"
            self.trend = "#1baf7a"
            self.good = "#0ca30c"
            self.warning = "#fab219"
            self.critical = "#d03b3b"
            self.surface = "#fcfcfb"
            self.page_bg = "#f9f9f7"
            self.text_primary = "#0b0b0b"
            self.text_secondary = "#52514e"
            self.text_muted = "#898781"
            self.gridline = "#e1e0d9"
            self.border = "rgba(11,11,11,0.10)"
            self.ordinal = ["#86b6ef", "#3987e5", "#256abf", "#104281"]
            self.sequential = [(0.0, "#cde2fb"), (0.35, "#6da7ec"), (0.65, "#2a78d6"), (1.0, "#0d366b")]
        self.in_ = self.new

    def rgba(self, hex_color: str, alpha: float) -> str:
        """Plotly не принимает 8-значный #RRGGBBAA (это CSS-only) — для заливок нужен rgba()."""
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"


def inject_css(p: Palette) -> None:
    st.markdown(f"""
<style>
html, body, [class*="css"] {{ font-family: {FONT_STACK}; }}
.stApp {{ background-color: {p.page_bg}; }}
[data-testid="stHeader"] {{ background-color: transparent; }}

h1#havas-analytics {{ font-weight: 700; letter-spacing: -0.02em; margin-bottom: 0; color: {p.text_primary}; }}
.havas-subtitle {{ color: {p.text_muted}; font-size: 14px; margin-top: -6px; margin-bottom: 18px; }}

[data-testid="stExpander"] {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
}}

h3 {{ font-size: 16px !important; font-weight: 600 !important; color: {p.text_primary}; }}

div[role="radiogroup"] {{ gap: 4px; flex-wrap: wrap; }}
div[role="radiogroup"] label {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px 14px !important;
    margin-right: 0 !important;
    transition: background 0.15s;
}}

hr {{ border-color: {p.gridline} !important; margin: 1.4rem 0 !important; }}

/* Телефон: экран дорогой — убираем лишний воздух сверху и ужимаем карточки */
@media (max-width: 640px) {{
    .havas-hero-value {{ font-size: 2.4rem !important; }}
    .havas-card {{ padding: 14px 16px !important; }}
    .block-container {{ padding-top: 1.2rem !important; }}
    h1#havas-analytics {{ font-size: 1.8rem !important; }}
}}
.block-container {{ padding-top: 2.2rem; }}
</style>
""", unsafe_allow_html=True)


def style_fig(fig, p: Palette, height: Optional[int] = None):
    """Единое оформление графиков под текущую тему."""
    fig.update_layout(
        plot_bgcolor=p.surface,
        paper_bgcolor=p.surface,
        font=dict(family=FONT_STACK, color=p.text_secondary, size=13),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=p.text_secondary)),
        margin=dict(t=16, b=10, l=10, r=10),
        hoverlabel=dict(bgcolor=p.surface, font=dict(family=FONT_STACK, color=p.text_primary)),
    )
    fig.update_xaxes(gridcolor=p.gridline, linecolor=p.gridline,
                     tickfont=dict(color=p.text_muted), title_font=dict(color=p.text_secondary))
    fig.update_yaxes(gridcolor=p.gridline, linecolor=p.gridline,
                     tickfont=dict(color=p.text_muted), title_font=dict(color=p.text_secondary))
    if height:
        fig.update_layout(height=height)
    return fig


def status_banner(icon: str, text: str, color: str, p: Palette) -> None:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;background:{p.rgba(color, 0.08)};'
        f'border:1px solid {p.rgba(color, 0.25)};border-left:4px solid {color};'
        f'border-radius:8px;padding:12px 16px;margin-bottom:12px">'
        f'<span style="font-size:18px;line-height:1">{icon}</span>'
        f'<span style="color:{p.text_primary};font-weight:600;font-size:15px">{escape(text)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def hero_metric(label: str, value: str, p: Palette, delta: Optional[str] = None,
                delta_positive: bool = True, hint: Optional[str] = None) -> None:
    """Главная цифра страницы — визуально тяжелее остальных карточек.

    Раньше все метрики были одного размера, и «Входов за период» терялось
    среди служебных чисел.
    """
    delta_html = ""
    if delta:
        color = p.good if delta_positive else p.critical
        arrow = "↑" if delta_positive else "↓"
        delta_html = (f'<div style="color:{color};font-size:14px;margin-top:6px">'
                      f'{arrow} {escape(delta)}</div>')
    hint_html = ""
    if hint:
        hint_html = f'<div style="color:{p.text_muted};font-size:13px;margin-top:8px">{escape(hint)}</div>'
    st.markdown(
        f'<div class="havas-card" style="background:{p.surface};border:1px solid {p.border};'
        f'border-radius:14px;padding:22px 24px;height:100%">'
        f'<div style="color:{p.text_muted};font-size:13px;text-transform:uppercase;'
        f'letter-spacing:0.04em">{escape(label)}</div>'
        f'<div class="havas-hero-value" style="color:{p.text_primary};font-size:3rem;font-weight:650;'
        f'font-variant-numeric:tabular-nums;line-height:1.1;margin-top:6px">{escape(value)}</div>'
        f'{delta_html}{hint_html}</div>',
        unsafe_allow_html=True,
    )


def metric_card(label: str, value: str, p: Palette, delta: Optional[str] = None,
                delta_positive: bool = True, hint: Optional[str] = None) -> None:
    """Обычная карточка.

    Своя HTML-разметка вместо st.metric: встроенный виджет обрезает текст
    многоточием в узкой колонке через JS-измерение, и это не лечится никаким CSS.
    Собирается одной строкой (без переносов и отступов) — иначе парсер Markdown
    в Streamlit принимает отступ после пустой строки за блок кода.
    """
    delta_html = ""
    if delta:
        color = p.good if delta_positive else p.critical
        arrow = "↑" if delta_positive else "↓"
        delta_html = (f'<div style="color:{color};font-size:13px;margin-top:4px">'
                      f'{arrow} {escape(delta)}</div>')
    hint_html = ""
    if hint:
        hint_html = f'<div style="color:{p.text_muted};font-size:12px;margin-top:6px">{escape(hint)}</div>'
    st.markdown(
        f'<div class="havas-card" style="background:{p.surface};border:1px solid {p.border};'
        f'border-radius:12px;padding:18px 20px;height:100%">'
        f'<div style="color:{p.text_muted};font-size:13px;overflow-wrap:break-word">{escape(label)}</div>'
        f'<div style="color:{p.text_primary};font-size:1.9rem;font-weight:600;'
        f'font-variant-numeric:tabular-nums;overflow-wrap:break-word;line-height:1.2;'
        f'margin-top:4px">{escape(value)}</div>{delta_html}{hint_html}</div>',
        unsafe_allow_html=True,
    )


def breakdown_card(label: str, rows: List[Tuple[str, str]], p: Palette) -> None:
    """Карточка со списком строк вместо одной большой цифры."""
    if not rows:
        rows = [("—", "")]
    rows_html = "".join(
        f'<div style="display:flex;justify-content:space-between;gap:8px;'
        f'font-size:14px;color:{p.text_primary};margin-top:6px">'
        f'<span>{escape(k)}</span>'
        f'<span style="font-weight:600;font-variant-numeric:tabular-nums">{escape(v)}</span></div>'
        for k, v in rows
    )
    st.markdown(
        f'<div class="havas-card" style="background:{p.surface};border:1px solid {p.border};'
        f'border-radius:12px;padding:18px 20px;height:100%">'
        f'<div style="color:{p.text_muted};font-size:13px">{escape(label)}</div>'
        f'{rows_html}</div>',
        unsafe_allow_html=True,
    )


STATUS_LABEL = {
    "ok": ("Работает", "good"),
    "camera_down": ("Камера недоступна", "warning"),
    "stalled": ("Не считает", "critical"),
    "down": ("Не отвечает", "critical"),
}


def store_row(row, p: Palette, is_current: bool = False) -> None:
    """Строка магазина в сводке по сети: состояние читается формой, не только цифрой."""
    label, tone = STATUS_LABEL.get(row["status"], (row["status"], "muted"))
    color = {"good": p.good, "warning": p.warning, "critical": p.critical}.get(tone, p.text_muted)
    extras = []
    if row.get("fps"):
        extras.append(f"{row['fps']:.1f} кадр/с")
    if row.get("pending"):
        extras.append(f"очередь {int(row['pending'])}")
    if row.get("version"):
        extras.append(str(row["version"]))
    st.markdown(
        f'<div style="display:grid;grid-template-columns:minmax(120px,1.4fr) auto auto auto minmax(90px,auto);'
        f'align-items:center;gap:14px;padding:12px 16px;margin-bottom:6px;border-radius:10px;'
        f'background:{p.surface};border:1px solid {p.border};'
        f'border-left:4px solid {color}">'
        f'<span style="color:{p.text_primary};font-weight:{"700" if is_current else "600"};'
        f'font-size:15px">{escape(str(row["store"]))}</span>'
        f'<span style="color:{p.text_primary};font-size:15px;font-variant-numeric:tabular-nums">'
        f'{int(row["entries"])} входов</span>'
        f'<span style="color:{p.text_secondary};font-size:13px;font-variant-numeric:tabular-nums">'
        f'повторных {row["repeat_pct"]:.0f}%</span>'
        f'<span style="color:{p.text_muted};font-size:12px">{escape(" · ".join(extras))}</span>'
        f'<span style="background:{p.rgba(color, 0.12)};color:{color};padding:3px 10px;'
        f'border-radius:12px;font-size:12px;font-weight:600;white-space:nowrap;'
        f'justify-self:end">{escape(label)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def summary_block(facts: List[str], p: Palette) -> None:
    """Выводы словами — первое, что читают на странице."""
    if not facts:
        return
    items = "".join(
        f'<li style="margin:6px 0;color:{p.text_primary};font-size:15px;line-height:1.5">{escape(fact)}</li>'
        for fact in facts
    )
    st.markdown(
        f'<div class="havas-card" style="background:{p.surface};border:1px solid {p.border};'
        f'border-left:4px solid {p.new};border-radius:12px;padding:16px 20px 16px 22px;margin-bottom:14px">'
        f'<div style="color:{p.text_muted};font-size:12px;text-transform:uppercase;'
        f'letter-spacing:0.04em;margin-bottom:4px">Главное за период</div>'
        f'<ul style="margin:0;padding-left:18px">{items}</ul></div>',
        unsafe_allow_html=True,
    )
