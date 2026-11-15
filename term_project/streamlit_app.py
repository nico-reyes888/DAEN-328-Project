"""LEGO term project dashboard (Streamlit + PostgreSQL via SQLAlchemy).

Reads only from the database, never from a CSV. Connection settings come from
the environment (.env). Tables expected, as produced by transform.py:
  sets(set_num, name, year, theme_name, parent_theme, num_parts, num_minifigs,
       num_unique_colors, has_minifigs, ...)
  themes(id, theme_name, parent_theme, total_sets)
  minifigures(fig_num, name, num_parts, total_sets_appeared, ...)
  theme_groups(theme_name, parent_theme)

Sets are always joined to theme_groups on BOTH theme_name and parent_theme.
Joining straight to themes would duplicate sets, because two theme names
(Creator, Technic) appear under two ids each.
"""
import os
import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from plotly.subplots import make_subplots
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL

load_dotenv()

st.set_page_config(page_title="LEGO 1975-2025", page_icon="🧱", layout="wide")

LEGO_RED = "#D01012"
LEGO_YELLOW = "#F5B800"
LEGO_BLUE = "#006CB7"
LEGO_DARK = "#1B2A34"


# ---------------------------------------------------------------- config
def _env(*names, default=None):
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _table(env_name, default):
    value = os.getenv(env_name, default)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"{env_name} is not a plain table name: {value!r}")
    return value


SETS = _table("TABLE_SETS", "sets")
THEMES = _table("TABLE_THEMES", "themes")
MINIFIGS = _table("TABLE_MINIFIGURES", "minifigures")
GROUPS = _table("TABLE_THEME_GROUPS", "theme_groups")


@st.cache_resource
def get_engine():
    url = _env("DATABASE_URL")
    if not url:
        url = URL.create(
            "postgresql+psycopg2",
            username=_env("POSTGRES_USER", "DB_USER", default="postgres"),
            password=_env("POSTGRES_PASSWORD", "DB_PASSWORD"),
            host=_env("POSTGRES_HOST", "DB_HOST", default="localhost"),
            port=int(_env("POSTGRES_PORT", "DB_PORT", default="5432")),
            database=_env("POSTGRES_DB", "DB_NAME", default="lego"),
        )
    return create_engine(url, pool_pre_ping=True)


def _freeze(params):
    return tuple(
        sorted(
            (k, tuple(v) if isinstance(v, (list, tuple)) else v)
            for k, v in params.items()
        )
    )


@st.cache_data(ttl=300, show_spinner=False)
def _run(sql, frozen):
    params = {k: (list(v) if isinstance(v, tuple) else v) for k, v in frozen}
    stmt = text(sql)
    for key, value in params.items():
        if isinstance(value, list):
            stmt = stmt.bindparams(bindparam(key, expanding=True))
    with get_engine().connect() as conn:
        return pd.read_sql(stmt, conn, params=params)


def q(sql, **params):
    """Run a read-only query and return a DataFrame."""
    return _run(sql, _freeze(params))


def to_num(df, columns):
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


# ---------------------------------------------------------------- sidebar
st.title("🧱 LEGO 1975-2025")
st.caption(
    "Sets, themes and minifigures, read live from PostgreSQL. "
    "Use the sidebar to filter every chart."
)

try:
    bounds = q(f"SELECT MIN(year) AS lo, MAX(year) AS hi FROM {SETS}")
    parent_list = q(
        f"SELECT DISTINCT parent_theme FROM {GROUPS} ORDER BY parent_theme"
    )["parent_theme"].dropna().tolist()
except Exception as exc:  # clean message instead of a traceback
    st.error(
        "Could not read from the database. Check that the ETL finished and "
        "that the POSTGRES_* / DATABASE_URL values in .env are right."
    )
    st.caption(f"Details: {type(exc).__name__}")
    st.stop()

year_lo, year_hi = int(bounds.loc[0, "lo"]), int(bounds.loc[0, "hi"])

with st.sidebar:
    st.header("Filters")
    years = st.slider("Release years", year_lo, year_hi, (year_lo, year_hi))
    parents = st.multiselect(
        "Parent theme (empty = all)", parent_list,
        help="Applies to the set-based charts.",
    )
    only_minifigs = st.checkbox("Only sets that include minifigures")
    top_n = st.slider("How many themes to show", 5, 30, 15)
    st.divider()
    st.caption(
        "Minifigure charts are not linked to sets in the source data, "
        "so the year and theme filters do not apply to them."
    )


def set_filter(alias="s", extra=()):
    clauses = [f"{alias}.year BETWEEN :y0 AND :y1"]
    params = {"y0": int(years[0]), "y1": int(years[1])}
    if parents:
        clauses.append(f"{alias}.parent_theme IN :parents")
        params["parents"] = list(parents)
    if only_minifigs:
        clauses.append(f"{alias}.has_minifigs = TRUE")
    clauses.extend(extra)
    return " AND ".join(clauses), params


def style(fig, height=380):
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=50, b=10),
        font=dict(color=LEGO_DARK),
        legend=dict(orientation="h", y=-0.2),
    )
    return fig


def show(fig):
    st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------- data
where, wp = set_filter()

kpi = to_num(
    q(
        f"""SELECT COUNT(*) AS sets, SUM(s.num_parts) AS parts,
                   AVG(s.num_parts) AS avg_parts,
                   COUNT(DISTINCT s.theme_name) AS themes
            FROM {SETS} s WHERE {where}""",
        **wp,
    ),
    ["sets", "parts", "avg_parts", "themes"],
)

if int(kpi.loc[0, "sets"] or 0) == 0:
    st.warning("No sets match these filters. Widen the year range or clear the theme filter.")
    st.stop()

yearly = to_num(
    q(
        f"""SELECT s.year, COUNT(*) AS sets, AVG(s.num_parts) AS avg_parts,
                   AVG(s.num_unique_colors) AS avg_colors
            FROM {SETS} s WHERE {where}
            GROUP BY s.year ORDER BY s.year""",
        **wp,
    ),
    ["year", "sets", "avg_parts", "avg_colors"],
)

themes_top = to_num(
    q(
        f"""SELECT tg.theme_name, tg.parent_theme,
                   COUNT(*) AS sets, AVG(s.num_parts) AS avg_parts
            FROM {SETS} s
            JOIN {GROUPS} tg
              ON tg.theme_name = s.theme_name
             AND tg.parent_theme = s.parent_theme
            WHERE {where}
            GROUP BY tg.theme_name, tg.parent_theme
            ORDER BY sets DESC, tg.theme_name
            LIMIT :n""",
        n=int(top_n),
        **wp,
    ),
    ["sets", "avg_parts"],
)

big_where, big_p = set_filter(extra=("s.num_parts IS NOT NULL",))
biggest = to_num(
    q(
        f"""SELECT s.set_num, s.name, s.year, tg.theme_name,
                   s.num_parts, s.num_minifigs
            FROM {SETS} s
            JOIN {GROUPS} tg
              ON tg.theme_name = s.theme_name
             AND tg.parent_theme = s.parent_theme
            WHERE {big_where}
            ORDER BY s.num_parts DESC, s.set_num
            LIMIT 10""",
        **big_p,
    ),
    ["year", "num_parts", "num_minifigs"],
)

fig_where, fig_p = set_filter(extra=("s.num_minifigs IS NOT NULL",))
by_minifig = to_num(
    q(
        f"""SELECT CASE WHEN s.num_minifigs = 0 THEN '0'
                        WHEN s.num_minifigs = 1 THEN '1'
                        WHEN s.num_minifigs BETWEEN 2 AND 3 THEN '2-3'
                        WHEN s.num_minifigs BETWEEN 4 AND 6 THEN '4-6'
                        ELSE '7+' END AS bucket,
                   COUNT(*) AS sets, AVG(s.num_parts) AS avg_parts
            FROM {SETS} s WHERE {fig_where}
            GROUP BY 1""",
        **fig_p,
    ),
    ["sets", "avg_parts"],
)

flag_where, flag_p = set_filter(extra=("s.has_minifigs IS NOT NULL",))
decades = to_num(
    q(
        f"""SELECT (s.year / 10) * 10 AS decade, COUNT(*) AS sets,
                   AVG(CASE WHEN s.has_minifigs THEN 1.0 ELSE 0.0 END) AS share
            FROM {SETS} s WHERE {flag_where}
            GROUP BY (s.year / 10) * 10 ORDER BY decade""",
        **flag_p,
    ),
    ["decade", "sets", "share"],
)

appear = to_num(
    q(
        f"""SELECT CASE WHEN m.total_sets_appeared = 1 THEN '1 set'
                        WHEN m.total_sets_appeared BETWEEN 2 AND 5 THEN '2-5 sets'
                        WHEN m.total_sets_appeared BETWEEN 6 AND 20 THEN '6-20 sets'
                        ELSE '21+ sets' END AS bucket,
                   COUNT(*) AS figures
            FROM {MINIFIGS} m
            WHERE m.total_sets_appeared IS NOT NULL
            GROUP BY 1"""
    ),
    ["figures"],
)

recon = to_num(
    q(
        f"""SELECT tg.theme_name, tg.parent_theme,
                   COALESCE(t.declared, 0) AS declared_total_sets,
                   COALESCE(c.counted, 0) AS counted_sets
            FROM {GROUPS} tg
            LEFT JOIN (SELECT theme_name, parent_theme,
                              SUM(total_sets) AS declared
                       FROM {THEMES} GROUP BY theme_name, parent_theme) t
                   ON t.theme_name = tg.theme_name
                  AND t.parent_theme = tg.parent_theme
            LEFT JOIN (SELECT theme_name, parent_theme, COUNT(*) AS counted
                       FROM {SETS} GROUP BY theme_name, parent_theme) c
                   ON c.theme_name = tg.theme_name
                  AND c.parent_theme = tg.parent_theme"""
    ),
    ["declared_total_sets", "counted_sets"],
)
recon["difference"] = recon["counted_sets"] - recon["declared_total_sets"]

# ---------------------------------------------------------------- KPIs
c1, c2, c3, c4 = st.columns(4)
c1.metric("Sets", f"{int(kpi.loc[0, 'sets']):,}")
c2.metric("Total parts", f"{int(kpi.loc[0, 'parts'] or 0):,}")
c3.metric("Avg parts per set", f"{float(kpi.loc[0, 'avg_parts'] or 0):,.0f}")
c4.metric("Theme names", f"{int(kpi.loc[0, 'themes']):,}")

# ---------------------------------------------------------------- insights
def weighted_avg(frame, column):
    weight = frame["sets"]
    if weight.sum() == 0:
        return float("nan")
    return float((frame[column] * weight).sum() / weight.sum())


insights = []
peak = yearly.loc[yearly["sets"].idxmax()]
insights.append(
    f"**Busiest year:** {int(peak['year'])} with {int(peak['sets']):,} sets released."
)
if len(yearly) >= 10:
    early, late = yearly.head(5), yearly.tail(5)
    insights.append(
        f"**Sets got bigger:** the first 5 years in view average "
        f"{weighted_avg(early, 'avg_parts'):,.0f} parts per set, "
        f"the last 5 average {weighted_avg(late, 'avg_parts'):,.0f}."
    )
if not themes_top.empty:
    lead = themes_top.iloc[0]
    insights.append(
        f"**Biggest theme:** {lead['theme_name']} with {int(lead['sets']):,} sets."
    )
if len(decades) >= 2:
    first, last = decades.iloc[0], decades.iloc[-1]
    insights.append(
        f"**Minifigures became standard:** {first['share']:.0%} of sets in the "
        f"{int(first['decade'])}s include one, versus {last['share']:.0%} in the "
        f"{int(last['decade'])}s."
    )
if not appear.empty and appear["figures"].sum() > 0:
    once = appear.loc[appear["bucket"] == "1 set", "figures"].sum()
    insights.append(
        f"**Most minifigures are one-offs:** {once / appear['figures'].sum():.0%} "
        f"appear in exactly one set."
    )

with st.expander("What the data says (updates with your filters)", expanded=True):
    for line in insights:
        st.markdown(f"- {line}")

# ---------------------------------------------------------------- tabs
tab_over, tab_themes, tab_figs, tab_quality = st.tabs(
    ["📈 Over time", "🧩 Themes and sets", "🧍 Minifigures", "✅ Data quality"]
)

with tab_over:
    st.subheader("How has LEGO output changed over time?")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=yearly["year"], y=yearly["sets"], name="Sets released",
                marker_color=LEGO_YELLOW)
    fig.add_scatter(x=yearly["year"], y=yearly["avg_parts"], name="Avg parts per set",
                    mode="lines+markers", line=dict(color=LEGO_RED, width=3),
                    secondary_y=True)
    fig.update_yaxes(title_text="Sets released", secondary_y=False)
    fig.update_yaxes(title_text="Avg parts per set", secondary_y=True)
    fig.update_layout(title="Sets released per year and average set size")
    show(style(fig))

    fig = px.line(yearly, x="year", y="avg_colors", markers=True,
                  title="Average number of unique colors per set",
                  labels={"avg_colors": "Avg unique colors", "year": "Year"})
    fig.update_traces(line_color=LEGO_BLUE)
    show(style(fig, 320))

with tab_themes:
    st.subheader("Which themes dominate, and which sets are the biggest?")
    labels = themes_top.apply(
        lambda r: r["theme_name"]
        if r["theme_name"] == r["parent_theme"]
        else f"{r['theme_name']} ({r['parent_theme']})",
        axis=1,
    )
    plot = themes_top.assign(label=labels).sort_values("sets")
    fig = px.bar(plot, x="sets", y="label", orientation="h",
                 color="avg_parts", color_continuous_scale=["#FFE08A", LEGO_RED],
                 hover_data={"avg_parts": ":.0f"},
                 title=f"Top {len(plot)} themes by number of sets",
                 labels={"sets": "Sets", "label": "", "avg_parts": "Avg parts"})
    show(style(fig, max(380, 26 * len(plot) + 120)))

    left, right = st.columns(2)
    with left:
        order = ["0", "1", "2-3", "4-6", "7+"]
        plot = by_minifig.set_index("bucket").reindex(order).dropna(how="all").reset_index()
        fig = px.bar(plot, x="bucket", y="avg_parts",
                     title="Do sets with more minifigures have more parts?",
                     labels={"bucket": "Minifigures in the set",
                             "avg_parts": "Avg parts per set"},
                     hover_data={"sets": ":,"})
        fig.update_traces(marker_color=LEGO_BLUE)
        show(style(fig, 340))
    with right:
        st.markdown("**10 biggest sets in view**")
        st.dataframe(
            biggest.rename(columns={"set_num": "Set", "name": "Name", "year": "Year",
                                    "theme_name": "Theme", "num_parts": "Parts",
                                    "num_minifigs": "Minifigs"}),
            hide_index=True, width="stretch",
        )

with tab_figs:
    st.subheader("When did minifigures take over, and which ones are everywhere?")
    left, right = st.columns(2)
    with left:
        fig = px.bar(decades, x="decade", y="share",
                     title="Share of sets that include minifigures, by decade",
                     labels={"decade": "Decade", "share": "Share of sets"},
                     hover_data={"sets": ":,"})
        fig.update_yaxes(tickformat=".0%", range=[0, 1])
        fig.update_traces(marker_color=LEGO_RED)
        show(style(fig, 340))
    with right:
        order = ["1 set", "2-5 sets", "6-20 sets", "21+ sets"]
        plot = appear.set_index("bucket").reindex(order).dropna(how="all").reset_index()
        fig = px.bar(plot, x="bucket", y="figures",
                     title="How many sets does each minifigure appear in?",
                     labels={"bucket": "Appears in", "figures": "Minifigures"})
        fig.update_traces(marker_color=LEGO_YELLOW)
        show(style(fig, 340))

    st.markdown("**Search minifigures**")
    search = st.text_input("Name contains", placeholder="e.g. Darth, Batman, Wizard")
    limit = st.slider("Rows", 10, 100, 25, step=5)
    like = f"%{search.strip().lower()}%"
    figs = to_num(
        q(
            f"""SELECT m.fig_num, m.name, m.num_parts, m.total_sets_appeared
                FROM {MINIFIGS} m
                WHERE LOWER(m.name) LIKE :like
                ORDER BY (m.total_sets_appeared IS NULL),
                         m.total_sets_appeared DESC, m.fig_num
                LIMIT :n""",
            like=like, n=int(limit),
        ),
        ["num_parts", "total_sets_appeared"],
    )
    if figs.empty:
        st.info("No minifigure matches that search.")
    else:
        st.dataframe(
            figs.rename(columns={"fig_num": "Figure", "name": "Name",
                                 "num_parts": "Parts",
                                 "total_sets_appeared": "Sets appeared in"}),
            hide_index=True, width="stretch",
        )

with tab_quality:
    st.subheader("Do the themes table and the sets table agree?")
    st.caption(
        "Compares the total_sets each theme claims with the number of sets "
        "actually in the sets table. Both sides are summed per theme group "
        "before joining, so no row is counted twice."
    )
    matches = int((recon["difference"] == 0).sum())
    a, b, c = st.columns(3)
    a.metric("Theme groups", f"{len(recon):,}")
    b.metric("Counts agree", f"{matches:,}")
    c.metric("Counts differ", f"{len(recon) - matches:,}")
    off = recon[recon["difference"] != 0].copy()
    off["abs"] = off["difference"].abs()
    off = off.sort_values("abs", ascending=False).drop(columns="abs").head(25)
    if off.empty:
        st.success("Every theme's declared total matches the sets table.")
    else:
        st.dataframe(
            off.rename(columns={"theme_name": "Theme", "parent_theme": "Parent theme",
                                "declared_total_sets": "Declared total",
                                "counted_sets": "Counted in sets",
                                "difference": "Difference"}),
            hide_index=True, width="stretch",
        )
        fig = go.Figure(go.Bar(x=off["theme_name"], y=off["difference"],
                               marker_color=LEGO_RED))
        fig.update_layout(title="Largest disagreements (counted minus declared)")
        show(style(fig, 320))
