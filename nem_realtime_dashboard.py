"""
nem_realtime_dashboard.py — v5 FINAL
Datos reales del NEM via Open Electricity API v4.

CORRER:
    $env:OPENELECTRICITY_API_KEY = "tu-key"
    python -m streamlit run nem_realtime_dashboard.py

DEPENDENCIAS:
    pip install streamlit plotly pandas numpy pytz requests
"""

import os, streamlit as st, pandas as pd, numpy as np
import plotly.graph_objects as go, requests
from datetime import datetime, timedelta
import pytz

# ── Constantes ────────────────────────────────────────────────────────────────
AEST     = pytz.timezone("Australia/Brisbane")
REGIONS  = ["QLD1", "NSW1", "VIC1", "SA1", "TAS1"]
API_BASE = "https://api.openelectricity.org.au/v4"

# Umbrales de régimen (market_value $/intervalo → precio implícito $/MWh)
# market_value = power_MW * price_$/MWh * (5/60)h  → price ≈ market_value / power * 12
SPIKE_MV = 500_000   # market_value spike proxy
HIGH_MV  = 150_000

PALETTE = {
    "bg":"#0A0E1A","card":"#111827","border":"#1E2D45",
    "accent":"#00C8FF","accent3":"#10B981","text":"#E2E8F0","muted":"#64748B",
    "QLD1":"#F59E0B","NSW1":"#3B82F6","VIC1":"#8B5CF6","SA1":"#EF4444","TAS1":"#10B981",
    "spike":"#FF3B30","high":"#FF9500","normal":"#30D158","low":"#0A84FF","neg":"#BF5AF2",
}

FUEL_COLORS = {
    "solar_utility":"#FCD34D","solar_rooftop":"#FDE68A","wind":"#6EE7B7",
    "hydro":"#60A5FA","gas_ccgt":"#F87171","gas_ocgt":"#FCA5A5",
    "coal_black":"#6B7280","coal_brown":"#92400E",
    "battery_discharging":"#A78BFA","battery_charging":"#C4B5FD",
    "battery":"#A78BFA","bioenergy_biomass":"#86EFAC",
    "pumps":"#BAE6FD","distillate":"#FB923C","exports":"#94A3B8",
}
FUEL_LABELS = {
    "solar_utility":"Solar Utility","solar_rooftop":"Solar Rooftop","wind":"Wind",
    "hydro":"Hydro","gas_ccgt":"Gas CCGT","gas_ocgt":"Gas OCGT",
    "coal_black":"Coal","coal_brown":"Coal (Brown)","battery":"Battery",
    "battery_discharging":"Battery","battery_charging":"Battery (Chg)",
    "bioenergy_biomass":"Bioenergy","pumps":"Pumped Hydro","distillate":"Distillate",
}
RENEWABLE = {"solar_utility","solar_rooftop","wind","hydro","bioenergy_biomass"}
SOLAR     = {"solar_utility","solar_rooftop"}


# ── CSS ────────────────────────────────────────────────────────────────────────
def inject_css():
    st.markdown(f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Sora:wght@300;400;600;700&display=swap');
    html,body,[class*="css"]{{font-family:'Sora',sans-serif;background:{PALETTE['bg']};color:{PALETTE['text']};}}
    .nem-title{{font-family:'IBM Plex Mono',monospace;font-size:1.6rem;font-weight:600;color:{PALETTE['accent']};letter-spacing:.04em;}}
    .nem-sub{{font-size:.74rem;color:{PALETTE['muted']};font-family:'IBM Plex Mono',monospace;}}
    .card{{background:{PALETTE['card']};border:1px solid {PALETTE['border']};border-radius:10px;padding:1rem 1.2rem;margin-bottom:.6rem;}}
    .card:hover{{border-color:{PALETTE['accent']}55;}}
    .lbl{{font-size:.66rem;color:{PALETTE['muted']};font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:.1em;margin-bottom:.2rem;}}
    .val{{font-family:'IBM Plex Mono',monospace;font-size:1.4rem;font-weight:600;}}
    .dlt{{font-size:.68rem;font-family:'IBM Plex Mono',monospace;margin-top:.1rem;}}
    .badge{{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:.66rem;font-weight:600;
            letter-spacing:.08em;text-transform:uppercase;padding:.15rem .5rem;border-radius:4px;margin-top:.2rem;}}
    .b-spike{{background:rgba(255,59,48,.13);color:{PALETTE['spike']};border:1px solid rgba(255,59,48,.35);}}
    .b-high{{background:rgba(255,149,0,.13);color:{PALETTE['high']};border:1px solid rgba(255,149,0,.35);}}
    .b-normal{{background:rgba(48,209,88,.13);color:{PALETTE['normal']};border:1px solid rgba(48,209,88,.35);}}
    .b-low{{background:rgba(10,132,255,.13);color:{PALETTE['low']};border:1px solid rgba(10,132,255,.35);}}
    .sec{{font-family:'IBM Plex Mono',monospace;font-size:.72rem;color:{PALETTE['accent']};text-transform:uppercase;
          letter-spacing:.12em;border-bottom:1px solid {PALETTE['border']};padding-bottom:.3rem;margin:1.1rem 0 .7rem;}}
    .dot{{display:inline-block;width:7px;height:7px;background:{PALETTE['accent3']};border-radius:50%;
          margin-right:6px;animation:pulse 2s infinite;}}
    @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
    </style>""", unsafe_allow_html=True)


# ── API ────────────────────────────────────────────────────────────────────────
def _hdr(key): return {"Authorization": f"Bearer {key}"}
def _dt(dt):
    if hasattr(dt,"tzinfo") and dt.tzinfo: dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")

def _get(key, endpoint, params, label=""):
    try:
        r = requests.get(f"{API_BASE}{endpoint}", headers=_hdr(key),
                         params=params, timeout=20)
        if r.status_code == 401:
            st.error("❌ API key inválida.")
            return None
        r.raise_for_status()
        return r.json()
    except requests.Timeout:
        st.warning(f"⚠️ Timeout ({label})")
        return None
    except Exception as e:
        st.warning(f"⚠️ Error {label}: {e}")
        return None


def _parse(raw) -> pd.DataFrame:
    """
    Parsea respuesta de la API.
    Data points son listas: ["2026-03-16T15:00:00+10:00", 172.76]
    Nombres de results: "power_QLD1|coal_black"  o  "market_value_QLD1"
    """
    if not raw or "data" not in raw:
        return pd.DataFrame()
    rows = []
    for series in raw["data"]:
        for result in series.get("results", []):
            name = result.get("name", "")
            # Separar región y fueltech
            # Formato A: "power_QLD1|coal_black"
            # Formato B: "market_value_QLD1"
            if "|" in name:
                left, fueltech = name.split("|", 1)
                # left = "power_QLD1"
                region = left.split("_")[-1]
            else:
                # "market_value_QLD1" → tomar última parte
                region   = name.split("_")[-1]
                fueltech = None

            for dp in result.get("data", []):
                # dp es lista [timestamp, value]
                if isinstance(dp, list) and len(dp) >= 2:
                    ts  = pd.to_datetime(dp[0], utc=True).tz_convert("Australia/Brisbane")
                    val = float(dp[1]) if dp[1] is not None else 0.0
                elif isinstance(dp, dict):
                    ts  = pd.to_datetime(dp.get("interval") or dp.get("timestamp"), utc=True)
                    val = float(next((v for v in dp.values() if isinstance(v,(int,float))), 0))
                else:
                    continue

                row = {"ts": ts, "region": region, "value": val}
                if fueltech:
                    row["fueltech"] = fueltech
                rows.append(row)

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── Fetch ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def get_market_value(key, hours):
    """market_value $/intervalo por región — proxy del precio spot."""
    raw = _get(key, "/data/network/NEM", {
        "metrics": "market_value", "interval": "5m",
        "date_start": _dt(datetime.now(AEST) - timedelta(hours=hours)),
        "primary_grouping": "network_region",
    }, "market_value")
    df = _parse(raw)
    if df.empty: return df
    df = df.rename(columns={"value": "mv"})
    return df[df["region"].isin(REGIONS)].sort_values("ts")


@st.cache_data(ttl=300, show_spinner=False)
def get_power(key, hours, interval="5m"):
    """Generación MW por región y fueltech."""
    raw = _get(key, "/data/network/NEM", {
        "metrics": "power", "interval": interval,
        "date_start": _dt(datetime.now(AEST) - timedelta(hours=hours)),
        "primary_grouping": "network_region",
        "secondary_grouping": "fueltech",
    }, "power")
    df = _parse(raw)
    if df.empty: return df
    df = df.rename(columns={"value": "mw"})
    return df[df["region"].isin(REGIONS)].sort_values("ts")


@st.cache_data(ttl=300, show_spinner=False)
def get_energy(key, hours):
    """Energía MWh por región."""
    raw = _get(key, "/data/network/NEM", {
        "metrics": "energy", "interval": "1h",
        "date_start": _dt(datetime.now(AEST) - timedelta(hours=hours)),
        "primary_grouping": "network_region",
    }, "energy")
    df = _parse(raw)
    if df.empty: return df
    df = df.rename(columns={"value": "mwh"})
    return df[df["region"].isin(REGIONS)].sort_values("ts")


@st.cache_data(ttl=300, show_spinner=False)
def get_emissions(key, hours):
    """Emisiones tCO2 por región."""
    raw = _get(key, "/data/network/NEM", {
        "metrics": "emissions", "interval": "1h",
        "date_start": _dt(datetime.now(AEST) - timedelta(hours=hours)),
        "primary_grouping": "network_region",
    }, "emissions")
    df = _parse(raw)
    if df.empty: return df
    df = df.rename(columns={"value": "tco2"})
    return df[df["region"].isin(REGIONS)].sort_values("ts")


# ── Precio implícito desde market_value y power ───────────────────────────────
def implied_price(df_mv: pd.DataFrame, df_pw: pd.DataFrame) -> pd.DataFrame:
    """
    price ≈ market_value / (total_power_MW * 5/60)
    Retorna DataFrame con columnas: ts, region, price
    """
    if df_mv.empty or df_pw.empty:
        return pd.DataFrame(columns=["ts","region","price"])

    total_pw = df_pw.groupby(["ts","region"])["mw"].sum().reset_index(name="total_mw")
    merged   = df_mv.merge(total_pw, on=["ts","region"], how="inner")
    merged["price"] = merged.apply(
        lambda r: r["mv"] / (r["total_mw"] * 5/60) if r["total_mw"] > 10 else 0.0, axis=1
    )
    return merged[["ts","region","price"]].sort_values(["region","ts"])


# ── Régimen por precio implícito ───────────────────────────────────────────────
def regime(price):
    if price >= 300:   return "SPIKE",    "b-spike"
    elif price >= 100: return "HIGH",     "b-high"
    elif price >= 0:   return "NORMAL",   "b-normal"
    else:              return "NEGATIVE", "b-low"


# ── Layout helpers ─────────────────────────────────────────────────────────────
def _L(h=300, margin=None):
    m = margin or dict(l=0, r=8, t=8, b=28)
    return dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Mono", font_color=PALETTE["text"],
        height=h, margin=m, hovermode="x unified")
def _xa(): return dict(showgrid=False,color=PALETTE["muted"],tickfont_size=9)
def _ya(t=""): return dict(showgrid=True,gridcolor="rgba(30,45,69,.4)",
    color=PALETTE["muted"],tickfont_size=9,title=t,title_font_size=10)
def _leg(): return dict(orientation="h",y=1.12,font_size=9,bgcolor="rgba(0,0,0,0)")


# ── Gráficos ───────────────────────────────────────────────────────────────────
def chart_multiregion(df_price):
    fig = go.Figure()
    for r in REGIONS:
        s = df_price[df_price["region"]==r].sort_values("ts")
        if s.empty: continue
        fig.add_trace(go.Scatter(x=s["ts"],y=s["price"],mode="lines",name=r,
            line=dict(color=PALETTE.get(r,"#888"),width=1.6),
            hovertemplate=f"<b>{r}: %{{y:.0f}} $/MWh</b><br>%{{x|%H:%M}}<extra></extra>"))
    fig.add_hline(y=300,line=dict(color="rgba(255,59,48,.5)",dash="dot",width=1))
    fig.add_hline(y=0,  line=dict(color="rgba(100,116,139,.4)",width=.8))
    fig.update_layout(**_L(310),legend=_leg(),xaxis=_xa(),yaxis=_ya("$/MWh"))
    return fig


def chart_price_series(df_price, region):
    s = df_price[df_price["region"]==region].sort_values("ts")
    if s.empty: return go.Figure()
    c = PALETTE.get(region,PALETTE["accent"])
    r,g,b = int(c[1:3],16),int(c[3:5],16),int(c[5:7],16)
    fig = go.Figure()
    fig.add_hrect(y0=0,y1=100,fillcolor="rgba(48,209,88,.05)",line_width=0)
    fig.add_hrect(y0=100,y1=300,fillcolor="rgba(255,149,0,.05)",line_width=0)
    fig.add_trace(go.Scatter(x=s["ts"],y=s["price"],mode="lines",name="Precio",
        line=dict(color=c,width=1.8),fill="tozeroy",fillcolor=f"rgba({r},{g},{b},.08)",
        hovertemplate="<b>%{y:.0f} $/MWh</b><br>%{x|%H:%M %d-%b}<extra></extra>"))
    spk = s[s["price"]>=300]
    if not spk.empty:
        fig.add_trace(go.Scatter(x=spk["ts"],y=spk["price"],mode="markers",name="⚡ Spike",
            marker=dict(color=PALETTE["spike"],size=9,symbol="diamond",line=dict(color="white",width=1))))
    fig.add_hline(y=300,line=dict(color="rgba(255,59,48,.5)",dash="dot",width=1),
        annotation_text="Spike ≥300",annotation_font_color=PALETTE["spike"],annotation_font_size=9)
    fig.update_layout(**_L(270),legend=_leg(),xaxis=_xa(),yaxis=_ya("$/MWh"))
    return fig


def chart_gen_donut(df_pw, region):
    if df_pw.empty or "fueltech" not in df_pw.columns: return go.Figure()
    s = df_pw[df_pw["region"]==region]
    if s.empty: return go.Figure()
    latest = s["ts"].max()
    snap   = s[(s["ts"]==latest) & (s["mw"]>0)].copy()
    if snap.empty: return go.Figure()
    snap["label"] = snap["fueltech"].map(FUEL_LABELS).fillna(snap["fueltech"])
    snap["color"] = snap["fueltech"].map(FUEL_COLORS).fillna("#94A3B8")
    snap["ren"]   = snap["fueltech"].isin(RENEWABLE)
    total   = snap["mw"].sum()
    ren_pct = snap[snap["ren"]]["mw"].sum()/total*100 if total>0 else 0
    fig = go.Figure(go.Pie(
        labels=snap["label"],values=snap["mw"].round(0),
        marker=dict(colors=snap["color"],line=dict(color=PALETTE["bg"],width=2)),
        textfont=dict(family="IBM Plex Mono",size=10,color="white"),
        textinfo="label+percent",
        hovertemplate="<b>%{label}</b><br>%{value:.0f} MW (%{percent})<extra></extra>",
        hole=.55,pull=[.05 if r else 0 for r in snap["ren"]],sort=False))
    fig.update_layout(**_L(285, margin=dict(l=0, r=0, t=15, b=0)), showlegend=False,
        annotations=[dict(text=f"<b>{ren_pct:.0f}%</b><br><span style='font-size:9px'>RENEW.</span>",
            x=.5, y=.5, font=dict(size=14, color=PALETTE["accent3"]), showarrow=False)])
    return fig


def chart_gen_stack(df_pw, region):
    if df_pw.empty or "fueltech" not in df_pw.columns: return go.Figure()
    s = df_pw[(df_pw["region"]==region) & (df_pw["mw"]>0)].copy()
    if s.empty: return go.Figure()
    fig = go.Figure()
    for ft in s.groupby("fueltech")["mw"].sum().sort_values(ascending=False).index:
        fd = s[s["fueltech"]==ft].sort_values("ts")
        color = FUEL_COLORS.get(ft,"#94A3B8")
        fig.add_trace(go.Scatter(x=fd["ts"],y=fd["mw"],mode="lines",
            name=FUEL_LABELS.get(ft,ft),stackgroup="g",
            line=dict(width=.5,color=color),fillcolor=color,
            hovertemplate=f"<b>{FUEL_LABELS.get(ft,ft)}: %{{y:.0f}} MW</b><extra></extra>"))
    fig.update_layout(**_L(270),legend=_leg(),xaxis=_xa(),yaxis=_ya("MW"))
    return fig


def chart_pv_vs_price(df_price, df_pw, region):
    if df_price.empty or df_pw.empty or "fueltech" not in df_pw.columns:
        return go.Figure()
    p = df_price[df_price["region"]==region][["ts","price"]].copy()
    g = df_pw[df_pw["region"]==region].copy()
    if p.empty or g.empty: return go.Figure()
    total = g.groupby("ts")["mw"].sum().reset_index(name="total")
    solar = g[g["fueltech"].isin(SOLAR)].groupby("ts")["mw"].sum().reset_index(name="solar")
    m = p.merge(total,on="ts").merge(solar,on="ts",how="left").fillna(0)
    m["pv_pct"] = (m["solar"]/m["total"].replace(0,np.nan)*100).fillna(0)
    def rc(v):
        if v>=300: return PALETTE["spike"]
        elif v>=100: return PALETTE["high"]
        elif v<0: return PALETTE["neg"]
        return PALETTE["normal"]
    fig = go.Figure(go.Scatter(x=m["pv_pct"],y=m["price"],mode="markers",
        marker=dict(color=m["price"].apply(rc),size=4,opacity=.65,line=dict(width=0)),
        hovertemplate="Solar: %{x:.1f}%<br>Precio: %{y:.0f} $/MWh<extra></extra>"))
    valid = m[m["pv_pct"]>0]
    if len(valid)>5:
        z = np.polyfit(valid["pv_pct"],valid["price"],2)
        xl = np.linspace(valid["pv_pct"].min(),valid["pv_pct"].max(),100)
        fig.add_trace(go.Scatter(x=xl,y=np.poly1d(z)(xl),mode="lines",name="Tendencia",
            line=dict(color=PALETTE["accent"],width=2,dash="dash")))
    fig.update_layout(**_L(255),showlegend=False,
        xaxis=dict(title="PV Penetration (%)",showgrid=True,gridcolor="rgba(30,45,69,.3)",
                   tickfont_size=9,color=PALETTE["muted"]),
        yaxis=dict(title="$/MWh",showgrid=True,gridcolor="rgba(30,45,69,.3)",
                   tickfont_size=9,color=PALETTE["muted"]))
    return fig


def chart_mv_multiregion(df_mv):
    """Market value por región — alternativa al precio."""
    fig = go.Figure()
    for r in REGIONS:
        s = df_mv[df_mv["region"]==r].sort_values("ts")
        if s.empty: continue
        fig.add_trace(go.Scatter(x=s["ts"],y=s["mv"]/1e6,mode="lines",name=r,
            line=dict(color=PALETTE.get(r,"#888"),width=1.5),
            hovertemplate=f"<b>{r}: $%{{y:.2f}}M</b><br>%{{x|%H:%M}}<extra></extra>"))
    fig.update_layout(**_L(250),legend=_leg(),xaxis=_xa(),
        yaxis=dict(showgrid=True,gridcolor="rgba(30,45,69,.4)",
                   color=PALETTE["muted"],tickfont_size=9,title="Market Value ($M/intervalo)"))
    return fig


# ── Tarjeta ────────────────────────────────────────────────────────────────────
def region_card(region, price, prev, mv):
    label, css = regime(price)
    delta = price - prev
    sign  = "▲" if delta>0 else "▼"
    dc    = PALETTE["spike"] if delta>0 else PALETTE["accent3"]
    rc    = PALETTE.get(region,PALETTE["text"])
    st.markdown(f"""
    <div class="card">
        <div class="lbl">{region}</div>
        <div class="val" style="color:{rc}">${price:,.0f}<span style="font-size:.7rem;color:{PALETTE['muted']}"> /MWh</span></div>
        <div class="dlt" style="color:{dc}">{sign} {abs(delta):.0f} vs prev</div>
        <div class="dlt" style="color:{PALETTE['muted']}">MV: ${mv/1e6:.2f}M</div>
        <span class="badge {css}">{label}</span>
    </div>""", unsafe_allow_html=True)


# ── Dashboard ──────────────────────────────────────────────────────────────────
def render_nem_dashboard():
    inject_css()

try:
    _secret = st.secrets.get("OPENELECTRICITY_API_KEY", None)
except:
    _secret = None

api_key = (st.session_state.get("oe_key")
           or _secret
           or os.environ.get("OPENELECTRICITY_API_KEY")
           or os.environ.get("OE_API_KEY"))

    if not api_key:
        st.markdown(f"""
        <div style="max-width:560px;margin:3rem auto;font-family:'IBM Plex Mono',monospace;">
        <div style="font-size:1.7rem;color:{PALETTE['accent']};margin-bottom:.5rem;">⚡ NEM Real-Time Monitor</div>
        <div style="color:{PALETTE['muted']};font-size:.82rem;">Ingresá tu API key de Open Electricity.</div>
        </div>""", unsafe_allow_html=True)
        st.info("**Obtener API key gratis:** https://platform.openelectricity.org.au")
        c1,c2 = st.columns([3,1])
        with c1: k = st.text_input("API Key",type="password",placeholder="oe_xxxxxxxx")
        with c2:
            st.markdown("<br>",unsafe_allow_html=True)
            if st.button("Conectar →",use_container_width=True) and k:
                r = requests.get(f"{API_BASE}/me",headers=_hdr(k),timeout=10)
                if r.status_code==200:
                    st.session_state["oe_key"]=k; st.rerun()
                else:
                    st.error(f"❌ Key inválida ({r.status_code})")
        return

    # Header
    now = datetime.now(AEST)
    st.markdown(f"""
    <div style="border-bottom:1px solid {PALETTE['border']};padding-bottom:.8rem;margin-bottom:1.2rem;">
        <div class="nem-title"><span class="dot"></span>NEM · REAL-TIME MONITOR</div>
        <div class="nem-sub">NATIONAL ELECTRICITY MARKET &nbsp;|&nbsp; {now.strftime("%d %b %Y · %H:%M AEST")}
        &nbsp;|&nbsp; <span style="color:{PALETTE['accent3']}">● Open Electricity API v4.4.13 · LIVE</span></div>
    </div>""", unsafe_allow_html=True)

    # Controles
    c1,c2,c3,c4 = st.columns([2,2,1,1])
    with c1: region = st.selectbox("Región focal", REGIONS, index=0)
    with c2: hours  = st.select_slider("Ventana",options=[1,6,12,24],value=6,
                                        format_func=lambda x:f"{x}h")
    with c3:
        st.markdown("<br>",unsafe_allow_html=True)
        if st.button("🔄 Refresh",use_container_width=True):
            st.cache_data.clear(); st.rerun()
    with c4:
        st.markdown("<br>",unsafe_allow_html=True)
        if st.button("🔑 Cambiar key",use_container_width=True):
            st.session_state.pop("oe_key",None); st.rerun()

    # Fetch
    with st.spinner("Cargando datos del NEM..."):
        df_mv  = get_market_value(api_key, hours)
        df_pw  = get_power(api_key, hours, interval="5m")
        df_en  = get_energy(api_key, hours)
        df_em  = get_emissions(api_key, hours)

    if df_mv.empty and df_pw.empty:
        st.error("No se cargaron datos. Verificá la API key.")
        return

    # Precio implícito
    df_price = implied_price(df_mv, df_pw)

    # ── Tarjetas ──
    st.markdown('<div class="sec">⚡ Precio Implícito · Market Value · Todas las Regiones</div>',
                unsafe_allow_html=True)
    cols = st.columns(5)
    for i, r in enumerate(REGIONS):
        sp = df_price[df_price["region"]==r].sort_values("ts")
        sm = df_mv[df_mv["region"]==r].sort_values("ts")
        curr_p = float(sp["price"].iloc[-1]) if not sp.empty else 0.0
        prev_p = float(sp["price"].iloc[-2]) if len(sp)>1 else curr_p
        curr_m = float(sm["mv"].iloc[-1])    if not sm.empty else 0.0
        with cols[i]: region_card(r, curr_p, prev_p, curr_m)

    # ── Precio multi-región ──
    if not df_price.empty:
        st.markdown('<div class="sec">📈 Precio Implícito · Comparación Regional</div>',
                    unsafe_allow_html=True)
        st.plotly_chart(chart_multiregion(df_price),
                        use_container_width=True, config={"displayModeBar":False})
    else:
        st.markdown('<div class="sec">📊 Market Value · Comparación Regional</div>',
                    unsafe_allow_html=True)
        st.plotly_chart(chart_mv_multiregion(df_mv),
                        use_container_width=True, config={"displayModeBar":False})

    # ── Análisis focal ──
    st.markdown(f'<div class="sec">🔍 Análisis Focal · {region}</div>', unsafe_allow_html=True)
    cl, cr = st.columns([3,2])
    with cl:
        if not df_price.empty:
            st.markdown("**Precio Implícito ($/MWh)**")
            st.plotly_chart(chart_price_series(df_price,region),
                            use_container_width=True,config={"displayModeBar":False})
        # Stats
        sp = df_price[df_price["region"]==region] if not df_price.empty else pd.DataFrame()
        if not sp.empty:
            n_spk = int((sp["price"]>=300).sum())
            n_neg = int((sp["price"]<0).sum())
            n_pts = len(sp)
            st.markdown(f"""
            <div class="card">
            <div class="lbl">Estadísticas · {hours}h · {region}</div>
            <div style="font-family:'IBM Plex Mono';font-size:.78rem;line-height:2;margin-top:.3rem;">
                <span style="color:{PALETTE['muted']}">Promedio</span>
                <span style="color:{PALETTE['text']};float:right">${sp['price'].mean():.0f}/MWh</span><br>
                <span style="color:{PALETTE['muted']}">Máximo</span>
                <span style="color:{PALETTE['spike']};float:right">${sp['price'].max():.0f}/MWh</span><br>
                <span style="color:{PALETTE['muted']}">Mínimo</span>
                <span style="color:{PALETTE['neg']};float:right">${sp['price'].min():.0f}/MWh</span><br>
                <span style="color:{PALETTE['muted']}">Spikes ≥300</span>
                <span style="color:{PALETTE['spike']};float:right">{n_spk}/{n_pts} ({n_spk/n_pts*100:.1f}%)</span><br>
                <span style="color:{PALETTE['muted']}">Negativos</span>
                <span style="color:{PALETTE['neg']};float:right">{n_neg} intervalos</span>
            </div></div>""", unsafe_allow_html=True)

    with cr:
        # Energy por región
        if not df_en.empty:
            st.markdown("**Energía Generada (MWh) · Últimas horas**")
            en_r = df_en[df_en["region"]==region].sort_values("ts").tail(24)
            fig_en = go.Figure(go.Bar(x=en_r["ts"],y=en_r["mwh"],
                marker_color=PALETTE.get(region,PALETTE["accent"]),
                hovertemplate="%{y:.0f} MWh<br>%{x|%H:%M}<extra></extra>"))
            fig_en.update_layout(**_L(200),xaxis=_xa(),
                yaxis=dict(showgrid=True,gridcolor="rgba(30,45,69,.4)",
                           color=PALETTE["muted"],tickfont_size=9))
            st.plotly_chart(fig_en,use_container_width=True,config={"displayModeBar":False})

        # Emisiones
        if not df_em.empty:
            st.markdown("**Emisiones (tCO₂eq)**")
            em_r = df_em[df_em["region"]==region].sort_values("ts").tail(24)
            fig_em = go.Figure(go.Scatter(x=em_r["ts"],y=em_r["tco2"],mode="lines+markers",
                line=dict(color="#F87171",width=1.5),
                marker=dict(size=3,color="#F87171"),
                hovertemplate="%{y:.0f} tCO₂<br>%{x|%H:%M}<extra></extra>"))
            fig_em.update_layout(**_L(180),xaxis=_xa(),
                yaxis=dict(showgrid=True,gridcolor="rgba(30,45,69,.4)",
                           color=PALETTE["muted"],tickfont_size=9))
            st.plotly_chart(fig_em,use_container_width=True,config={"displayModeBar":False})

    # ── Generación ──
    st.markdown('<div class="sec">🌱 Mix de Generación · Penetración Renovable</div>',
                unsafe_allow_html=True)
    if not df_pw.empty and "fueltech" in df_pw.columns:
        gm1,gm2,gm3 = st.columns(3)
        with gm1:
            st.markdown(f"**Mix actual · {region}**")
            st.plotly_chart(chart_gen_donut(df_pw,region),
                            use_container_width=True,config={"displayModeBar":False})
        with gm2:
            st.markdown("**Generación en el tiempo**")
            st.plotly_chart(chart_gen_stack(df_pw,region),
                            use_container_width=True,config={"displayModeBar":False})
        with gm3:
            st.markdown("**PV Penetration vs. Precio**")
            st.plotly_chart(chart_pv_vs_price(df_price,df_pw,region),
                            use_container_width=True,config={"displayModeBar":False})
            st.markdown(f"""<div style="font-family:'IBM Plex Mono';font-size:.6rem;
                color:{PALETTE['muted']};margin-top:-.3rem;">
                ↘ Merit-order effect: más solar → menor precio</div>""",
                unsafe_allow_html=True)
    else:
        st.info("Datos de generación cargando. Intentá con ventana de 6h.")

    # ── Tabla régimen ──
    st.markdown('<div class="sec">🗂 Últimos Intervalos · {}</div>'.format(region),
                unsafe_allow_html=True)
    sp = df_price[df_price["region"]==region].sort_values("ts").tail(12) \
         if not df_price.empty else pd.DataFrame()
    if not sp.empty:
        tbl = sp[["ts","price"]].copy()
        tbl["ts"]     = tbl["ts"].dt.strftime("%H:%M  %d-%b")
        tbl["regime"] = sp["price"].apply(lambda x: regime(x)[0]).values
        tbl.columns   = ["Intervalo (AEST)","$/MWh (impl.)","Régimen"]
        tbl["$/MWh (impl.)"] = tbl["$/MWh (impl.)"].map("${:.0f}".format)
        rc_map = {"SPIKE":PALETTE["spike"],"HIGH":PALETTE["high"],
                  "NORMAL":PALETTE["normal"],"NEGATIVE":PALETTE["neg"]}
        styled = (tbl.style
            .applymap(lambda v: f"color:{rc_map.get(v,PALETTE['text'])};font-weight:600",
                      subset=["Régimen"])
            .set_properties(**{"background-color":PALETTE["card"],"color":PALETTE["text"],
                               "font-family":"IBM Plex Mono","font-size":".8rem"}))
        st.dataframe(styled,use_container_width=True,hide_index=True)

    # Footer
    st.markdown(f"""
    <div style="margin-top:2rem;padding-top:.8rem;border-top:1px solid {PALETTE['border']};
        font-family:'IBM Plex Mono';font-size:.6rem;color:{PALETTE['muted']};
        display:flex;justify-content:space-between;flex-wrap:wrap;gap:.4rem;">
        <span>Open Electricity API v4 · AEMO · CC BY-NC 4.0</span>
        <span>{region} · {hours}h · {now.strftime("%H:%M AEST")}</span>
    </div>""", unsafe_allow_html=True)


if __name__ == "__main__":
    st.set_page_config(page_title="NEM Real-Time Monitor",page_icon="⚡",
                       layout="wide",initial_sidebar_state="collapsed")
    render_nem_dashboard()