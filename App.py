import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests, json, re, os, time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit_authenticator as stauth

# ==================================================
# 1. CONFIGURATIE, STYLING & AUTHENTICATIE
# ==================================================
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .block-container { padding-top: 1.4rem; padding-bottom: 2.5rem; }
    div[data-testid="stMetric"] {
        background-color: rgba(128, 128, 128, 0.06);
        border: 1px solid rgba(128, 128, 128, 0.16);
        padding: 13px 15px; border-radius: 12px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.03);
    }
    div[data-testid="stMetricLabel"] > div {
        font-size: 0.80rem !important; font-weight: 600 !important;
        text-transform: uppercase; letter-spacing: 0.03em; opacity: 0.82;
    }
    div[data-testid="stMetricValue"] > div { font-size: 1.38rem !important; font-weight: 700 !important; }
    button[data-baseweb="tab"] { font-weight: 600 !important; font-size: 0.95rem !important; padding: 10px 18px !important; }
    .news-card {
        padding: 10px 14px; border-radius: 8px;
        background-color: rgba(128, 128, 128, 0.05);
        border-left: 4px solid #2563EB; margin-bottom: 8px;
    }
    .news-card a { text-decoration: none; font-weight: 600; }
    .news-card a:hover { text-decoration: underline; }
    .score-badge {
        display: flex; align-items: center; justify-content: center;
        width: 86px; height: 86px; border-radius: 50%;
        font-size: 30px; font-weight: 800; margin: auto;
        box-shadow: 0 4px 12px rgba(0,0,0,0.10);
    }
</style>
""", unsafe_allow_html=True)

credentials = {
    'usernames': {
        'admin': {
            'name': 'Maarten Broekstra',
            'password': '6f9a2b8c4d1e7f3a9b0c5e8d2f1a4c7b9e0d1f3a6b8c5d2e7f9a1b0c4e8d2f'
        }
    }
}
authenticator = stauth.Authenticate(credentials, 'portfolio_dashboard_cookie', 'abcdef', cookie_expiry_days=30)

# ==================================================
# 2. DATABASE & ATOMIC PERSISTENCE
# ==================================================
DB_FILE = 'portfolio_db.json'

def _parse_date_safe(val):
    if not val: return None
    if isinstance(val, datetime): return val.date()
    if isinstance(val, date): return val
    try: return datetime.strptime(str(val).split('T')[0], "%Y-%m-%d").date()
    except Exception: return None

def load_portfolio_from_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                port = data.get('portfolio', [])
                for p in port: p['buy_date'] = _parse_date_safe(p.get('buy_date'))
                wishlist = data.get('wishlist', [])
                dca_history = data.get('dca_history', {})
                transactions = data.get('transactions', [])
                for t in transactions: t['date'] = _parse_date_safe(t.get('date')) or datetime.now().date()
                settings = data.get('settings', {'cash_eur': 0.0, 'max_weight_alert': 15.0})
                return port, wishlist, dca_history, transactions, settings
        except Exception:
            pass
    return [], [], {}, [], {'cash_eur': 0.0, 'max_weight_alert': 15.0}

def save_portfolio_to_db():
    serializable_port = []
    for p in st.session_state.get('portfolio', []):
        p_copy = p.copy()
        if isinstance(p_copy.get('buy_date'), (date, datetime)):
            p_copy['buy_date'] = p_copy['buy_date'].strftime("%Y-%m-%d")
        serializable_port.append(p_copy)

    serializable_tx = []
    for t in st.session_state.get('transactions', []):
        t_copy = t.copy()
        if isinstance(t_copy.get('date'), (date, datetime)):
            t_copy['date'] = t_copy['date'].strftime("%Y-%m-%d")
        serializable_tx.append(t_copy)

    data = {
        'portfolio': serializable_port,
        'wishlist': st.session_state.get('wishlist', []),
        'dca_history': st.session_state.get('dca_history', {}),
        'transactions': serializable_tx,
        'settings': st.session_state.get('settings', {'cash_eur': 0.0, 'max_weight_alert': 15.0})
    }
    tmp_file = f"{DB_FILE}.tmp"
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_file, DB_FILE)
    except Exception as e:
        st.error(f"Kon database niet opslaan: {e}")

# ==================================================
# 3. INLOGGEN
# ==================================================
authenticator.login(location='main')

if st.session_state.get("authentication_status") is False:
    st.error('Gebruikersnaam of wachtwoord is onjuist')
elif st.session_state.get("authentication_status") is None:
    st.warning('Voer je inloggegevens in om Aandelen Dashboard Pro te openen.')
elif st.session_state.get("authentication_status"):

    # ==================================================
    # 4. CONSTANTEN, KLEUREN & BETROUWBARE METADATA FALLBACKS
    # ==================================================
    HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}

    EU_TO_US_MAP = {
        "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
        "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
        "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
        "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
        "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
        "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD", "SHELL.AS": "SHEL",
        "WKL.AS": "WTKWY", "9PDA.DU": "PROSY", "RAND.AS": "RANJY", "AKZA.AS": "AKZOY",
        "KPN.AS": "KKPNY", "NN.AS": "NNGP", "ASM.AS": "ASMIY", "TKWY.AS": "JTKWY"
    }

    ISIN_TO_YF = {
        "IE00BK5BQT80": "VWCE.DE", "US0567521085": "BIDU", "US7223041028": "PDD",
        "NL0012015705": "TKWY.AS", "US2546871060": "DIS", "US4581401001": "INTC",
        "US6541061031": "NKE", "US7170811035": "PFE", "US70450Y1038": "PYPL",
        "US8336351056": "SQM", "NL0010273215": "ASML.AS", "IE0003XJA0J9": "WEBN.DE",
        "NL0000009165": "HEIA.AS", "US01609W1027": "BABA", "US7134481081": "PEP",
        "DE0007664005": "VOW3.DE", "US7960508882": "SSUN.F", "US47215P1066": "JD",
        "US4781601046": "JNJ", "DK0062498333": "NOVO-B.CO", "GB00BP6MXD84": "SHELL.AS",
        "US0079031078": "AMD", "US0231351067": "AMZN", "NL0012866412": "BESI.AS",
        "NL0000395903": "WKL.AS", "US02079K3059": "GOOGL", "US68389X1054": "ORCL",
        "US5949181045": "MSFT", "US5324571083": "LLY", "ES0119037010": "CBAV.MC",
        "KYG4124C1096": "GRAB", "US79466L3024": "CRM", "US5504241051": "LAZR",
        "US5504243032": "LAZR", "US6701002056": "NVO", "KYG875721634": "TCEHY",
        "US81762P1021": "NOW"
    }

    # Gegarandeerde fallback voor Sector & Land als Yahoo Finance faalt bij Europese of ADR tickers
    KNOWN_TICKER_META = {
        "VWCE.DE": {"sector": "Wereld ETF", "country": "Wereldwijd", "type": "ETF", "name": "Vanguard FTSE All-World"},
        "WEBN.DE": {"sector": "Wereld ETF", "country": "Wereldwijd", "type": "ETF", "name": "Amundi Prime All Country World"},
        "ASML.AS": {"sector": "Technology", "country": "Netherlands", "name": "ASML Holding"},
        "ASML": {"sector": "Technology", "country": "Netherlands", "name": "ASML Holding"},
        "BESI.AS": {"sector": "Technology", "country": "Netherlands", "name": "BE Semiconductor"},
        "ASM.AS": {"sector": "Technology", "country": "Netherlands", "name": "ASM International"},
        "WKL.AS": {"sector": "Industrials", "country": "Netherlands", "name": "Wolters Kluwer"},
        "HEIA.AS": {"sector": "Consumer Defensive", "country": "Netherlands", "name": "Heineken"},
        "AD.AS": {"sector": "Consumer Defensive", "country": "Netherlands", "name": "Ahold Delhaize"},
        "TKWY.AS": {"sector": "Consumer Cyclical", "country": "Netherlands", "name": "Just Eat Takeaway"},
        "SHELL.AS": {"sector": "Energy", "country": "United Kingdom", "name": "Shell PLC"},
        "INGA.AS": {"sector": "Financial Services", "country": "Netherlands", "name": "ING Groep"},
        "NN.AS": {"sector": "Financial Services", "country": "Netherlands", "name": "NN Group"},
        "KPN.AS": {"sector": "Communication Services", "country": "Netherlands", "name": "KPN"},
        "NOVO-B.CO": {"sector": "Healthcare", "country": "Denmark", "name": "Novo Nordisk"},
        "NVO": {"sector": "Healthcare", "country": "Denmark", "name": "Novo Nordisk"},
        "CBAV.MC": {"sector": "Healthcare", "country": "Spain", "name": "Clínica Baviera"},
        "VOW3.DE": {"sector": "Consumer Cyclical", "country": "Germany", "name": "Volkswagen AG"},
        "SSUN.F": {"sector": "Technology", "country": "South Korea", "name": "Samsung Electronics"},
        "BIDU": {"sector": "Communication Services", "country": "China", "name": "Baidu"},
        "PDD": {"sector": "Consumer Cyclical", "country": "China", "name": "PDD Holdings"},
        "BABA": {"sector": "Consumer Cyclical", "country": "China", "name": "Alibaba Group"},
        "JD": {"sector": "Consumer Cyclical", "country": "China", "name": "JD.com"},
        "TCEHY": {"sector": "Communication Services", "country": "China", "name": "Tencent Holdings"},
        "GRAB": {"sector": "Technology", "country": "Singapore", "name": "Grab Holdings"},
        "SQM": {"sector": "Basic Materials", "country": "Chile", "name": "SQM"},
        "DIS": {"sector": "Communication Services", "country": "United States", "name": "Walt Disney"},
        "INTC": {"sector": "Technology", "country": "United States", "name": "Intel"},
        "NKE": {"sector": "Consumer Cyclical", "country": "United States", "name": "Nike"},
        "PFE": {"sector": "Healthcare", "country": "United States", "name": "Pfizer"},
        "PYPL": {"sector": "Financial Services", "country": "United States", "name": "PayPal"},
        "PEP": {"sector": "Consumer Defensive", "country": "United States", "name": "PepsiCo"},
        "JNJ": {"sector": "Healthcare", "country": "United States", "name": "Johnson & Johnson"},
        "AMD": {"sector": "Technology", "country": "United States", "name": "AMD"},
        "AMZN": {"sector": "Consumer Cyclical", "country": "United States", "name": "Amazon"},
        "GOOGL": {"sector": "Communication Services", "country": "United States", "name": "Alphabet (Google)"},
        "ORCL": {"sector": "Technology", "country": "United States", "name": "Oracle"},
        "MSFT": {"sector": "Technology", "country": "United States", "name": "Microsoft"},
        "LLY": {"sector": "Healthcare", "country": "United States", "name": "Eli Lilly"},
        "CRM": {"sector": "Technology", "country": "United States", "name": "Salesforce"},
        "NOW": {"sector": "Technology", "country": "United States", "name": "ServiceNow"},
        "LAZR": {"sector": "Consumer Cyclical", "country": "United States", "name": "Luminar"}
    }

    TOOLTIPS = {
        "peg": "Price/Earnings to Growth (PEG).\n🟢 Onder 1.5: Ondergewaardeerd t.o.v. groei.\n🔴 Boven 2.5: Overgewaardeerd.",
        "pe": "Koers/Winst verhouding (P/E).\n🟢 Onder 20: Gunstig gewaardeerd.\n🟠 Boven 30: Groeipremie.",
        "roe": "Rendement op Eigen Vermogen (ROE).\n🟢 Boven 15%: Efficiënt en sterk management.",
        "de": "Schuldratio (Debt/Equity).\n🟢 Onder 1.0: Gezonde balans.\n🔴 Boven 2.0: Hoge schuldenlast.",
        "cr": "Current Ratio (Liquiditeit op korte termijn).\n🟢 Boven 1.5: Ruime buffer.\n🔴 Onder 1.0: Krappe liquiditeit.",
        "div": "Dividend Rendement.\n🟢 Boven 2.5%: Aantrekkelijk inkomen.",
        "margin": "Nettowinstmarge.\n🟢 Boven 20%: Sterke pricing power & moat.",
        "eps": "Winst per aandeel (EPS).\n🟢 Boven 0: Winstgevend bedrijf."
    }

    # Hoog-contrast professioneel kleurenpalet (perfect leesbaar in zowel Light als Dark theme)
    MATTE_COLORS = {
        "blue": "#2563EB",      # Helder Koningsblauw
        "green": "#10B981",     # Smaragdgroen
        "red": "#EF4444",       # Helder Koraalrood
        "yellow": "#F59E0B",    # Amber Goud
        "charcoal": "#475569",  # Leisteen Grijs/Blauw (goed zichtbaar)
        "orange": "#F97316",    # Helder Oranje
        "purple": "#8B5CF6",    # Violet
        "grey": "#64748B"       # Duidelijk Midden-Grijs (voor stippellijnen/verwachtingen)
    }

    SECTOR_COLORS = {
        "Technology": "#2563EB", "Financial Services": "#475569",
        "Healthcare": "#10B981", "Consumer Cyclical": "#F97316",
        "Industrials": "#8B5CF6", "Consumer Defensive": "#06B6D4",
        "Energy": "#F59E0B", "Communication Services": "#EC4899",
        "Real Estate": "#14B8A6", "Basic Materials": "#6366F1",
        "Utilities": "#84CC16", "Wereld ETF": "#64748B",
        "ETF/Overig": "#64748B", "Onbekend": "#94A3B8"
    }

    COUNTRY_COLORS = [
        "#2563EB", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899",
        "#06B6D4", "#F97316", "#64748B", "#14B8A6", "#6366F1"
    ]

    PLOTLY_CONFIG_NO_ZOOM = {'displayModeBar': False, 'scrollZoom': False}

    # ==================================================
    # 5. STATE MANAGEMENT
    # ==================================================
    if 'portfolio' not in st.session_state:
        p, w, d, tx, s = load_portfolio_from_db()
        st.session_state['portfolio'] = p
        st.session_state['wishlist'] = w
        st.session_state['dca_history'] = d
        st.session_state['transactions'] = tx
        st.session_state['settings'] = s

    if 'settings' not in st.session_state:
        st.session_state['settings'] = {'cash_eur': 0.0, 'max_weight_alert': 15.0}
    if 'search_results' not in st.session_state:
        st.session_state['search_results'] = []
    if 'search_main' not in st.session_state:
        st.session_state['search_main'] = ""

    # ==================================================
    # 6. DATA FETCHING & ROBUUSTE VALUTA ENGINE
    # ==================================================
    FALLBACK_EUR_RATES = {
        "EUR": 1.0, "USD": 0.92, "DKK": 0.13405, "GBP": 1.18,
        "CHF": 1.06, "SEK": 0.088, "NOK": 0.085, "CAD": 0.67,
        "HKD": 0.118, "JPY": 0.0061, "AUD": 0.60
    }

    @st.cache_data(ttl=600, show_spinner=False)
    def get_currency_rate(from_curr, to_curr="EUR"):
        if not from_curr:
            return 1.0
        fc = str(from_curr).strip().upper()
        if fc == "GBX" or from_curr == "GBp":
            fc = "GBP"
            factor = 0.01
        else:
            factor = 1.0

        if fc == to_curr.upper():
            return 1.0 * factor

        try:
            pair = f"EUR{fc}=X"
            hist = yf.Ticker(pair).history(period="5d")
            if hist is not None and not hist.empty:
                val = float(hist['Close'].dropna().iloc[-1])
                if val > 0:
                    return (1.0 / val) * factor
        except Exception:
            pass

        try:
            pair_inv = f"{fc}EUR=X"
            hist_inv = yf.Ticker(pair_inv).history(period="5d")
            if hist_inv is not None and not hist_inv.empty:
                val_inv = float(hist_inv['Close'].dropna().iloc[-1])
                if val_inv > 0:
                    return val_inv * factor
        except Exception:
            pass

        return FALLBACK_EUR_RATES.get(fc, 1.0) * factor

    @st.cache_data(ttl=900, show_spinner=False)
    def get_finviz_data(ticker):
        """Haalt robuust alle fundamentele kerncijfers op van Finviz via een universele tabel-parser."""
        d = {}
        if not ticker:
            return d
        clean_t = EU_TO_US_MAP.get(ticker, ticker.split('.')[0] if '.' in ticker else ticker)
        try:
            url = f"https://finviz.com/quote.ashx?t={clean_t}"
            r = requests.get(url, headers=HEADERS, timeout=5)
            if r.status_code == 200:
                def parse(label):
                    pattern = rf'>{re.escape(label)}</td>\s*<td[^>]*>.*?<b>\s*([-+]?\d*\.?\d+)%?\s*</b>'
                    m = re.search(pattern, r.text, re.DOTALL | re.IGNORECASE)
                    if m and m.group(1) not in ('-', ''):
                        try:
                            return float(m.group(1))
                        except Exception:
                            return None
                    return None

                d['peg'] = parse('PEG')
                d['target'] = parse('Target Price')
                d['eps'] = parse('EPS (ttm)')
                d['pe'] = parse('P/E')
                d['forward_pe'] = parse('Forward P/E')
                d['de'] = parse('Debt/Eq')
                d['roe'] = parse('ROE')
                d['margin'] = parse('Profit Margin')
                d['div'] = parse('Dividend %')
                d['cr'] = parse('Current Ratio')
                d['pb'] = parse('P/B')
        except Exception:
            pass
        return d

    def _fetch_yahoo_earnings_raw(ticker):
        """Multi-methode EPS-ophaling: probeert get_earnings_dates, earnings_dates en earnings_history."""
        history = []
        try:
            tk = yf.Ticker(ticker)
            df = None
            try:
                df = tk.get_earnings_dates(limit=12)
            except Exception:
                try:
                    df = tk.earnings_dates
                except Exception:
                    df = None

            if df is not None and not df.empty:
                df = df.copy()
                if getattr(df.index, 'tz', None) is not None:
                    df.index = df.index.tz_localize(None)

                rep_col = next((c for c in df.columns if 'reported' in c.lower()), None)
                est_col = next((c for c in df.columns if 'estimate' in c.lower()), None)
                surp_col = next((c for c in df.columns if 'surprise' in c.lower()), None)

                if rep_col and est_col:
                    df_past = df[df.index <= datetime.now()].dropna(subset=[rep_col, est_col], how='any')
                    df_past = df_past[~df_past.index.duplicated(keep='first')].sort_index(ascending=False).head(4)
                    for date_idx, row in df_past.iterrows():
                        est, rep = float(row[est_col]), float(row[rep_col])
                        surp = row.get(surp_col, np.nan) if surp_col else np.nan
                        if pd.isna(surp) and est != 0:
                            surp = ((rep - est) / abs(est)) * 100.0
                        elif pd.isna(surp):
                            surp = 0.0
                        history.append({
                            'date': date_idx.strftime('%Y-%m-%d'),
                            'quarter_label': f"Q{((date_idx.month-1)//3)+1} '{date_idx.strftime('%y')}",
                            'reported': rep,
                            'estimate': est,
                            'surprise': float(surp)
                        })
                    if history:
                        history.reverse()
                        return history

            # Fallback op tk.earnings_history indien earnings_dates geblokkeerd is
            try:
                eh = tk.earnings_history
                if eh is not None and not eh.empty:
                    eh = eh.dropna(subset=['epsActual', 'epsEstimate']).tail(4)
                    for idx, row in eh.iterrows():
                        est, rep = float(row['epsEstimate']), float(row['epsActual'])
                        surp = ((rep - est) / abs(est) * 100.0) if est != 0 else 0.0
                        dt_str = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)[:10]
                        history.append({
                            'date': dt_str,
                            'quarter_label': dt_str,
                            'reported': rep,
                            'estimate': est,
                            'surprise': float(surp)
                        })
                    if history:
                        return history
            except Exception:
                pass
        except Exception:
            pass
        return None

    @st.cache_data(ttl=3600, show_spinner=False)
    def get_reliable_earnings(ticker):
        # Probeer voor Europese aandelen eerst het US equivalent indien de EU ticker leeg blijft
        data = _fetch_yahoo_earnings_raw(ticker)
        if data:
            return data
        us_ticker = EU_TO_US_MAP.get(ticker)
        if us_ticker:
            data = _fetch_yahoo_earnings_raw(us_ticker)
            if data:
                return data
        if '.' in ticker:
            base_t = ticker.split('.')[0]
            data = _fetch_yahoo_earnings_raw(base_t)
            if data:
                return data
        return []

    def render_classic_earnings_chart(earnings_list, title_text=None):
        """Rendert een strakke, hoog-contrast Earnings grafiek zonder inzoom-problemen."""
        if not earnings_list:
            st.info("Geen kwartaal-earnings bekend voor deze positie (bijvoorbeeld bij ETF's).")
            return
        if title_text:
            st.markdown(f"##### {title_text}")

        df_eps = pd.DataFrame(earnings_list)
        x_labels = df_eps.get('quarter_label', df_eps['date'])

        # Kleur per kwartaal: Groen bij een Beat, Rood bij een Miss
        beat_colors = [MATTE_COLORS["green"] if r >= e else MATTE_COLORS["red"] for r, e in zip(df_eps['reported'], df_eps['estimate'])]

        fig_eps = go.Figure()
        fig_eps.add_trace(go.Scatter(
            x=x_labels, y=df_eps['estimate'],
            name='Analisten Verwachting', mode='markers+lines',
            line=dict(dash='dot', color=MATTE_COLORS["grey"], width=2.5),
            marker=dict(color=MATTE_COLORS["grey"], size=11, symbol='circle', line=dict(width=1.5, color='white')),
            hovertemplate="<b>%{x}</b><br>Verwacht EPS: %{y:.2f}<extra></extra>"
        ))
        fig_eps.add_trace(go.Scatter(
            x=x_labels, y=df_eps['reported'],
            name='Werkelijk Gerapporteerd', mode='markers+lines+text',
            text=[f"{v:.2f}" for v in df_eps['reported']],
            textposition="top center",
            line=dict(color=MATTE_COLORS["blue"], width=3),
            marker=dict(color=beat_colors, size=14, symbol='diamond', line=dict(width=1.5, color='white')),
            hovertemplate="<b>%{x}</b><br>Werkelijk EPS: %{y:.2f}<extra></extra>"
        ))

        all_vals = list(df_eps['estimate']) + list(df_eps['reported'])
        min_y, max_y = min(all_vals), max(all_vals)
        pad = max(0.15, (max_y - min_y) * 0.32)

        fig_eps.update_layout(
            xaxis_title=None, yaxis_title="EPS",
            height=290, margin=dict(t=45, b=20, l=10, r=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1),
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified"
        )
        fig_eps.update_xaxes(fixedrange=True, type='category', showgrid=False)
        fig_eps.update_yaxes(fixedrange=True, range=[min_y - pad * 0.5, max_y + pad], gridcolor='rgba(128,128,128,0.15)')
        st.plotly_chart(fig_eps, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

        latest = df_eps.iloc[-1]
        est, rep, pct = latest['estimate'], latest['reported'], latest['surprise']
        diff = rep - est
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Verwacht EPS (Laatste Q)", f"{est:.2f}")
        m2.metric("Werkelijk EPS", f"{rep:.2f}")
        m3.metric("Verschil", f"{diff:+.2f}")
        m4.metric("Surprise %", f"{pct:+.1f}%", delta="Beat" if pct >= 0 else "Miss", delta_color="normal" if pct >= 0 else "inverse")

    def _calc_div_cagr_from_series(divs):
        try:
            if divs is None or divs.empty:
                return 0.0
            if divs.index.tz is not None:
                divs = divs.copy()
                divs.index = divs.index.tz_localize(None)
            yearly = divs.resample('YE').sum()
            yearly = yearly[yearly > 0]
            if len(yearly) >= 4:
                return float((((yearly.iloc[-1] / yearly.iloc[-4]) ** (1 / 3)) - 1) * 100)
        except Exception:
            pass
        return 0.0

    def _resolve_sector_and_country(ticker, info_dict, is_etf=False):
        """Zorgt ervoor dat Sector en Land ALTIJD gevuld zijn (Yahoo -> US Ticker -> Master Fallback)."""
        known = KNOWN_TICKER_META.get(ticker, {})
        if not known and ticker in EU_TO_US_MAP:
            known = KNOWN_TICKER_META.get(EU_TO_US_MAP[ticker], {})

        sec = info_dict.get('sector') or info_dict.get('category') or known.get('sector')
        cnt = info_dict.get('country') or known.get('country')

        if is_etf:
            sec = sec if sec and sec != 'Onbekend' else 'Wereld ETF'
            cnt = cnt if cnt and cnt != 'Onbekend' else 'Wereldwijd'
        else:
            if not sec or sec == 'Onbekend':
                sec = 'Overig'
            if not cnt or cnt == 'Onbekend':
                if ticker.endswith('.AS'): cnt = 'Netherlands'
                elif ticker.endswith('.DE') or ticker.endswith('.F'): cnt = 'Germany'
                elif ticker.endswith('.CO'): cnt = 'Denmark'
                elif ticker.endswith('.PA'): cnt = 'France'
                elif ticker.endswith('.MC'): cnt = 'Spain'
                elif ticker.endswith('.L'): cnt = 'United Kingdom'
                else: cnt = 'United States'
        return sec, cnt

    def _fetch_single_ticker_extended(ticker):
        try:
            tk = yf.Ticker(ticker)
            info = {}
            try:
                info = tk.info or {}
            except Exception:
                info = {}

            # Vul ontbrekende profielvelden (sector, country, target, beta) aan vanuit US ADR als EU ticker karig is
            us_eq = EU_TO_US_MAP.get(ticker)
            if us_eq and (not info.get('sector') or not info.get('country') or not info.get('targetMeanPrice')):
                try:
                    us_info = yf.Ticker(us_eq).info or {}
                    for k in ['sector', 'country', 'industry', 'beta', 'trailingAnnualDividendYield', 'dividendYield']:
                        if not info.get(k) and us_info.get(k):
                            info[k] = us_info[k]
                except Exception:
                    pass

            fast_price, fast_prev, fast_curr = None, None, None
            try:
                fi = tk.fast_info
                fast_price = getattr(fi, 'last_price', None)
                fast_prev = getattr(fi, 'previous_close', None)
                fast_curr = getattr(fi, 'currency', None)
            except Exception:
                pass

            current_price = info.get('currentPrice') or info.get('regularMarketPrice') or fast_price or info.get('regularMarketPreviousClose')
            previous_close = info.get('regularMarketPreviousClose') or fast_prev

            if not current_price:
                hist_5d = tk.history(period="5d")
                if hist_5d is not None and not hist_5d.empty:
                    current_price = float(hist_5d['Close'].iloc[-1])
                    previous_close = float(hist_5d['Close'].iloc[-2]) if len(hist_5d) > 1 else current_price

            if not current_price:
                return None

            known_meta = KNOWN_TICKER_META.get(ticker, {})
            q_type = 'ETF' if ('ETF' in str(info.get('quoteType', '')).upper() or known_meta.get('type') == 'ETF') else 'Aandeel'

            earnings_history_list = _fetch_yahoo_earnings_raw(ticker)
            if not earnings_history_list and us_eq:
                earnings_history_list = _fetch_yahoo_earnings_raw(us_eq)
            earnings_history_list = earnings_history_list or []
            earnings_surprise = (
                {'date': earnings_history_list[-1]['date'], 'surprise_pct': earnings_history_list[-1]['surprise']}
                if earnings_history_list else None
            )

            dividend_months = []
            div_cagr = 0.0
            try:
                hist_div = tk.dividends
                if (hist_div is None or hist_div.empty) and us_eq:
                    hist_div = yf.Ticker(us_eq).dividends
                if hist_div is not None and not hist_div.empty:
                    if hist_div.index.tz is not None:
                        hist_div.index = hist_div.index.tz_localize(None)
                    div_cagr = _calc_div_cagr_from_series(hist_div)
                    recent_divs = hist_div[hist_div.index > datetime.now() - timedelta(days=365)]
                    for date_val, amount in recent_divs.items():
                        dividend_months.append({'month': int(date_val.month), 'amount': float(amount)})
            except Exception:
                pass

            ytd_start_price = None
            try:
                hist_ytd = tk.history(start=f"{datetime.now().year}-01-01", end=f"{datetime.now().year}-01-10", interval="1d")
                if hist_ytd is not None and not hist_ytd.empty:
                    ytd_start_price = float(hist_ytd['Close'].iloc[0])
            except Exception:
                pass

            day_change_pct = (
                ((float(current_price) - float(previous_close)) / float(previous_close) * 100.0)
                if current_price and previous_close and previous_close > 0 else 0.0
            )

            raw_yield = info.get('trailingAnnualDividendYield') or info.get('dividendYield')
            if raw_yield is not None:
                raw_yield = float(raw_yield)
                if raw_yield > 0.40:
                    raw_yield = raw_yield / 100.0

            raw_curr = info.get('currency') or fast_curr
            if not raw_curr:
                if ticker.endswith('.CO'): raw_curr = 'DKK'
                elif ticker.endswith(('.AS', '.DE', '.PA', '.MC', '.MI', '.BR', '.F', '.DU')): raw_curr = 'EUR'
                elif ticker.endswith('.L'): raw_curr = 'GBP'
                else: raw_curr = 'USD'

            if raw_curr == 'GBp' or str(raw_curr).upper() == 'GBX':
                current_price = float(current_price) / 100.0
                if previous_close: previous_close = float(previous_close) / 100.0
                if ytd_start_price: ytd_start_price = float(ytd_start_price) / 100.0
                raw_curr = 'GBP'

            sec_resolved, cnt_resolved = _resolve_sector_and_country(ticker, info, is_etf=(q_type == 'ETF'))
            fallback_name = known_meta.get('name') or ticker

            return {
                'type': q_type,
                'earnings': earnings_surprise,
                'earnings_history': earnings_history_list,
                'dividend_history': dividend_months,
                'div_cagr': div_cagr,
                'sector': sec_resolved,
                'country': cnt_resolved,
                'live_price': float(current_price),
                'day_change_pct': float(day_change_pct),
                'target_price': info.get('targetMeanPrice'),
                'previous_close': float(previous_close) if previous_close else float(current_price),
                'ytd_start_price': ytd_start_price,
                'currency_code': raw_curr,
                'currency_symbol': '€' if raw_curr == 'EUR' else '$' if raw_curr == 'USD' else raw_curr,
                'shortName': info.get('shortName') or info.get('longName') or fallback_name,
                'longName': info.get('longName') or info.get('shortName') or fallback_name,
                'fallback_div_rate': info.get('trailingAnnualDividendRate') or info.get('dividendRate'),
                'fallback_div_yield': raw_yield,
                'beta': info.get('beta')
            }
        except Exception:
            return None

    @st.cache_data(ttl=1800, show_spinner=False)
    def fetch_batch_portfolio_data(tickers_tuple):
        results = {}
        if not tickers_tuple:
            return results
        max_workers = min(8, len(tickers_tuple))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ticker = {executor.submit(_fetch_single_ticker_extended, t): t for t in tickers_tuple}
            for future in as_completed(future_to_ticker):
                t = future_to_ticker[future]
                try: results[t] = future.result()
                except Exception: results[t] = None
        return results

    @st.cache_data(ttl=86400, show_spinner=False)
    def get_correlation_matrix(tickers_tuple):
        if not tickers_tuple or len(tickers_tuple) < 2:
            return None
        df = pd.DataFrame()
        for t in tickers_tuple:
            try:
                hist = yf.Ticker(t).history(period="1y")
                if not hist.empty:
                    if hist.index.tz is not None: hist.index = hist.index.tz_localize(None)
                    df[t] = hist['Close']
            except Exception:
                pass
        return df.pct_change().corr() if not df.empty and len(df.columns) > 1 else None

    @st.cache_data(ttl=3600, show_spinner=False)
    def get_dca_benchmark_comparison(transactions_json, benchmark_ticker="VWCE.DE", filter_mode="STOCKS_ONLY"):
        """
        Zuivere Cashflow-Matched DCA Benchmark:
        Simuleert wat er was gebeurd als elke netto euro-inleg (in losse aandelen of totaal)
        op exact dezelfde dag in de Benchmark ETF was belegd.
        """
        try:
            txs = json.loads(transactions_json)
            if not txs:
                return None, None
            df_tx = pd.DataFrame(txs)
            df_tx = df_tx[df_tx['type'].isin(['BUY', 'SELL'])].copy()

            if filter_mode == "STOCKS_ONLY" and 'asset_class' in df_tx.columns:
                df_tx = df_tx[df_tx['asset_class'] != 'FUND'].copy()

            if df_tx.empty:
                return None, None

            df_tx['date'] = pd.to_datetime(df_tx['date']).dt.tz_localize(None)
            df_tx = df_tx.sort_values('date')

            start_dt = df_tx['date'].min() - timedelta(days=5)
            bm_tk = yf.Ticker(benchmark_ticker)
            bm_hist = bm_tk.history(start=start_dt.strftime('%Y-%m-%d'))
            if bm_hist is None or bm_hist.empty:
                return None, None
            if bm_hist.index.tz is not None:
                bm_hist.index = bm_hist.index.tz_localize(None)

            bm_curr = "USD" if benchmark_ticker in ("^GSPC", "^NDX") else "EUR"
            bm_rate = get_currency_rate(bm_curr, "EUR")
            bm_close_eur = (bm_hist['Close'] * bm_rate).ffill().bfill()

            full_idx = pd.date_range(start=bm_close_eur.index.min(), end=datetime.now(), freq='D')
            bm_daily = bm_close_eur.reindex(full_idx).ffill().bfill()

            bm_units = 0.0
            net_invested = 0.0
            realized_bm_cash = 0.0
            tx_grouped = df_tx.groupby(df_tx['date'].dt.floor('D'))

            dates_out, invested_out, bm_val_out = [], [], []
            for d in full_idx:
                price_d = float(bm_daily.loc[d])
                if d in tx_grouped.groups:
                    day_rows = tx_grouped.get_group(d)
                    for _, r in day_rows.iterrows():
                        amt = float(r['amount_eur'])
                        if r['type'] == 'BUY' and price_d > 0:
                            # Gebruik eerst eventueel vrijgekomen verkoop-cash, anders nieuwe netto inleg
                            if realized_bm_cash >= amt:
                                realized_bm_cash -= amt
                            else:
                                extra_needed = amt - realized_bm_cash
                                realized_bm_cash = 0.0
                                net_invested += extra_needed
                            bm_units += amt / price_d
                        elif r['type'] == 'SELL' and price_d > 0:
                            units_to_sell = amt / price_d
                            if units_to_sell <= bm_units:
                                bm_units -= units_to_sell
                                realized_bm_cash += amt
                            else:
                                realized_bm_cash += bm_units * price_d
                                bm_units = 0.0
                dates_out.append(d)
                invested_out.append(net_invested)
                bm_val_out.append((bm_units * price_d) + realized_bm_cash)

            df_res = pd.DataFrame({
                'Datum': dates_out,
                'Cumulatieve Netto Inleg (€)': invested_out,
                'DCA in Benchmark (€)': bm_val_out
            })
            return df_res, float(bm_val_out[-1]) if bm_val_out else 0.0
        except Exception:
            return None, None

    @st.cache_data(ttl=1800, show_spinner=False)
    def search_yahoo_strict(query):
        try:
            r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&quotesCount=15", headers=HEADERS, timeout=4)
            return [f"{q.get('symbol')} | {q.get('shortname') or q.get('longname') or q.get('symbol')}" for q in r.json().get('quotes', []) if q.get('symbol')]
        except Exception:
            return []

    @st.cache_data(ttl=1800, show_spinner=False)
    def get_google_news(query):
        try:
            r = requests.get(f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en", headers=HEADERS, timeout=4)
            root = ET.fromstring(r.content)
            return [{"title": i.find('title').text, "link": i.find('link').text, "date": i.find('pubDate').text[:16] if i.find('pubDate') is not None else ""} for i in root.findall('./channel/item')[:4]]
        except Exception:
            return []

    @st.cache_data(ttl=3600, show_spinner=False)
    def get_youtube_video_id(query):
        try:
            r = requests.get(f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}+stock+analysis+{datetime.now().year}", headers=HEADERS, timeout=5)
            vids = re.findall(r"watch\?v=(\S{11})", r.text)
            return vids[0] if vids else None
        except Exception:
            return None

    # ==================================================
    # 7. VERBETERDE DATA ENGINE & AI SCORE (ALTIJD VOLLEDIGE METRICS)
    # ==================================================
    class DataEngine:
        def __init__(self, us_ticker, original_ticker):
            self.us_ticker = us_ticker
            self.original_ticker = original_ticker
            self.yf_obj = yf.Ticker(original_ticker)
            self.yf_info = {}
            try:
                self.yf_info = self.yf_obj.info or {}
            except Exception:
                self.yf_info = {}

            # ALTIJD ontbrekende ratio's en profieldata aanvullen vanuit het Amerikaanse equivalent!
            # (Lost het probleem op dat Europese tickers wel 20 koersvelden hebben maar géén fundamentals)
            if us_ticker and us_ticker != original_ticker:
                try:
                    us_info = yf.Ticker(us_ticker).info or {}
                    price_keys = {'currentPrice', 'regularMarketPrice', 'regularMarketPreviousClose', 'currency', 'fiftyTwoWeekLow', 'fiftyTwoWeekHigh', 'targetMeanPrice', 'bookValue', 'trailingEps', 'forwardEps'}
                    for k, v in us_info.items():
                        if k not in price_keys:
                            if self.yf_info.get(k) is None and v is not None:
                                self.yf_info[k] = v
                except Exception:
                    pass

            # Zorg dat prijs en 52W-range altijd gevuld zijn (ook als tk.info hapert)
            if not (self.yf_info.get('currentPrice') or self.yf_info.get('regularMarketPrice')):
                try:
                    h = self.yf_obj.history(period="1y")
                    if h is not None and not h.empty:
                        self.yf_info['currentPrice'] = float(h['Close'].iloc[-1])
                        self.yf_info['fiftyTwoWeekLow'] = float(h['Low'].min())
                        self.yf_info['fiftyTwoWeekHigh'] = float(h['High'].max())
                except Exception:
                    pass

            # Bereken P/E zelf als prijs en EPS bekend zijn maar trailingPE ontbreekt
            cp = self.yf_info.get('currentPrice') or self.yf_info.get('regularMarketPrice')
            eps_t = self.yf_info.get('trailingEps')
            if not self.yf_info.get('trailingPE') and cp and eps_t and eps_t > 0:
                self.yf_info['trailingPE'] = float(cp) / float(eps_t)

            # Vul Sector en Land gegarandeerd aan
            is_etf = 'ETF' in str(self.yf_info.get('quoteType', '')).upper() or KNOWN_TICKER_META.get(original_ticker, {}).get('type') == 'ETF'
            sec, cnt = _resolve_sector_and_country(original_ticker, self.yf_info, is_etf=is_etf)
            self.yf_info['sector'] = sec
            self.yf_info['country'] = cnt

            self.finviz_data = get_finviz_data(original_ticker)

        def _normalize_value(self, val, is_percent, is_dividend, is_debt_equity):
            if val is None: return None
            if is_debt_equity: return val / 100.0 if val > 5.0 else val
            if is_percent:
                if is_dividend: return val * 100.0 if 0 < abs(val) < 0.35 else val
                return val * 100.0 if abs(val) <= 1.0 and val != 0 else val
            return val

        def get_robust_peg(self):
            values, sources = [], []
            peg_yf = self.yf_info.get('pegRatio') or self.yf_info.get('trailingPegRatio')
            if peg_yf is not None:
                values.append(float(peg_yf)); sources.append("Yahoo")
            if self.finviz_data.get('peg') is not None:
                values.append(float(self.finviz_data.get('peg'))); sources.append("Finviz")
            if not values:
                # Fallback: schat PEG uit P/E en verwachte winstgroei indien beschikbaar
                pe_v = self.yf_info.get('trailingPE') or self.finviz_data.get('pe')
                eg_v = self.yf_info.get('earningsGrowth') or self.yf_info.get('revenueGrowth')
                if pe_v and eg_v and eg_v > 0.02:
                    est_peg = float(pe_v) / (float(eg_v) * 100.0)
                    if 0 < est_peg < 15:
                        return est_peg, "Yahoo (Calc)"
            return (sum(values) / len(values), " & ".join(sources)) if values else (None, "N/A")

        def get_combined_metric(self, key_yf, key_finviz=None, alt_key_yf=None, is_percent=False, is_dividend=False, is_debt_equity=False, use_average=True):
            valid_values, sources = [], []
            raw_yf = self.yf_info.get(key_yf)
            if raw_yf is None and alt_key_yf:
                raw_yf = self.yf_info.get(alt_key_yf)
            if raw_yf is not None:
                norm_v = self._normalize_value(float(raw_yf), is_percent, is_dividend, is_debt_equity)
                if norm_v is not None:
                    valid_values.append(norm_v)
                    sources.append("Yahoo")

            if not use_average and valid_values:
                return valid_values[0], sources[0]

            if key_finviz and self.finviz_data.get(key_finviz) is not None:
                norm_fv = self._normalize_value(float(self.finviz_data.get(key_finviz)), is_percent, is_dividend, is_debt_equity)
                if norm_fv is not None:
                    valid_values.append(norm_fv)
                    sources.append("Finviz")

            if not valid_values:
                return None, "N/A"
            if not use_average:
                return valid_values[0], sources[0]
            return sum(valid_values) / len(valid_values), " & ".join(sources) + (" (Avg)" if len(sources) > 1 else "")

    def calculate_ai_score(data):
        score, reasons = 5.0, []
        eps_val, _ = data['eps']
        if eps_val is not None:
            if eps_val > 0: score += 1.0; reasons.append("Winstgevend")
            else: score -= 2.5; reasons.append("Verlieslatend")
        peg_val, _ = data['peg']
        if peg_val is not None:
            if 0 < peg_val < 1.5: score += 1.0; reasons.append("Gunstige PEG")
            elif peg_val > 2.5: score -= 0.5
            elif peg_val < 0: score -= 1.0
        pe_val, _ = data['pe']
        if pe_val is not None:
            if 0 < pe_val < 20: score += 0.5
            elif pe_val > 40: score -= 0.5
        de_val, _ = data['de']
        if de_val is not None:
            if de_val < 1.0: score += 1.0; reasons.append("Sterke Balans")
            elif de_val > 2.0: score -= 1.0; reasons.append("Hoge Schuld")
        cr_val, _ = data['cr']
        if cr_val is not None:
            if cr_val > 1.5: score += 0.5
            elif cr_val < 1.0: score -= 0.5; reasons.append("Krappe Liquiditeit")
        margin_val, _ = data['margin']
        if margin_val is not None:
            if margin_val > 20: score += 1.0; reasons.append("Hoge Winstmarge")
            elif margin_val < 5: score -= 0.5
        roe_val, _ = data['roe']
        if roe_val is not None:
            if roe_val > 15: score += 0.5; reasons.append("Sterke ROE")
        div_val, _ = data['div']
        if div_val is not None and div_val > 2.5:
            score += 0.5; reasons.append("Mooi Dividend")

        final_score = max(0.0, min(10.0, score))
        summary = "Het bedrijf scoort gemiddeld op fundamentele ratio's."
        if final_score >= 7.5: summary = f"Fundamenteel sterk aandeel! Uitblinkers: {', '.join(reasons[:4]).lower()}."
        elif final_score <= 4.0: summary = f"Aandachtspunt: de fundamentals ogen zwak ({', '.join(reasons[:3]).lower()})."
        elif reasons: summary = f"Gemengd beeld. Opvallende kenmerken: {', '.join(reasons[:3]).lower()}."
        return round(final_score, 1), summary

    @st.cache_data(ttl=1800, show_spinner=False)
    def get_cached_fundamentals(selected_ticker):
        us_equivalent = EU_TO_US_MAP.get(selected_ticker, selected_ticker.split('.')[0] if '.' in selected_ticker else selected_ticker)
        engine = DataEngine(us_equivalent, selected_ticker)
        info = engine.yf_info

        metrics = {
            'peg': engine.get_robust_peg(),
            'pe': engine.get_combined_metric('trailingPE', 'pe', alt_key_yf='forwardPE', use_average=False),
            'div': engine.get_combined_metric('dividendYield', 'div', alt_key_yf='trailingAnnualDividendYield', is_percent=True, is_dividend=True, use_average=False),
            'roe': engine.get_combined_metric('returnOnEquity', 'roe', is_percent=True),
            'de': engine.get_combined_metric('debtToEquity', 'de', is_debt_equity=True),
            'cr': engine.get_combined_metric('currentRatio', 'cr'),
            'pb': engine.get_combined_metric('priceToBook', 'pb'),
            'margin': engine.get_combined_metric('profitMargins', 'margin', is_percent=True),
            'eps': engine.get_combined_metric('trailingEps', 'eps', alt_key_yf='forwardEps')
        }
        score, summary = calculate_ai_score(metrics)
        target_val = info.get('targetMeanPrice')
        if not target_val and selected_ticker == us_equivalent:
            target_val = engine.finviz_data.get('target')

        bvps = info.get('bookValue')
        eps_v = metrics['eps'][0]
        graham_number = float((22.5 * eps_v * bvps) ** 0.5) if (eps_v and eps_v > 0 and bvps and bvps > 0) else None

        return {'info': info, 'metrics': metrics, 'score': score, 'summary': summary, 'target_val': target_val, 'graham_number': graham_number}

    # ==================================================
    # 8. TRADE REPUBLIC CSV PARSER
    # ==================================================
    def parse_tr_csv(uploaded_file):
        df = pd.read_csv(uploaded_file)
        portfolio_dict, dca_history, transactions_list = {}, {}, []
        valid_types = ['BUY', 'SELL', 'DIVIDEND', 'MIGRATION', 'SPLIT', 'REVERSE_SPLIT']
        df_trades = df[df['type'].isin(valid_types) & df['asset_class'].isin(['STOCK', 'FUND'])].copy()

        for _, row in df_trades.sort_values('date').iterrows():
            isin, t_type, a_class = row['symbol'], row['type'], row['asset_class']
            try: shares_raw = float(row['shares'])
            except Exception: shares_raw = 0.0
            try: price = float(row['price'])
            except Exception: price = 0.0
            try: amount_val = float(row['amount'])
            except Exception: amount_val = 0.0
            date_val = _parse_date_safe(row.get('date')) or datetime.now().date()

            if pd.isna(isin) or str(isin).strip() == '':
                continue
            ticker = ISIN_TO_YF.get(isin, isin)

            transactions_list.append({
                'ticker': ticker, 'type': t_type, 'shares': shares_raw,
                'asset_class': a_class,
                'amount_eur': abs(amount_val) if amount_val != 0 else (abs(shares_raw) * price),
                'date': date_val
            })

            if isin not in portfolio_dict:
                portfolio_dict[isin] = {
                    'shares': 0.0, 'total_buys': 0.0, 'total_sells': 0.0,
                    'total_dividends': 0.0, 'class': 'FUND' if a_class == 'FUND' else 'Aandeel',
                    'buy_date': date_val
                }

            month_str = date_val.strftime('%Y-%m')
            cat = 'ETF' if a_class == 'FUND' else 'Aandeel'

            if t_type == 'BUY':
                portfolio_dict[isin]['shares'] += shares_raw
                invested = abs(amount_val) if amount_val != 0 else (shares_raw * price)
                portfolio_dict[isin]['total_buys'] += invested
                if month_str not in dca_history:
                    dca_history[month_str] = {'ETF': 0.0, 'Aandeel': 0.0}
                dca_history[month_str][cat] += invested
            elif t_type == 'SELL':
                abs_shares = abs(shares_raw)
                portfolio_dict[isin]['shares'] = max(0.0, portfolio_dict[isin]['shares'] - abs_shares)
                cash_received = abs(amount_val) if amount_val != 0 else (abs_shares * price)
                portfolio_dict[isin]['total_sells'] += cash_received
            elif t_type == 'DIVIDEND':
                portfolio_dict[isin]['total_dividends'] += abs(amount_val)
            elif t_type in ['MIGRATION', 'SPLIT', 'REVERSE_SPLIT']:
                portfolio_dict[isin]['shares'] += shares_raw

        new_portfolio = []
        for isin, data in portfolio_dict.items():
            ticker = ISIN_TO_YF.get(isin, isin)
            new_portfolio.append({
                'ticker': ticker, 'amount': data['shares'],
                'total_buys': data['total_buys'], 'total_sells': data['total_sells'],
                'total_dividends': data['total_dividends'],
                'net_inleg': data['total_buys'] - data['total_sells'],
                'class': data['class'], 'currency': 'EUR', 'symbol': '€',
                'buy_date': data['buy_date']
            })
        return new_portfolio, dca_history, transactions_list

    # ==================================================
    # 9. SIDEBAR
    # ==================================================
    user_name = st.session_state.get("name", "Gebruiker")
    st.sidebar.markdown(f"### 👤 {user_name}")
    authenticator.logout('Uitloggen', 'sidebar')
    st.sidebar.divider()

    with st.sidebar.expander("⚙️ Portfolio Instellingen & Cash", expanded=False):
        cash_val = st.number_input("Vrije Cash / Kasgeld (€)", min_value=0.0, value=float(st.session_state['settings'].get('cash_eur', 0.0)), step=100.0)
        max_w_val = st.slider("Concentratierisico Waarschuwing (%)", 5, 50, int(st.session_state['settings'].get('max_weight_alert', 15)))
        if st.button("💾 Instellingen Opslaan", use_container_width=True):
            st.session_state['settings']['cash_eur'] = cash_val
            st.session_state['settings']['max_weight_alert'] = float(max_w_val)
            save_portfolio_to_db()
            st.toast("Instellingen opgeslagen!", icon="✅")

    st.sidebar.header("📂 Data & Synchronisatie")
    uploaded_csv = st.sidebar.file_uploader("📈 Importeer Broker CSV", type=['csv'])
    if uploaded_csv and st.sidebar.button("Verwerk CSV", type="primary", use_container_width=True):
        try:
            parsed_portfolio, parsed_dca, parsed_tx = parse_tr_csv(uploaded_csv)
            if parsed_portfolio:
                st.session_state['portfolio'] = parsed_portfolio
                st.session_state['dca_history'] = parsed_dca
                st.session_state['transactions'] = parsed_tx
                save_portfolio_to_db()
                st.sidebar.success("Transacties & historie succesvol ingeladen!")
                time.sleep(0.6); st.rerun()
        except Exception as e:
            st.sidebar.error(f"Fout bij verwerken CSV: {e}")

    st.sidebar.divider()
    col_dl1, col_dl2 = st.sidebar.columns(2)
    if st.session_state.get('portfolio'):
        csv_data = pd.DataFrame(st.session_state['portfolio']).to_csv(index=False).encode('utf-8')
        col_dl1.download_button("⬇️ CSV", data=csv_data, file_name="portfolio_export.csv", mime="text/csv", use_container_width=True)

    json_export = json.dumps({
        'portfolio': st.session_state.get('portfolio', []),
        'wishlist': st.session_state.get('wishlist', []),
        'dca_history': st.session_state.get('dca_history', {}),
        'transactions': st.session_state.get('transactions', []),
        'settings': st.session_state.get('settings', {})
    }, default=str, indent=2)
    col_dl2.download_button("⬇️ JSON", data=json_export, file_name=f"aandelen_backup_{datetime.now().strftime('%Y%m%d')}.json", mime="application/json", use_container_width=True)

    uploaded_file = st.sidebar.file_uploader("⬆️ Herstel Backup (JSON)", type=['json'])
    if uploaded_file and st.sidebar.button("📂 Backup Herstellen", use_container_width=True):
        try:
            loaded_data = json.load(uploaded_file)
            if 'portfolio' in loaded_data:
                for p in loaded_data['portfolio']: p['buy_date'] = _parse_date_safe(p.get('buy_date'))
                st.session_state['portfolio'] = loaded_data['portfolio']
            if 'wishlist' in loaded_data: st.session_state['wishlist'] = loaded_data['wishlist']
            elif 'w' in loaded_data: st.session_state['wishlist'] = loaded_data['w']
            if 'dca_history' in loaded_data: st.session_state['dca_history'] = loaded_data['dca_history']
            if 'transactions' in loaded_data:
                for t in loaded_data['transactions']: t['date'] = _parse_date_safe(t.get('date')) or datetime.now().date()
                st.session_state['transactions'] = loaded_data['transactions']
            if 'settings' in loaded_data: st.session_state['settings'] = loaded_data['settings']
            save_portfolio_to_db()
            st.toast("Backup succesvol hersteld!", icon="✅")
            time.sleep(0.6); st.rerun()
        except Exception as e:
            st.sidebar.error(f"Fout bij laden bestand: {e}")

    st.sidebar.divider()
    if st.sidebar.button("🔄 Live Koersen Verversen", use_container_width=True):
        st.cache_data.clear()
        st.toast("Cache geleegd, live data wordt opgehaald!", icon="🔄")
        st.rerun()

    # ==================================================
    # 10. HOOFDNAVIGATIE TABS
    # ==================================================
    global_total_val_eur = 0.0
    global_total_year_change_eur = 0.0
    global_total_expected_div_eur = 0.0

    tabs = st.tabs(["📊 Portfolio Pro", "🔎 Aandelen Analyse", "❤️ Wishlist & Koopzones", "🇳🇱 Box 3 Belasting"])

    # --------------------------------------------------
    # TAB 1: PORTFOLIO PRO
    # --------------------------------------------------
    with tabs[0]:
        if not st.session_state['portfolio']:
            st.info("👋 Welkom in **Aandelen Dashboard Pro**! Upload links in het menu je **Trade Republic CSV** of een **JSON-backup**, of voeg een aandeel toe via **🔎 Aandelen Analyse**.")
        else:
            all_port_tickers = tuple(sorted(set(item['ticker'] for item in st.session_state['portfolio'])))
            with st.spinner("⚡ Live beursdata en wisselkoersen (EUR/USD/DKK) synchroniseren..."):
                batch_ext_data = fetch_batch_portfolio_data(all_port_tickers)

            portfolio_data = []
            monthly_div_projection = {i: 0.0 for i in range(1, 13)}
            sector_distribution, country_distribution = {}, {}

            total_val_eur = total_inleg_eur = total_winst_eur = total_received_div_eur = 0.0
            total_day_change_eur = total_year_change_eur = total_potential_eur = total_expected_div_eur = closed_positions_profit_eur = 0.0

            etf_stats = {'value': 0.0, 'inleg': 0.0, 'profit': 0.0, 'dividends': 0.0, 'active_inleg': 0.0, 'active_profit': 0.0}
            stock_stats = {'value': 0.0, 'inleg': 0.0, 'profit': 0.0, 'dividends': 0.0, 'active_inleg': 0.0, 'active_profit': 0.0}

            failed_tickers = []
            earnings_history_store, ticker_details_map = {}, {}
            current_year_date = date(datetime.now().year, 1, 1)

            for item in st.session_state['portfolio']:
                ticker = item['ticker']
                amount = float(item.get('amount', 0.0))
                net_inleg_eur = float(item.get('net_inleg', item.get('avg_price', 0.0) * amount))
                dividends_eur = float(item.get('total_dividends', 0.0))
                p_class = item.get('class', 'Aandeel')
                buy_date = _parse_date_safe(item.get('buy_date'))

                ext_data = batch_ext_data.get(ticker)
                data_failed = False
                if ext_data is None:
                    data_failed = True
                    if amount > 0.01: failed_tickers.append(ticker)
                    known_m = KNOWN_TICKER_META.get(ticker, {})
                    is_etf_fb = (p_class == 'FUND' or known_m.get('type') == 'ETF')
                    sec_fb, cnt_fb = _resolve_sector_and_country(ticker, {}, is_etf=is_etf_fb)
                    ext_data = {
                        'live_price': 0.0, 'target_price': 0.0, 'previous_close': 0.0, 'ytd_start_price': 0.0,
                        'type': 'ETF' if is_etf_fb else 'Aandeel', 'sector': sec_fb, 'country': cnt_fb,
                        'currency_code': item.get('currency', 'EUR'), 'currency_symbol': item.get('symbol', '€'),
                        'shortName': known_m.get('name', f"⚠️ {ticker}"), 'longName': known_m.get('name', ticker),
                        'earnings': None, 'dividend_history': [], 'div_cagr': 0.0, 'beta': None
                    }

                live_price = float(ext_data.get('live_price', 0.0))
                curr_code = ext_data.get('currency_code', 'EUR')
                eur_rate = get_currency_rate(curr_code, "EUR")

                val_eur = (live_price * amount * eur_rate) if amount > 0.01 else 0.0
                total_profit_eur = val_eur - net_inleg_eur + dividends_eur

                total_val_eur += val_eur
                total_inleg_eur += net_inleg_eur
                total_winst_eur += total_profit_eur
                total_received_div_eur += dividends_eur

                if amount <= 0.01:
                    closed_positions_profit_eur += total_profit_eur

                is_etf = (ext_data.get('type') == 'ETF' or p_class == 'FUND' or KNOWN_TICKER_META.get(ticker, {}).get('type') == 'ETF')
                target_bucket = etf_stats if is_etf else stock_stats
                target_bucket['inleg'] += max(0.0, net_inleg_eur) if amount > 0.01 else 0.0
                target_bucket['value'] += val_eur
                target_bucket['profit'] += total_profit_eur
                target_bucket['dividends'] += dividends_eur
                if amount > 0.01:
                    target_bucket['active_inleg'] += net_inleg_eur
                    target_bucket['active_profit'] += total_profit_eur

                if amount > 0.01:
                    used_target = ext_data.get('target_price') if ext_data.get('target_price') else live_price
                    total_potential_eur += float(used_target) * amount * eur_rate

                    day_eur_pos = 0.0
                    if not data_failed:
                        if ext_data.get('previous_close'):
                            day_eur_pos = (live_price - float(ext_data['previous_close'])) * amount * eur_rate
                            total_day_change_eur += day_eur_pos

                        ytd_start_native = ext_data.get('ytd_start_price')
                        if buy_date and buy_date >= current_year_date and net_inleg_eur > 0:
                            ytd_ref_eur_per_share = net_inleg_eur / amount
                            total_year_change_eur += ((live_price * eur_rate) - ytd_ref_eur_per_share) * amount
                        elif ytd_start_native and ytd_start_native > 0:
                            total_year_change_eur += (live_price - float(ytd_start_native)) * amount * eur_rate

                    yearly_expected_div_eur = 0.0
                    if not data_failed:
                        if ext_data.get('dividend_history'):
                            for div_pay in ext_data['dividend_history']:
                                payout_eur = float(div_pay['amount']) * amount * eur_rate
                                monthly_div_projection[int(div_pay['month'])] += payout_eur
                                yearly_expected_div_eur += payout_eur
                        elif ext_data.get('fallback_div_rate'):
                            yearly_expected_div_eur = float(ext_data['fallback_div_rate']) * amount * eur_rate
                        elif ext_data.get('fallback_div_yield') and ext_data['fallback_div_yield'] > 0:
                            yearly_expected_div_eur = (live_price * float(ext_data['fallback_div_yield'])) * amount * eur_rate

                    total_expected_div_eur += yearly_expected_div_eur
                    div_cagr = float(ext_data.get('div_cagr', 0.0))

                    sec_name, cnt_name = _resolve_sector_and_country(ticker, ext_data, is_etf=is_etf)
                    if val_eur > 0:
                        sector_distribution[sec_name] = sector_distribution.get(sec_name, 0.0) + val_eur
                        country_distribution[cnt_name] = country_distribution.get(cnt_name, 0.0) + val_eur

                    earnings_str = "-"
                    if ext_data.get('earnings_history'):
                        earnings_history_store[ticker] = ext_data['earnings_history']
                    if ext_data.get('earnings'):
                        surp = ext_data['earnings']['surprise_pct']
                        earnings_str = f"🟢 +{surp:.1f}%" if surp > 0 else f"🔴 {surp:.1f}%"

                    ticker_details_map[ticker] = {
                        'shortName': ext_data.get('shortName', ticker),
                        'longName': ext_data.get('longName', ticker),
                        'beta': ext_data.get('beta')
                    }

                    roi_pct = (total_profit_eur / net_inleg_eur * 100.0) if net_inleg_eur > 0 else 0.0
                    yoc_pct = (yearly_expected_div_eur / net_inleg_eur * 100.0) if net_inleg_eur > 0 else 0.0

                    portfolio_data.append({
                        "Ticker": ticker,
                        "Naam": ext_data.get('shortName', ticker),
                        "Type": "ETF" if is_etf else "Aandeel",
                        "Valuta": curr_code,
                        "Aantal": float(amount),
                        "Netto Inleg (€)": round(net_inleg_eur, 2),
                        "Prijs (€)": round(live_price * eur_rate, 2) if not data_failed else 0.0,
                        "Waarde (€)": round(val_eur, 2),
                        "Dag %": round(ext_data.get('day_change_pct', 0.0), 2),
                        "Dag (€)": round(day_eur_pos, 2),
                        "Ontvangen Div (€)": round(dividends_eur, 2),
                        "Totale Winst (€)": round(total_profit_eur, 2),
                        "Rendement (%)": round(roi_pct, 2),
                        "Verwacht Div/J (€)": round(yearly_expected_div_eur, 2),
                        "YoC (%)": round(yoc_pct, 2),
                        "Div CAGR (%)": round(div_cagr, 2),
                        "Datum": buy_date,
                        "Earnings": earnings_str,
                        "Sector": sec_name,
                        "Land": cnt_name,
                        "Beta": ext_data.get('beta'),
                        "Verwijder": False
                    })

            global_total_val_eur = total_val_eur
            global_total_year_change_eur = total_year_change_eur
            global_total_expected_div_eur = total_expected_div_eur

            if failed_tickers:
                st.warning(f"⚠️ Yahoo Finance kon tijdelijk geen koers ophalen voor: **{', '.join(failed_tickers)}**.")

            for p in portfolio_data:
                p['Weging'] = (p['Waarde (€)'] / total_val_eur * 100.0) if total_val_eur > 0 else 0.0
            portfolio_data.sort(key=lambda x: x["Waarde (€)"], reverse=True)

            # EXECUTIVE KPI BAR
            cash_eur = float(st.session_state['settings'].get('cash_eur', 0.0))
            total_net_worth = total_val_eur + cash_eur
            active_inleg_sum = etf_stats['active_inleg'] + stock_stats['active_inleg']
            pct_total_profit = (total_winst_eur / active_inleg_sum * 100.0) if active_inleg_sum > 0 else 0.0
            yield_pct = (total_expected_div_eur / total_val_eur * 100.0) if total_val_eur > 0 else 0.0
            yoc_total_pct = (total_expected_div_eur / active_inleg_sum * 100.0) if active_inleg_sum > 0 else 0.0
            day_pct_total = (total_day_change_eur / (total_val_eur - total_day_change_eur) * 100.0) if (total_val_eur - total_day_change_eur) > 0 else 0.0
            ytd_pct = (total_year_change_eur / (total_val_eur - total_year_change_eur) * 100.0) if (total_val_eur - total_year_change_eur) > 0 else 0.0

            k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
            k1.metric("Belegd Vermogen", f"€ {total_val_eur:,.2f}", delta=f"Incl. cash: € {total_net_worth:,.0f}" if cash_eur > 0 else f"{len(portfolio_data)} posities", delta_color="off")
            k2.metric("Actieve Inleg", f"€ {active_inleg_sum:,.2f}", delta=f"Ontv. Div: € {total_received_div_eur:,.0f}", delta_color="off")
            k3.metric("Totale Winst", f"€ {total_winst_eur:,.2f}", delta=f"{pct_total_profit:+.2f}%")
            k4.metric("Dag Resultaat", f"€ {total_day_change_eur:+,.2f}", delta=f"{day_pct_total:+.2f}% vandaag")
            k5.metric("Jaar Resultaat (YTD)", f"€ {total_year_change_eur:+,.2f}", delta=f"{ytd_pct:+.2f}% YTD")
            k6.metric("Verwacht Dividend", f"€ {total_expected_div_eur:,.2f}", delta=f"€ {total_expected_div_eur/12:,.0f} / mnd", delta_color="off")
            k7.metric("Dividend Yield", f"{yield_pct:.2f}%", delta=f"YoC: {yoc_total_pct:.2f}%", delta_color="off")

            max_w_limit = float(st.session_state['settings'].get('max_weight_alert', 15.0))
            overweight_stocks = [f"**{p['Naam']}** ({p['Weging']:.1f}%)" for p in portfolio_data if p['Type'] == 'Aandeel' and p['Weging'] > max_w_limit]
            if overweight_stocks:
                st.caption(f"⚠️ **Concentratie-indicator:** Losse posities boven je drempel van {max_w_limit:.0f}%: {', '.join(overweight_stocks)}")

            sub_tabs = st.tabs([
                "🗺️ Overzicht & Heatmap",
                "📋 Posities & Beheer",
                "📈 Benchmark & Risico",
                "💰 Dividend & Cashflow",
                "🎯 Deep-Dive & FIRE Calculator"
            ])

            # SUB-TAB 1: OVERZICHT & HEATMAP
            with sub_tabs[0]:
                col_hm_hdr, col_hm_opt = st.columns([3, 1])
                with col_hm_hdr:
                    st.markdown("#### 🗺️ Portfolio Treemap Overzicht")
                    st.caption("Grootte van het blok = Actuele Waarde (€). Kleur = Dagresultaat (%) of Totale Winst (%).")
                with col_hm_opt:
                    hm_color_mode = st.selectbox("Kleur op basis van:", ["Rendement (%)", "Dag %"], index=0, label_visibility="collapsed")

                if portfolio_data:
                    df_tm = pd.DataFrame(portfolio_data)
                    scale_bound = 3.5 if hm_color_mode == "Dag %" else 35.0
                    fig_tm = px.treemap(
                        df_tm, path=[px.Constant("Portfolio"), 'Type', 'Ticker'],
                        values='Waarde (€)', color=hm_color_mode,
                        color_continuous_scale=['#EF4444', '#334155', '#10B981'],
                        color_continuous_midpoint=0, range_color=[-scale_bound, scale_bound],
                        hover_data={'Naam': True, 'Waarde (€)': ':,.2f', 'Totale Winst (€)': ':,.2f', 'Rendement (%)': ':.1f', 'Dag %': ':.2f', 'Weging': ':.1f'}
                    )
                    fig_tm.update_traces(
                        textinfo="label+percent parent",
                        textfont=dict(size=14, color="white"),
                        hovertemplate="<b>%{customdata[0]} (%{label})</b><br>Waarde: € %{value:,.2f}<br>Winst: € %{customdata[2]:,.2f} (%{customdata[3]:+.1f}%)<br>Dag: %{customdata[4]:+.2f}%<br>Weging: %{customdata[5]:.1f}%<extra></extra>"
                    )
                    fig_tm.update_layout(height=400, margin=dict(t=15, b=10, l=0, r=0), paper_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(fig_tm, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

                st.divider()
                st.markdown("#### 🏆 Performance Benchmark: Losse Aandelen vs. All-World ETF's")
                etf_base = etf_stats['active_inleg'] if etf_stats['active_inleg'] > 0 else 1.0
                stock_base = stock_stats['active_inleg'] if stock_stats['active_inleg'] > 0 else 1.0
                etf_pct = (etf_stats['profit'] / etf_base * 100.0) if etf_stats['active_inleg'] > 0 else 0.0
                stock_pct = (stock_stats['profit'] / stock_base * 100.0) if stock_stats['active_inleg'] > 0 else 0.0
                alpha_pct = stock_pct - etf_pct
                extra_euro_alpha = (stock_stats['active_inleg'] * (alpha_pct / 100.0)) if stock_stats['active_inleg'] > 0 else 0.0

                col_etf, col_stock, col_alpha, col_chart_vs = st.columns([1, 1, 1, 1.8])
                with col_etf:
                    st.metric("All-World ETF Winst", f"€ {etf_stats['profit']:,.2f}", f"{etf_pct:+.2f}% ROI")
                    st.caption(f"Waarde: € {etf_stats['value']:,.0f} | Inleg: € {etf_stats['active_inleg']:,.0f}")
                with col_stock:
                    st.metric("Losse Aandelen Winst", f"€ {stock_stats['profit']:,.2f}", f"{stock_pct:+.2f}% ROI")
                    st.caption(f"Waarde: € {stock_stats['value']:,.0f} | Inleg: € {stock_stats['active_inleg']:,.0f}")
                with col_alpha:
                    st.metric(
                        "Stock-Picking Alpha",
                        f"{alpha_pct:+.2f}%",
                        delta=f"€ {extra_euro_alpha:+,.0f} extra winst vs ETF",
                        delta_color="normal" if alpha_pct >= 0 else "inverse"
                    )
                    st.caption("Outperformance van je losse aandelen t.o.v. je All-World ETF's")
                with col_chart_vs:
                    df_vs = pd.DataFrame({
                        "Categorie": ["All-World ETF's", "Losse Aandelen"],
                        "Rendement (%)": [round(etf_pct, 2), round(stock_pct, 2)]
                    })
                    fig_vs = go.Figure(go.Bar(
                        x=df_vs["Categorie"], y=df_vs["Rendement (%)"],
                        text=[f"{v:+.1f}%" for v in df_vs["Rendement (%)"]],
                        textposition="outside",
                        marker_color=[MATTE_COLORS["charcoal"], MATTE_COLORS["green"] if stock_pct >= etf_pct else MATTE_COLORS["blue"]],
                        width=0.45
                    ))
                    max_r = max(abs(etf_pct), abs(stock_pct), 10.0) * 1.35
                    fig_vs.update_layout(
                        title=dict(text="Rendement op Inleg (%)", font=dict(size=13)),
                        height=230, margin=dict(t=40, b=10, l=10, r=10),
                        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)'
                    )
                    fig_vs.update_xaxes(fixedrange=True)
                    fig_vs.update_yaxes(fixedrange=True, range=[min(0, min(etf_pct, stock_pct)*1.3), max_r], ticksuffix="%")
                    st.plotly_chart(fig_vs, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

                st.divider()
                comp1, comp2, comp3 = st.columns(3)
                with comp1:
                    st.markdown("##### ⚖️ Verdeling: ETF vs. Aandelen")
                    fig_d1 = go.Figure(data=[go.Pie(
                        labels=['All-World ETF', 'Losse Aandelen'],
                        values=[etf_stats['value'], stock_stats['value']],
                        hole=.58, textposition='inside', textinfo='percent+label',
                        marker=dict(colors=[MATTE_COLORS["charcoal"], MATTE_COLORS["blue"]], line=dict(color='white', width=1.5))
                    )])
                    fig_d1.update_layout(showlegend=True, legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5), height=300, margin=dict(t=15, b=45, l=10, r=10), paper_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(fig_d1, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

                with comp2:
                    st.markdown("##### 🏭 Sectorverdeling")
                    if sector_distribution:
                        sec_sorted = sorted(sector_distribution.items(), key=lambda x: x[1], reverse=True)
                        s_labels = [k for k, _ in sec_sorted]
                        s_vals = [v for _, v in sec_sorted]
                        fig_d2 = go.Figure(data=[go.Pie(
                            labels=s_labels, values=s_vals,
                            hole=.58, textposition='inside', textinfo='percent',
                            marker=dict(colors=[SECTOR_COLORS.get(k, '#64748B') for k in s_labels], line=dict(color='white', width=1.5)),
                            hovertemplate="<b>%{label}</b><br>€ %{value:,.2f} (%{percent})<extra></extra>"
                        )])
                        fig_d2.update_layout(showlegend=True, legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5, font=dict(size=11)), height=300, margin=dict(t=15, b=65, l=10, r=10), paper_bgcolor='rgba(0,0,0,0)')
                        st.plotly_chart(fig_d2, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

                with comp3:
                    st.markdown("##### 🌍 Landen & Regioverdeling")
                    if country_distribution:
                        cnt_sorted = sorted(country_distribution.items(), key=lambda x: x[1], reverse=True)
                        c_labels = [k for k, _ in cnt_sorted]
                        c_vals = [v for _, v in cnt_sorted]
                        fig_d3 = go.Figure(data=[go.Pie(
                            labels=c_labels, values=c_vals,
                            hole=.58, textposition='inside', textinfo='percent',
                            marker=dict(colors=COUNTRY_COLORS[:len(c_labels)], line=dict(color='white', width=1.5)),
                            hovertemplate="<b>%{label}</b><br>€ %{value:,.2f} (%{percent})<extra></extra>"
                        )])
                        fig_d3.update_layout(showlegend=True, legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5, font=dict(size=11)), height=300, margin=dict(t=15, b=65, l=10, r=10), paper_bgcolor='rgba(0,0,0,0)')
                        st.plotly_chart(fig_d3, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

            # SUB-TAB 2: POSITIES & BEHEER
            with sub_tabs[1]:
                col_info_tbl, col_closed_metric = st.columns([3, 1])
                with col_info_tbl:
                    st.info("💡 **Interactief Beheer:** Pas *Aantal*, *Netto Inleg (€)* of *Datum* direct aan in de tabel. Alle bedragen zijn automatisch omgerekend naar **EUR**.")
                with col_closed_metric:
                    st.metric("Resultaat Gesloten Posities", f"€ {closed_positions_profit_eur:+,.2f}")

                df_editor_source = pd.DataFrame(portfolio_data)
                column_settings = {
                    "Verwijder": st.column_config.CheckboxColumn("🗑️", default=False, width="small"),
                    "Ticker": st.column_config.TextColumn("Ticker", disabled=True, width="small"),
                    "Naam": st.column_config.TextColumn("Naam", disabled=True),
                    "Type": st.column_config.TextColumn("Type", disabled=True, width="small"),
                    "Sector": st.column_config.TextColumn("Sector", disabled=True),
                    "Land": st.column_config.TextColumn("Land", disabled=True),
                    "Valuta": st.column_config.TextColumn("Val.", disabled=True, width="small"),
                    "Aantal": st.column_config.NumberColumn("Aantal", min_value=0.0, required=True, step=1.0, format="%.4f"),
                    "Netto Inleg (€)": st.column_config.NumberColumn("Netto Inleg (€)", format="€ %.2f", disabled=False),
                    "Prijs (€)": st.column_config.NumberColumn("Koers (€)", format="€ %.2f", disabled=True),
                    "Waarde (€)": st.column_config.NumberColumn("Waarde (€)", format="€ %.2f", disabled=True),
                    "Dag %": st.column_config.NumberColumn("Dag %", format="%+.2f%%", disabled=True),
                    "Ontvangen Div (€)": st.column_config.NumberColumn("Ontv. Div", format="€ %.2f", disabled=True),
                    "Totale Winst (€)": st.column_config.NumberColumn("Totale Winst", format="€ %.2f", disabled=True),
                    "Rendement (%)": st.column_config.NumberColumn("ROI %", format="%+.1f%%", disabled=True),
                    "YoC (%)": st.column_config.NumberColumn("YoC %", format="%.2f%%", disabled=True),
                    "Weging": st.column_config.ProgressColumn("Weging", format="%.1f%%", min_value=0, max_value=max(25.0, float(df_editor_source['Weging'].max()) if not df_editor_source.empty else 100.0)),
                    "Datum": st.column_config.DateColumn("Eerste Aankoop", format="DD-MM-YYYY", disabled=False),
                    "Earnings": st.column_config.TextColumn("Laatste Q", disabled=True)
                }

                edited_df = st.data_editor(
                    df_editor_source, column_config=column_settings,
                    column_order=("Verwijder", "Ticker", "Naam", "Type", "Sector", "Land", "Aantal", "Netto Inleg (€)", "Prijs (€)", "Waarde (€)", "Dag %", "Ontvangen Div (€)", "Totale Winst (€)", "Rendement (%)", "YoC (%)", "Weging", "Datum", "Earnings"),
                    hide_index=True, use_container_width=True, key="main_portfolio_editor"
                )

                editable_cols = ["Verwijder", "Aantal", "Netto Inleg (€)", "Datum"]
                if not df_editor_source[editable_cols].equals(edited_df[editable_cols]):
                    inactive_portfolio = [p for p in st.session_state['portfolio'] if float(p.get('amount', 0)) <= 0.01]
                    new_active_portfolio = []
                    for _, row in edited_df.iterrows():
                        if not row['Verwijder']:
                            orig_item = next((item for item in st.session_state['portfolio'] if item['ticker'] == row['Ticker']), None)
                            updated_item = orig_item.copy() if orig_item else {}
                            updated_item.update({
                                'ticker': row['Ticker'], 'amount': float(row['Aantal']),
                                'net_inleg': float(row['Netto Inleg (€)']),
                                'total_dividends': orig_item.get('total_dividends', 0.0) if orig_item else 0.0,
                                'class': orig_item.get('class', 'Aandeel') if orig_item else 'Aandeel',
                                'currency': orig_item.get('currency', 'EUR') if orig_item else 'EUR',
                                'symbol': orig_item.get('symbol', '€') if orig_item else '€',
                                'buy_date': row['Datum']
                            })
                            new_active_portfolio.append(updated_item)
                    st.session_state['portfolio'] = inactive_portfolio + new_active_portfolio
                    save_portfolio_to_db()
                    st.toast("Portfolio wijzigingen opgeslagen!", icon="💾")
                    st.rerun()

                with st.expander("⚠️ Geavanceerd: Portfolio Volledig Wissen"):
                    confirm_reset = st.checkbox("Ik weet zeker dat ik mijn volledige portfolio wil wissen")
                    if st.button("🗑️ Portfolio Definitief Wissen", disabled=not confirm_reset):
                        st.session_state['portfolio'], st.session_state['dca_history'], st.session_state['transactions'] = [], {}, []
                        save_portfolio_to_db(); st.rerun()

            # SUB-TAB 3: EERLIJKE BENCHMARK & RISICO
            with sub_tabs[2]:
                st.markdown("#### 🏆 Aandelen vs. All-World Benchmark Analyse")
                st.caption("Bekijk zowel het directe rendementsverschil (ROI %) als wat je aankopen waard zouden zijn als je elke aandelenaankoop op die dag in een All-World ETF had gestoken.")

                bm_col1, bm_col2 = st.columns([1.1, 2.9])
                with bm_col1:
                    bm_scope = st.radio(
                        "Wat wil je benchmarken?",
                        ["Alleen Losse Aandelen (Stock Picking)", "Volledige Portfolio (incl. ETF's)"],
                        index=0
                    )
                    bm_choice = st.selectbox(
                        "Vergelijk met Benchmark:",
                        [("VWCE.DE", "Vanguard FTSE All-World (VWCE)"), ("WEBN.DE", "Amundi Prime All-World (WEBN)"), ("^GSPC", "S&P 500 Index (in EUR)")],
                        format_func=lambda x: x[1]
                    )
                    weighted_beta = sum(float(p['Beta']) * p['Waarde (€)'] for p in portfolio_data if p.get('Beta') is not None and p['Waarde (€)'] > 0)
                    beta_w_sum = sum(p['Waarde (€)'] for p in portfolio_data if p.get('Beta') is not None and p['Waarde (€)'] > 0)
                    port_beta = (weighted_beta / beta_w_sum) if beta_w_sum > 0 else 1.0
                    st.metric("Gewogen Portfolio Beta", f"{port_beta:.2f}", delta="Defensiever" if port_beta < 0.95 else "Marktconform" if port_beta <= 1.1 else "Offensiever", delta_color="off")

                with bm_col2:
                    # 1. Directe Rendementsvergelijking op actieve posities
                    stocks_roi = (stock_stats['profit'] / stock_stats['active_inleg'] * 100.0) if stock_stats['active_inleg'] > 0 else 0.0
                    etf_roi = (etf_stats['profit'] / etf_stats['active_inleg'] * 100.0) if etf_stats['active_inleg'] > 0 else 0.0

                    # Indien dezelfde inleg in de All-World ETF in je portfolio had gezeten:
                    hypothetical_etf_profit = stock_stats['active_inleg'] * (etf_roi / 100.0)
                    stock_outperformance_eur = stock_stats['profit'] - hypothetical_etf_profit

                    b_kpi1, b_kpi2, b_kpi3 = st.columns(3)
                    b_kpi1.metric("Rendement Losse Aandelen", f"{stocks_roi:+.2f}%", f"Winst: € {stock_stats['profit']:+,.2f}")
                    b_kpi2.metric("Rendement All-World ETF's", f"{etf_roi:+.2f}%", f"Winst: € {etf_stats['profit']:+,.2f}")
                    b_kpi3.metric(
                        "Outperformance (Losse Aandelen)",
                        f"{stocks_roi - etf_roi:+.2f}%",
                        delta=f"€ {stock_outperformance_eur:+,.2f} vs All-World",
                        delta_color="normal" if stock_outperformance_eur >= 0 else "inverse"
                    )

                    # 2. Cashflow-matched DCA tijdreeks op actieve posities
                    filter_mode = "STOCKS_ONLY" if "Losse Aandelen" in bm_scope else "ALL"
                    if filter_mode == "STOCKS_ONLY":
                        sim_tx = [
                            {'ticker': p['Ticker'], 'type': 'BUY', 'asset_class': 'STOCK', 'amount_eur': float(p['Netto Inleg (€)']), 'date': str(p['Datum'] or '2024-06-01')}
                            for p in portfolio_data if p['Type'] == 'Aandeel' and float(p['Netto Inleg (€)']) > 0
                        ]
                        actual_val_now = stock_stats['value'] + stock_stats['dividends']
                    else:
                        sim_tx = [
                            {'ticker': p['Ticker'], 'type': 'BUY', 'asset_class': 'FUND' if p['Type'] == 'ETF' else 'STOCK', 'amount_eur': float(p['Netto Inleg (€)']), 'date': str(p['Datum'] or '2024-06-01')}
                            for p in portfolio_data if float(p['Netto Inleg (€)']) > 0
                        ]
                        actual_val_now = total_val_eur + total_received_div_eur

                    tx_json = json.dumps(sim_tx, default=str)
                    with st.spinner("DCA-simulatie doorrekenen op de aankoopdatums van je posities..."):
                        df_dca_bm, bm_final_val = get_dca_benchmark_comparison(tx_json, bm_choice[0], filter_mode=filter_mode)

                    if df_dca_bm is not None and not df_dca_bm.empty:
                        fig_bm = go.Figure()
                        fig_bm.add_trace(go.Scatter(
                            x=df_dca_bm['Datum'], y=df_dca_bm['Cumulatieve Netto Inleg (€)'],
                            mode='lines', name='Netto Inleg (€)',
                            line=dict(color=MATTE_COLORS["grey"], width=2, dash='dot')
                        ))
                        fig_bm.add_trace(go.Scatter(
                            x=df_dca_bm['Datum'], y=df_dca_bm['DCA in Benchmark (€)'],
                            mode='lines', name=f'Zelfde inleg in {bm_choice[1]} (€ {bm_final_val:,.0f})',
                            line=dict(color=MATTE_COLORS["orange"], width=2.5)
                        ))
                        fig_bm.add_trace(go.Scatter(
                            x=[df_dca_bm['Datum'].iloc[-1]], y=[actual_val_now],
                            mode='markers+text', name=f'Jouw Waarde incl. Div (€ {actual_val_now:,.0f})',
                            text=[f"  € {actual_val_now:,.0f}"], textposition="middle left",
                            marker=dict(color=MATTE_COLORS["green"], size=14, symbol='diamond', line=dict(width=1.5, color='white'))
                        ))
                        fig_bm.update_layout(
                            height=280, margin=dict(t=40, b=15, l=10, r=20),
                            yaxis_title="Waarde (€)", hovermode="x unified",
                            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                            legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="right", x=1)
                        )
                        fig_bm.update_xaxes(fixedrange=True)
                        fig_bm.update_yaxes(fixedrange=True, gridcolor='rgba(128,128,128,0.15)')
                        st.plotly_chart(fig_bm, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

                st.divider()
                st.markdown("#### 📅 Inleg Tijdlijn (DCA) per Maand")
                dca_hist = st.session_state.get('dca_history', {})
                if dca_hist:
                    df_dca = pd.DataFrame([{"Maand": m, "Type": k, "Inleg (€)": v} for m, cats in dca_hist.items() for k, v in cats.items()]).sort_values(by="Maand")
                    fig_dca = px.bar(df_dca, x='Maand', y='Inleg (€)', color='Type', barmode='stack', color_discrete_map={'ETF': MATTE_COLORS["charcoal"], 'Aandeel': MATTE_COLORS["blue"]})
                    fig_dca.update_layout(height=300, margin=dict(t=35, b=15, l=10, r=10), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    fig_dca.update_xaxes(fixedrange=True)
                    fig_dca.update_yaxes(fixedrange=True, gridcolor='rgba(128,128,128,0.15)')
                    st.plotly_chart(fig_dca, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

                st.divider()
                st.markdown("#### 🔗 Risico & Correlatie Matrix (Top 10 Posities)")
                if len(portfolio_data) > 1:
                    top_10_tickers = tuple(p['Ticker'] for p in portfolio_data[:10])
                    corr_matrix = get_correlation_matrix(top_10_tickers)
                    if corr_matrix is not None:
                        fig_corr = px.imshow(corr_matrix, text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
                        fig_corr.update_layout(height=420, margin=dict(t=25, b=15, l=10, r=10), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
                        fig_corr.update_xaxes(fixedrange=True)
                        fig_corr.update_yaxes(fixedrange=True)
                        st.plotly_chart(fig_corr, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

            # SUB-TAB 4: DIVIDEND & CASHFLOW
            with sub_tabs[3]:
                d_kpi1, d_kpi2, d_kpi3, d_kpi4 = st.columns(4)
                d_kpi1.metric("Verwacht Jaardividend", f"€ {total_expected_div_eur:,.2f}")
                d_kpi2.metric("Gemiddeld per Maand", f"€ {total_expected_div_eur / 12.0:,.2f}")
                d_kpi3.metric("Gemiddeld per Dag", f"€ {total_expected_div_eur / 365.0:,.2f}")
                d_kpi4.metric("Yield on Cost (YoC)", f"{yoc_total_pct:.2f}%", delta=f"Reeds ontvangen: € {total_received_div_eur:,.2f}", delta_color="off")

                col_div1, col_div2 = st.columns(2)
                with col_div1:
                    st.markdown("##### 📅 Verwachte Dividenduitkeringen per Kalendermaand")
                    monthly_vals = [monthly_div_projection[i] for i in range(1, 13)]
                    if sum(monthly_vals) > 0:
                        months = ["Jan", "Feb", "Mrt", "Apr", "Mei", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]
                        avg_m = sum(monthly_vals) / 12.0
                        max_m = max(monthly_vals) * 1.25
                        fig_bar = go.Figure([go.Bar(
                            x=months, y=monthly_vals, marker_color=MATTE_COLORS["green"],
                            text=[f"€{v:,.0f}" if v > 0 else "" for v in monthly_vals], textposition="outside"
                        )])
                        fig_bar.add_hline(y=avg_m, line_dash="dot", line_color=MATTE_COLORS["yellow"], annotation_text=f"Gem: € {avg_m:,.0f}/mnd")
                        fig_bar.update_layout(height=340, margin=dict(t=35, b=15, l=10, r=10), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
                        fig_bar.update_xaxes(fixedrange=True)
                        fig_bar.update_yaxes(fixedrange=True, range=[0, max_m], gridcolor='rgba(128,128,128,0.15)')
                        st.plotly_chart(fig_bar, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)
                    else:
                        st.info("Geen maandelijkse dividendhistorie gevonden.")
                with col_div2:
                    st.markdown("##### 🏆 Verwacht Dividend per Positie & 3-Jaars Groei (CAGR)")
                    df_div_chart = pd.DataFrame([p for p in portfolio_data if p['Verwacht Div/J (€)'] > 0])
                    if not df_div_chart.empty:
                        df_div_chart = df_div_chart.sort_values(by="Verwacht Div/J (€)", ascending=False)
                        max_d = df_div_chart['Verwacht Div/J (€)'].max() * 1.22
                        fig_bar2 = px.bar(
                            df_div_chart, x='Ticker', y='Verwacht Div/J (€)', color='Sector',
                            text_auto='.0f',
                            hover_data={'Naam': True, 'YoC (%)': ':.2f', 'Div CAGR (%)': ':.2f'},
                            color_discrete_map=SECTOR_COLORS
                        )
                        fig_bar2.update_traces(textposition="outside")
                        fig_bar2.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', height=340, margin=dict(t=35, b=15, l=10, r=10), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                        fig_bar2.update_xaxes(fixedrange=True)
                        fig_bar2.update_yaxes(fixedrange=True, range=[0, max_d], gridcolor='rgba(128,128,128,0.15)')
                        st.plotly_chart(fig_bar2, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

            # SUB-TAB 5: DEEP-DIVE & FIRE CALCULATOR
            with sub_tabs[4]:
                st.markdown("#### 🔎 Earnings Surprise & Laatste Nieuws per Positie")
                available_tickers = [item['Ticker'] for item in portfolio_data if item['Type'] == 'Aandeel'] or [item['Ticker'] for item in portfolio_data]
                ticker_to_name = {item['Ticker']: item['Naam'] for item in portfolio_data}

                if available_tickers:
                    sel_port_ticker = st.selectbox("Selecteer een aandeel uit je portfolio:", available_tickers, format_func=lambda x: f"{ticker_to_name.get(x, x)} ({x})")
                    if sel_port_ticker:
                        long_name = ticker_details_map.get(sel_port_ticker, {}).get('longName', sel_port_ticker)
                        dd_col1, dd_col2 = st.columns([3, 2])
                        with dd_col1:
                            e_data = earnings_history_store.get(sel_port_ticker) or get_reliable_earnings(sel_port_ticker)
                            render_classic_earnings_chart(e_data, f"Kwartaalcijfers (EPS Verwacht vs. Werkelijk): {long_name}")
                        with dd_col2:
                            st.markdown(f"##### 📰 Laatste Nieuws: {long_name}")
                            for n in get_google_news(f"{long_name} stock earnings"):
                                st.markdown(f"<div class='news-card'><a href='{n['link']}' target='_blank'>{n['title']}</a><br><small>{n['date']}</small></div>", unsafe_allow_html=True)

                st.divider()
                st.markdown("#### 🧮 Vermogensgroei & FIRE Calculator")
                col_cal1, col_cal2 = st.columns([1, 2])
                with col_cal1:
                    start_cap = st.number_input("Startkapitaal (€)", value=float(round(total_val_eur, 2)), step=1000.0)
                    mnd_inleg = st.number_input("Maandelijkse Inleg (€)", value=500.0, step=50.0)
                    jaren = st.slider("Beleggingshorizon (Jaren)", 1, 40, 20)
                    rendement = st.slider("Verwacht Jaarlijks Rendement (%)", 1.0, 15.0, 7.5, 0.5) / 100.0
                    inflatie_corr = st.checkbox("Corrigeer voor inflatie (2,5% p/j)", value=False)
                with col_cal2:
                    eff_rendement = (rendement - 0.025) if inflatie_corr else rendement
                    years_range = list(range(1, jaren + 1))
                    values_list, invested_list = [], []
                    current_balance = total_invested = start_cap
                    for _ in years_range:
                        for _ in range(12):
                            current_balance = current_balance * (1.0 + eff_rendement / 12.0) + mnd_inleg
                            total_invested += mnd_inleg
                        values_list.append(round(current_balance, 0))
                        invested_list.append(round(total_invested, 0))

                    winst_rente = current_balance - total_invested
                    mc1, mc2, mc3 = st.columns(3)
                    mc1.metric(f"Eindvermogen ({jaren}j)", f"€ {current_balance:,.0f}")
                    mc2.metric("Rendementswinst", f"€ {winst_rente:,.0f}", delta=f"+{(winst_rente/total_invested*100):.0f}% op inleg" if total_invested > 0 else "")
                    mc3.metric("Passief Inkomen (4%-regel)", f"€ {(current_balance * 0.04) / 12.0:,.0f} / mnd")

                    df_comp = pd.DataFrame({"Jaar": years_range, "Portfolio Waarde": values_list, "Eigen Inleg": invested_list})
                    fig_comp = px.area(df_comp, x="Jaar", y=["Eigen Inleg", "Portfolio Waarde"], color_discrete_sequence=[MATTE_COLORS["charcoal"], MATTE_COLORS["green"]])
                    fig_comp.update_layout(height=280, margin=dict(t=35, b=10, l=10, r=10), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    fig_comp.update_xaxes(fixedrange=True)
                    fig_comp.update_yaxes(fixedrange=True, gridcolor='rgba(128,128,128,0.15)')
                    st.plotly_chart(fig_comp, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

    # --------------------------------------------------
    # TAB 2: AANDELEN ANALYSE (VOLLEDIGE METRICS & STABIELE GRAFIEKEN)
    # --------------------------------------------------
    with tabs[1]:
        def update_search():
            st.session_state.search_main = st.session_state.search_input_widget

        col_search, col_period, col_chart_type = st.columns([3, 1, 1])
        with col_search:
            st.text_input(
                "🔍 Zoek een aandeel of ETF op naam, ticker of ISIN (druk op Enter):",
                key="search_input_widget", value=st.session_state.search_main,
                placeholder="Bijv. ASML.AS, NOVO-B.CO, WKL.AS, JD, MSFT...",
                on_change=update_search
            )
            query = st.session_state.search_main.strip()
            if query:
                clean_q = ISIN_TO_YF.get(query.upper(), query)
                if "." in clean_q and " " not in clean_q:
                    st.session_state['search_results'] = [clean_q.upper()]
                else:
                    res = search_yahoo_strict(clean_q)
                    st.session_state['search_results'] = res if res else [clean_q.upper()]
            else:
                st.session_state['search_results'] = []

            selected_ticker = None
            if st.session_state['search_results']:
                selection = st.selectbox("Selecteer beursnotering:", st.session_state['search_results'])
                if selection: selected_ticker = selection.split(" | ")[0].strip()
            elif query:
                selected_ticker = ISIN_TO_YF.get(query.upper(), query.upper())

        with col_period:
            chart_period = st.selectbox("Periode", ["1mo", "6mo", "1y", "2y", "5y", "max"], index=2)
        with col_chart_type:
            chart_style = st.selectbox("Grafiekstijl", ["Lijn + SMA", "Candlestick + SMA"], index=0)

        if selected_ticker:
            try:
                with st.spinner(f"Fundamentele & technische data ophalen voor {selected_ticker}..."):
                    fund_bundle = get_cached_fundamentals(selected_ticker)

                info = fund_bundle['info']
                metrics = fund_bundle['metrics']
                score = fund_bundle['score']
                summary = fund_bundle['summary']
                target_val = fund_bundle['target_val']
                graham_number = fund_bundle['graham_number']

                raw_currency = (info.get('currency') or ('DKK' if selected_ticker.endswith('.CO') else 'EUR')).upper()
                curr_sym = '$' if raw_currency == 'USD' else '€' if raw_currency == 'EUR' else raw_currency
                current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('regularMarketPreviousClose')
                low_52w, high_52w = info.get('fiftyTwoWeekLow'), info.get('fiftyTwoWeekHigh')
                eur_conv = get_currency_rate(raw_currency, "EUR")
                disp_name = info.get('shortName') or info.get('longName') or KNOWN_TICKER_META.get(selected_ticker, {}).get('name') or selected_ticker

                with st.container(border=True):
                    h1, h2, h3, h4, h5 = st.columns([2.2, 1, 1, 1, 1])
                    with h1:
                        st.markdown(f"## {disp_name}")
                        st.caption(f"**Ticker:** `{selected_ticker}` • **Sector:** {info.get('sector', 'Onbekend')} • **Land:** {info.get('country', 'Onbekend')} • **Valuta:** {raw_currency}")
                    with h2:
                        eur_sub = f"€ {current_price * eur_conv:,.2f}" if (current_price and raw_currency != 'EUR') else None
                        st.metric("Huidige Koers", f"{curr_sym} {current_price:,.2f}" if current_price else "-", delta=eur_sub, delta_color="off")
                    with h3:
                        if target_val and current_price:
                            st.metric("Analisten Doel", f"{curr_sym} {target_val:,.2f}", f"{(target_val - current_price) / current_price:+.1%}")
                        else:
                            st.metric("Analisten Doel", "N/A")
                    with h4:
                        if graham_number and current_price:
                            st.metric("Graham Waarde", f"{curr_sym} {graham_number:,.2f}", f"{(graham_number - current_price) / current_price:+.1%}")
                        else:
                            st.metric("52W Bereik", f"{curr_sym}{low_52w:.0f} - {curr_sym}{high_52w:.0f}" if (low_52w and high_52w) else "N/A")
                    with h5:
                        score_col = "#10B981" if score >= 7.0 else "#F59E0B" if score >= 5.0 else "#EF4444"
                        st.markdown(f"<div class='score-badge' style='color:{score_col}; border:4px solid {score_col};'>{score}</div>", unsafe_allow_html=True)

                    st.info(f"🤖 **AI Fundamenteel Oordeel:** {summary}")

                st.markdown("#### 📊 Fundamentele Ratio's (Yahoo Finance, US-Equivalent & Finviz Gecombineerd)")
                def display_metric(label, key, good_fn, bad_fn, fmt="{:.2f}", is_pct=False):
                    val, src = metrics[key]
                    if val is None:
                        st.metric(label, "N/A", help=f"Bron: {src}"); return
                    delta_symbol = "✅ Gezond" if good_fn(val) else "⚠️ Let op" if bad_fn(val) else "⚪ Neutraal"
                    val_display = f"{val:.2f}%" if is_pct else fmt.format(val)
                    st.metric(label, val_display, delta=delta_symbol, delta_color="off", help=f"Exacte waarde: {val:.2f} (Bron: {src})\n\n{TOOLTIPS.get(key, '')}")

                c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
                with c1: display_metric("P/E Ratio", 'pe', lambda x: 0 < x < 20, lambda x: x > 35 or x < 0)
                with c2: display_metric("PEG Ratio", 'peg', lambda x: 0 < x < 1.5, lambda x: x > 2.5 or x < 0)
                with c3: display_metric("EPS (ttm)", 'eps', lambda x: x > 0, lambda x: x < 0)
                with c4: display_metric("Dividend", 'div', lambda x: x > 2.5, lambda x: x == 0, is_pct=True)
                with c5: display_metric("ROE", 'roe', lambda x: x > 15, lambda x: x < 5, is_pct=True)
                with c6: display_metric("Winstmarge", 'margin', lambda x: x > 20, lambda x: x < 5, is_pct=True)
                with c7: display_metric("Debt / Eq", 'de', lambda x: x < 1.0, lambda x: x > 2.0)
                with c8: display_metric("Current Ratio", 'cr', lambda x: x > 1.5, lambda x: x < 1.0)

                col_chart, col_news = st.columns([2.2, 1])
                with col_chart:
                    hist_data = yf.Ticker(selected_ticker).history(period=chart_period)
                    if not hist_data.empty:
                        hist_data['SMA50'] = hist_data['Close'].rolling(window=50).mean()
                        hist_data['SMA200'] = hist_data['Close'].rolling(window=200).mean()

                        delta_p = hist_data['Close'].diff()
                        gain = delta_p.clip(lower=0).rolling(window=14).mean()
                        loss = (-delta_p.clip(upper=0)).rolling(window=14).mean()
                        rs = gain / loss.replace(0, np.nan)
                        hist_data['RSI'] = 100.0 - (100.0 / (1.0 + rs))
                        last_rsi = hist_data['RSI'].iloc[-1] if not pd.isna(hist_data['RSI'].iloc[-1]) else None

                        rsi_badge = f" • Actuele RSI(14): **{last_rsi:.1f}**" if last_rsi is not None else ""
                        st.markdown(f"##### 📈 Koersverloop, SMA 50/200 & RSI ({chart_period}){rsi_badge}")

                        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07, row_heights=[0.73, 0.27])
                        if chart_style == "Candlestick + SMA":
                            fig.add_trace(go.Candlestick(x=hist_data.index, open=hist_data['Open'], high=hist_data['High'], low=hist_data['Low'], close=hist_data['Close'], name="Koers"), row=1, col=1)
                        else:
                            fig.add_trace(go.Scatter(x=hist_data.index, y=hist_data['Close'], mode='lines', name='Slotkoers', line=dict(color=MATTE_COLORS["blue"], width=2.5), fill='tozeroy', fillcolor='rgba(37, 99, 235, 0.10)'), row=1, col=1)

                        fig.add_trace(go.Scatter(x=hist_data.index, y=hist_data['SMA50'], mode='lines', name='SMA 50', line=dict(color=MATTE_COLORS["orange"], width=1.8, dash='dot')), row=1, col=1)
                        fig.add_trace(go.Scatter(x=hist_data.index, y=hist_data['SMA200'], mode='lines', name='SMA 200', line=dict(color=MATTE_COLORS["purple"], width=2.0, dash='dash')), row=1, col=1)
                        fig.add_trace(go.Scatter(x=hist_data.index, y=hist_data['RSI'], mode='lines', name='RSI (14)', line=dict(color=MATTE_COLORS["green"], width=2.0)), row=2, col=1)
                        fig.add_hline(y=70, line_dash="dot", line_color="#EF4444", row=2, col=1)
                        fig.add_hline(y=30, line_dash="dot", line_color="#10B981", row=2, col=1)

                        fig.update_layout(
                            height=460, margin=dict(t=35, b=15, l=10, r=10),
                            xaxis_rangeslider_visible=False, hovermode="x unified",
                            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                        )
                        fig.update_xaxes(fixedrange=True)
                        fig.update_yaxes(title_text=f"Koers ({curr_sym})", fixedrange=True, gridcolor='rgba(128,128,128,0.15)', row=1, col=1)
                        fig.update_yaxes(title_text="RSI", range=[0, 100], fixedrange=True, gridcolor='rgba(128,128,128,0.15)', row=2, col=1)
                        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

                    # Klassieke Earnings Grafiek (Stippellijn + Diamanten)
                    earnings_hist = get_reliable_earnings(selected_ticker)
                    if earnings_hist:
                        st.write("")
                        render_classic_earnings_chart(earnings_hist, "🎯 Laatste Kwartaalcijfers (EPS Verwacht vs. Werkelijk)")

                with col_news:
                    with st.container(border=True):
                        st.markdown("#### ➕ Acties voor Positie")
                        b_col1, b_col2 = st.columns(2)
                        with b_col1:
                            if st.button("❤️ Naar Wishlist", use_container_width=True):
                                if selected_ticker not in st.session_state['wishlist']:
                                    st.session_state['wishlist'].append(selected_ticker)
                                    save_portfolio_to_db()
                                    st.toast(f"{selected_ticker} toegevoegd aan Wishlist!", icon="❤️")
                        with b_col2:
                            with st.popover("💼 Koop / Toevoegen", use_container_width=True):
                                add_qty = st.number_input("Aantal stuks", min_value=0.0001, value=1.0, step=1.0)
                                default_inleg = float((current_price or 0.0) * add_qty * eur_conv)
                                add_inleg = st.number_input("Totale Inleg (€)", min_value=0.0, value=round(default_inleg, 2), step=50.0)
                                add_date = st.date_input("Aankoopdatum", value=datetime.now().date())
                                if st.button("✅ Opslaan in Portfolio", type="primary", use_container_width=True):
                                    existing = next((p for p in st.session_state['portfolio'] if p['ticker'] == selected_ticker), None)
                                    if existing:
                                        existing['amount'] = float(existing.get('amount', 0.0)) + float(add_qty)
                                        existing['net_inleg'] = float(existing.get('net_inleg', 0.0)) + float(add_inleg)
                                    else:
                                        is_etf_type = 'ETF' in str(info.get('quoteType', '')).upper()
                                        st.session_state['portfolio'].append({
                                            'ticker': selected_ticker, 'amount': float(add_qty),
                                            'total_buys': float(add_inleg), 'total_sells': 0.0, 'total_dividends': 0.0,
                                            'net_inleg': float(add_inleg), 'class': 'FUND' if is_etf_type else 'Aandeel',
                                            'currency': 'EUR', 'symbol': '€', 'buy_date': add_date
                                        })
                                    save_portfolio_to_db()
                                    st.toast(f"{selected_ticker} toegevoegd aan portfolio!", icon="✅")
                                    time.sleep(0.5); st.rerun()

                    st.markdown("#### 📰 Laatste Nieuws")
                    for n in get_google_news(f"{disp_name} stock"):
                        st.markdown(f"<div class='news-card'><a href='{n['link']}' target='_blank'>{n['title']}</a><br><small>{n['date']}</small></div>", unsafe_allow_html=True)

                    st.markdown("#### 🎥 Video Analyse")
                    vid_id = get_youtube_video_id(disp_name)
                    if vid_id: st.video(f"https://www.youtube.com/watch?v={vid_id}")
            except Exception as e:
                st.error(f"Kon analyse niet laden voor `{selected_ticker}`: {e}")

    # --------------------------------------------------
    # TAB 3: WISHLIST & KOOPZONES
    # --------------------------------------------------
    with tabs[2]:
        st.markdown("### ❤️ Wishlist, Koopzones & Kwaliteitsscreener")
        w_col1, w_col2 = st.columns([3, 1])
        with w_col1:
            new_wish_ticker = st.text_input("Voeg ticker toe (bijv. ASML.AS, NOVO-B.CO, WKL.AS, GOOGL):", placeholder="Typ een ticker...", label_visibility="collapsed")
        with w_col2:
            if st.button("➕ Toevoegen aan Wishlist", use_container_width=True, type="primary"):
                clean_t = new_wish_ticker.strip().upper()
                if clean_t and clean_t not in st.session_state['wishlist']:
                    st.session_state['wishlist'].append(clean_t)
                    save_portfolio_to_db(); st.rerun()

        if not st.session_state['wishlist']:
            st.info("Je wishlist is momenteel leeg.")
        else:
            wish_rows = []
            with st.spinner("Wishlist fundamentals en koopzones berekenen..."):
                for wt in st.session_state['wishlist']:
                    try:
                        fb = get_cached_fundamentals(wt)
                        w_info, w_metrics = fb['info'], fb['metrics']
                        w_price = w_info.get('currentPrice') or w_info.get('regularMarketPrice') or w_info.get('regularMarketPreviousClose') or 0.0
                        w_low = w_info.get('fiftyTwoWeekLow') or w_price
                        w_high = w_info.get('fiftyTwoWeekHigh') or w_price
                        w_target = fb['target_val']
                        w_curr = w_info.get('currency') or ('DKK' if wt.endswith('.CO') else 'EUR')
                        w_eur_rate = get_currency_rate(w_curr, "EUR")

                        range_pos = ((w_price - w_low) / (w_high - w_low) * 100.0) if (w_high and w_low and w_high > w_low) else 50.0
                        upside = ((w_target - w_price) / w_price * 100.0) if (w_target and w_price > 0) else 0.0

                        if fb['score'] >= 7.0 and (range_pos <= 35.0 or upside >= 15.0): signal = "🟢 Sterke Koopzone"
                        elif range_pos <= 25.0 or upside >= 20.0: signal = "🟢 Dip / Korting"
                        elif range_pos >= 88.0 and upside < 5.0: signal = "🔴 Prijzig / Top 52W"
                        else: signal = "⚪ Hold / Watch"

                        wish_rows.append({
                            "Verwijder": False, "Ticker": wt,
                            "Naam": w_info.get('shortName') or KNOWN_TICKER_META.get(wt, {}).get('name', wt),
                            "Sector": w_info.get('sector', 'Overig'),
                            "Valuta": w_curr, "Koers (€)": round(float(w_price * w_eur_rate), 2),
                            "Koers (Orig)": round(float(w_price), 2),
                            "Doel (Orig)": round(float(w_target), 2) if w_target else None,
                            "Upside (%)": round(float(upside), 1),
                            "52W Positie (%)": round(float(max(0.0, min(100.0, range_pos))), 1),
                            "P/E": round(float(w_metrics['pe'][0]), 1) if w_metrics['pe'][0] is not None else None,
                            "PEG": round(float(w_metrics['peg'][0]), 2) if w_metrics['peg'][0] is not None else None,
                            "Div (%)": round(float(w_metrics['div'][0]), 2) if w_metrics['div'][0] is not None else 0.0,
                            "AI Score": fb['score'], "Signaal": signal
                        })
                    except Exception:
                        wish_rows.append({
                            "Verwijder": False, "Ticker": wt, "Naam": wt, "Sector": "-", "Valuta": "-",
                            "Koers (€)": 0.0, "Koers (Orig)": 0.0, "Doel (Orig)": None, "Upside (%)": 0.0,
                            "52W Positie (%)": 50.0, "P/E": None, "PEG": None, "Div (%)": 0.0, "AI Score": 5.0, "Signaal": "⚠️ Geen data"
                        })

            df_wish = pd.DataFrame(wish_rows)
            edited_wish = st.data_editor(
                df_wish,
                column_config={
                    "Verwijder": st.column_config.CheckboxColumn("🗑️", default=False, width="small"),
                    "Koers (€)": st.column_config.NumberColumn("Koers (€)", format="€ %.2f", disabled=True),
                    "Upside (%)": st.column_config.NumberColumn("Upside %", format="%+.1f%%", disabled=True),
                    "52W Positie (%)": st.column_config.ProgressColumn("52W Range", format="%.0f%%", min_value=0, max_value=100),
                    "AI Score": st.column_config.NumberColumn("AI Score", format="⭐ %.1f", disabled=True)
                },
                hide_index=True, use_container_width=True, key="wishlist_editor"
            )
            if not df_wish[["Verwijder"]].equals(edited_wish[["Verwijder"]]):
                st.session_state['wishlist'] = [row["Ticker"] for _, row in edited_wish.iterrows() if not row["Verwijder"]]
                save_portfolio_to_db(); st.rerun()

    # --------------------------------------------------
    # TAB 4: BOX 3 BELASTING & TEGENBEWIJS CALCULATOR
    # --------------------------------------------------
    with tabs[3]:
        st.markdown("### 🇳🇱 Box 3 Vermogensrendementsheffing & Tegenbewijsregeling")
        BOX3_PARAMS = {
            2024: {"vrijstelling": 57000.0, "spaar_pct": 0.0144, "beleg_pct": 0.0604, "schuld_pct": 0.0261, "tarief": 0.36, "schuld_drempel": 3700.0},
            2025: {"vrijstelling": 57684.0, "spaar_pct": 0.0144, "beleg_pct": 0.0588, "schuld_pct": 0.0262, "tarief": 0.36, "schuld_drempel": 3800.0},
            2026: {"vrijstelling": 59357.0, "spaar_pct": 0.0150, "beleg_pct": 0.0600, "schuld_pct": 0.0270, "tarief": 0.36, "schuld_drempel": 3800.0}
        }

        b_col1, b_col2 = st.columns([1, 1.4])
        with b_col1:
            with st.container(border=True):
                st.markdown("#### 📋 Gegevens Peildatum (1 januari)")
                tax_year = st.selectbox("Belastingjaar", [2026, 2025, 2024], index=0)
                has_partner = st.checkbox("Fiscaal partner (verdubbelt heffingsvrij vermogen)", value=True)
                default_cash = float(st.session_state['settings'].get('cash_eur', 0.0))
                spaar_bedrag = st.number_input("Bank- en spaartegoeden (€)", min_value=0.0, value=max(15000.0, default_cash), step=1000.0)
                beleg_bedrag = st.number_input("Beleggingen op 1 jan (€)", min_value=0.0, value=float(round(global_total_val_eur, 0)), step=1000.0)
                schuld_bedrag = st.number_input("Box 3 Schulden (€)", min_value=0.0, value=0.0, step=1000.0)
                st.divider()
                default_real_return = float(round(global_total_year_change_eur + global_total_expected_div_eur, 0))
                werkelijk_rendement_eur = st.number_input("Werkelijk behaald rendement in dit jaar (€)", value=default_real_return, step=250.0)

        with b_col2:
            p_tax = BOX3_PARAMS[tax_year]
            vrijstelling_totaal = p_tax["vrijstelling"] * (2.0 if has_partner else 1.0)
            schuld_drempel = p_tax["schuld_drempel"] * (2.0 if has_partner else 1.0)
            aftrekbare_schuld = max(0.0, schuld_bedrag - schuld_drempel)

            bezittingen_totaal = spaar_bedrag + beleg_bedrag
            rendementsgrondslag = max(0.0, bezittingen_totaal - aftrekbare_schuld)
            grondslag_sparen_beleggen = max(0.0, rendementsgrondslag - vrijstelling_totaal)

            forf_rendement_netto = max(0.0, (spaar_bedrag * p_tax["spaar_pct"]) + (beleg_bedrag * p_tax["beleg_pct"]) - (aftrekbare_schuld * p_tax["schuld_pct"]))
            rendements_pct = (forf_rendement_netto / rendementsgrondslag) if rendementsgrondslag > 0 else 0.0
            forfaitair_voordeel_box3 = grondslag_sparen_beleggen * rendements_pct
            belasting_forfaitair = forfaitair_voordeel_box3 * p_tax["tarief"]

            belasting_werkelijk = max(0.0, werkelijk_rendement_eur) * p_tax["tarief"] if grondslag_sparen_beleggen > 0 else 0.0
            voordeel_tegenbewijs = max(0.0, belasting_forfaitair - belasting_werkelijk)

            with st.container(border=True):
                st.markdown(f"#### 🧾 Resultaat Box 3 Berekening ({tax_year})")
                t1, t2, t3 = st.columns(3)
                t1.metric("Heffingsvrij Vermogen", f"€ {vrijstelling_totaal:,.0f}")
                t2.metric("Belastbare Grondslag", f"€ {grondslag_sparen_beleggen:,.0f}")
                t3.metric("Fictief Rendement", f"€ {forfaitair_voordeel_box3:,.0f}", f"{rendements_pct*100:.2f}% gewogen", delta_color="off")
                st.divider()
                res1, res2, res3 = st.columns(3)
                res1.metric("Box 3 (Forfaitair)", f"€ {belasting_forfaitair:,.0f}")
                res2.metric("Box 3 (Werkelijk)", f"€ {belasting_werkelijk:,.0f}")
                res3.metric("Besparing Tegenbewijs", f"€ {voordeel_tegenbewijs:,.0f}", delta="Tegenbewijs loont!" if voordeel_tegenbewijs > 0 else "Forfaitair gunstiger", delta_color="normal" if voordeel_tegenbewijs > 0 else "off")

                if grondslag_sparen_beleggen <= 0:
                    st.success("🎉 **Geen Box 3 belasting verschuldigd!** Je vermogen blijft onder het heffingsvrij vermogen.")
                elif voordeel_tegenbewijs > 0:
                    st.info(f"💡 **Tegenbewijsregeling Advies:** Je werkelijke rendement (€ {werkelijk_rendement_eur:,.0f}) is lager dan het forfaitaire rendement (€ {forfaitair_voordeel_box3:,.0f}). Je bespaart **€ {voordeel_tegenbewijs:,.0f}**.")
                else:
                    st.success(f"✅ **Forfaitair stelsel is het voordeligst:** Je betaalt het lagere forfaitaire bedrag van **€ {belasting_forfaitair:,.0f}**.")
