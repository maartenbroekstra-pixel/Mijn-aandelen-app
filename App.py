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

st.markdown('''
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
''', unsafe_allow_html=True)

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
        "MT.AS": "MT", "DSM.AS": "RDSMY", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
        "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD", "SHELL.AS": "SHEL",
        "WKL.AS": "WTKWY", "9PDA.DU": "PROSY", "RAND.AS": "RANJY", "AKZA.AS": "AKZOY",
        "KPN.AS": "KKPNY", "NN.AS": "NNGPF", "ASM.AS": "ASMIY", "TKWY.AS": "JTKWY",
        "PRX.AS": "PROSY", "ADYEN.AS": "ADYEY", "UMG.AS": "UMGNF", "ABN.AS": "AAVMY",
        "AGN.AS": "AEG", "ASRNL.AS": "ASRNF", "IMCD.AS": "IMCDY", "LIGHT.AS": "PHPPY",
        "SAP.DE": "SAP", "SAN.PA": "SNY", "TTE.PA": "TTE", "SU.PA": "SBGSY",
        "RMS.PA": "HESAY", "KER.PA": "PPRUY", "EL.PA": "ESLOY", "BNP.PA": "BNPQY",
        "AZN.L": "AZN", "HSBA.L": "HSBC", "BP.L": "BP", "RIO.L": "RIO", "ULVR.L": "UL",
        "GSK.L": "GSK", "DEO": "DEO", "DGE.L": "DEO", "BATS.L": "BTI", "REL.L": "RELX",
        "REN.AS": "RELX", "NESN.SW": "NSRGY", "ROG.SW": "RHHBY", "NOVN.SW": "NVS"
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

    MATTE_COLORS = {
        "blue": "#2563EB",
        "green": "#10B981",
        "red": "#EF4444",
        "yellow": "#F59E0B",
        "charcoal": "#475569",
        "orange": "#F97316",
        "purple": "#8B5CF6",
        "grey": "#64748B"
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
    # 6. DATA FETCHING, BETA & ROBUUSTE VALUTA ENGINE
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

        tc = str(to_curr).strip().upper()
        if fc == tc:
            return 1.0 * factor

        try:
            pair = f"{tc}{fc}=X"
            hist = yf.Ticker(pair).history(period="5d")
            if hist is not None and not hist.empty:
                val = float(hist['Close'].dropna().iloc[-1])
                if val > 0:
                    return (1.0 / val) * factor
        except Exception:
            pass

        try:
            pair_inv = f"{fc}{tc}=X"
            hist_inv = yf.Ticker(pair_inv).history(period="5d")
            if hist_inv is not None and not hist_inv.empty:
                val_inv = float(hist_inv['Close'].dropna().iloc[-1])
                if val_inv > 0:
                    return val_inv * factor
        except Exception:
            pass

        rate_from_eur = FALLBACK_EUR_RATES.get(fc, 1.0)
        rate_to_eur = FALLBACK_EUR_RATES.get(tc, 1.0)
        return (rate_from_eur / rate_to_eur) * factor

    @st.cache_data(ttl=86400, show_spinner=False)
    def _get_market_benchmark_returns():
        """Haalt 1-jaars dagrendementen op van de wereldmarkt voor empirische Beta-berekening."""
        for bm in ["VWCE.DE", "URTH", "^GSPC"]:
            try:
                h = yf.Ticker(bm).history(period="1y")
                if h is not None and not h.empty and len(h) > 40:
                    if h.index.tz is not None:
                        h.index = h.index.tz_localize(None)
                    s = h['Close'].pct_change().dropna()
                    s.index = s.index.floor('D')
                    return s
            except Exception:
                pass
        return None

    def _calculate_empirical_beta(hist_df):
        """Berekent de werkelijke Beta uit 1-jaars koershistorie als Yahoo Finance geen Beta levert."""
        try:
            if hist_df is None or hist_df.empty or len(hist_df) < 30:
                return None
            bm_ret = _get_market_benchmark_returns()
            if bm_ret is None or bm_ret.empty:
                return None
            h = hist_df.copy()
            if h.index.tz is not None:
                h.index = h.index.tz_localize(None)
            s_ret = h['Close'].pct_change().dropna()
            s_ret.index = s_ret.index.floor('D')
            aligned = pd.concat([s_ret.rename('s'), bm_ret.rename('m')], axis=1).dropna()
            if len(aligned) < 25:
                return None
            var_m = float(np.var(aligned['m'], ddof=1))
            if var_m <= 0:
                return None
            cov_sm = float(np.cov(aligned['s'], aligned['m'], ddof=1)[0, 1])
            beta_calc = cov_sm / var_m
            if -1.0 <= beta_calc <= 4.5:
                return round(beta_calc, 2)
        except Exception:
            pass
        return None

    def _resolve_finviz_ticker(ticker):
        """Geeft uitsluitend een geldige Amerikaanse ticker terug voor Finviz (voorkomt foutieve .split('.')[0] matches)."""
        if not ticker:
            return None
        t_up = ticker.strip().upper()
        if t_up in EU_TO_US_MAP:
            return EU_TO_US_MAP[t_up]
        if '.' not in t_up and '-' not in t_up:
            return t_up
        return None

    @st.cache_data(ttl=900, show_spinner=False)
    def get_finviz_data(ticker):
        """Haalt robuust alle fundamentele kerncijfers op van Finviz indien een geldige US ticker beschikbaar is."""
        d = {}
        clean_t = _resolve_finviz_ticker(ticker)
        if not clean_t:
            return d
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
                d['beta'] = parse('Beta')
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
        data = _fetch_yahoo_earnings_raw(ticker)
        if data:
            return data
        us_ticker = EU_TO_US_MAP.get(ticker)
        if us_ticker:
            data = _fetch_yahoo_earnings_raw(us_ticker)
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

            us_eq = EU_TO_US_MAP.get(ticker)
            if us_eq and (not info.get('sector') or not info.get('country') or not info.get('targetMeanPrice') or info.get('beta') is None):
                try:
                    us_info = yf.Ticker(us_eq).info or {}
                    for k in ['sector', 'country', 'industry', 'beta', 'trailingAnnualDividendYield', 'dividendYield']:
                        if info.get(k) is None and us_info.get(k) is not None:
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

            hist_1y = None
            try:
                hist_1y = tk.history(period="1y")
            except Exception:
                hist_1y = None

            current_price = info.get('currentPrice') or info.get('regularMarketPrice') or fast_price or info.get('regularMarketPreviousClose')
            previous_close = info.get('regularMarketPreviousClose') or fast_prev

            if not current_price and hist_1y is not None and not hist_1y.empty:
                current_price = float(hist_1y['Close'].iloc[-1])
                previous_close = float(hist_1y['Close'].iloc[-2]) if len(hist_1y) > 1 else current_price

            if not current_price:
                return None

            known_meta = KNOWN_TICKER_META.get(ticker, {})
            q_type = 'ETF' if ('ETF' in str(info.get('quoteType', '')).upper() or known_meta.get('type') == 'ETF') else 'Aandeel'

            # Zorg dat Beta ALTIJD echt berekend is (geen statische 1.00 meer)
            beta_val = info.get('beta') or info.get('beta3Year')
            if beta_val is None and hist_1y is not None and not hist_1y.empty:
                beta_val = _calculate_empirical_beta(hist_1y)
            if beta_val is None and us_eq:
                fv_b = get_finviz_data(us_eq).get('beta')
                if fv_b is not None:
                    beta_val = fv_b
            if beta_val is None and q_type == 'ETF':
                beta_val = 1.0

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
                if hist_1y is not None and not hist_1y.empty:
                    h_ytd = hist_1y.copy()
                    if h_ytd.index.tz is not None:
                        h_ytd.index = h_ytd.index.tz_localize(None)
                    h_curr_yr = h_ytd[h_ytd.index >= datetime(datetime.now().year, 1, 1)]
                    if not h_curr_yr.empty:
                        ytd_start_price = float(h_curr_yr['Close'].iloc[0])
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
                'beta': float(beta_val) if beta_val is not None else None
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
        Volledige Tijdreeks Vergelijking:
        Berekent dag-voor-dag:
        1. Cumulatieve Netto Inleg (€)
        2. Waarde als elke aankoop/verkoop op die exacte dag in de Benchmark ETF was gedaan (€)
        3. Werkelijke historische waardeontwikkeling van jouw eigen portefeuille op die dagen (€)
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

            df_tx['date'] = pd.to_datetime(df_tx['date']).dt.tz_localize(None).dt.floor('D')
            df_tx = df_tx.sort_values('date')

            start_dt = max(df_tx['date'].min() - timedelta(days=3), pd.Timestamp(datetime.now() - timedelta(days=365*5)))
            end_dt = pd.Timestamp(datetime.now().date())
            full_idx = pd.date_range(start=start_dt, end=end_dt, freq='D')

            # 1. Haal Benchmark koersreeks op in EUR
            bm_tk = yf.Ticker(benchmark_ticker)
            bm_hist = bm_tk.history(start=(start_dt - timedelta(days=7)).strftime('%Y-%m-%d'))
            if bm_hist is None or bm_hist.empty:
                return None, None
            if bm_hist.index.tz is not None:
                bm_hist.index = bm_hist.index.tz_localize(None)
            bm_hist.index = bm_hist.index.floor('D')
            bm_hist = bm_hist[~bm_hist.index.duplicated(keep='last')]

            bm_curr = "USD" if benchmark_ticker in ("^GSPC", "^NDX") else "EUR"
            bm_rate = get_currency_rate(bm_curr, "EUR")
            bm_daily = (bm_hist['Close'] * bm_rate).reindex(full_idx).ffill().bfill()

            # 2. Haal historische koersen op van alle tickers in de selectie om ook jouw echte portfoliolijn te tekenen
            unique_tickers = [t for t in df_tx['ticker'].dropna().unique() if str(t).strip()]
            ticker_daily_eur = {}
            for t in unique_tickers:
                try:
                    th = yf.Ticker(t).history(start=(start_dt - timedelta(days=7)).strftime('%Y-%m-%d'))
                    if th is not None and not th.empty:
                        if th.index.tz is not None:
                            th.index = th.index.tz_localize(None)
                        th.index = th.index.floor('D')
                        th = th[~th.index.duplicated(keep='last')]
                        if t.endswith('.CO'): t_curr = 'DKK'
                        elif t.endswith(('.AS', '.DE', '.PA', '.MC', '.MI', '.BR', '.F', '.DU')): t_curr = 'EUR'
                        elif t.endswith('.L'): t_curr = 'GBP'
                        else: t_curr = 'USD'
                        t_rate = get_currency_rate(t_curr, "EUR")
                        ticker_daily_eur[t] = (th['Close'] * t_rate).reindex(full_idx).ffill().bfill()
                except Exception:
                    pass

            bm_units = 0.0
            net_invested = 0.0
            realized_bm_cash = 0.0
            holdings_shares = {t: 0.0 for t in unique_tickers}
            holdings_cost_fallback = {t: 0.0 for t in unique_tickers}

            tx_grouped = df_tx.groupby('date')
            dates_out, invested_out, bm_val_out, port_val_out = [], [], [], []

            for d in full_idx:
                price_bm = float(bm_daily.loc[d])
                if d in tx_grouped.groups:
                    day_rows = tx_grouped.get_group(d)
                    for _, r in day_rows.iterrows():
                        amt = float(r.get('amount_eur', 0.0))
                        t_sym = r.get('ticker')
                        sh = float(r.get('shares', 0.0))
                        if sh <= 0 and t_sym in ticker_daily_eur and ticker_daily_eur[t_sym].loc[d] > 0:
                            sh = amt / float(ticker_daily_eur[t_sym].loc[d])

                        if r['type'] == 'BUY' and price_bm > 0:
                            if realized_bm_cash >= amt:
                                realized_bm_cash -= amt
                            else:
                                extra_needed = amt - realized_bm_cash
                                realized_bm_cash = 0.0
                                net_invested += extra_needed
                            bm_units += amt / price_bm
                            if t_sym in holdings_shares:
                                holdings_shares[t_sym] += sh
                                holdings_cost_fallback[t_sym] += amt
                        elif r['type'] == 'SELL' and price_bm > 0:
                            units_to_sell = amt / price_bm
                            if units_to_sell <= bm_units:
                                bm_units -= units_to_sell
                                realized_bm_cash += amt
                            else:
                                realized_bm_cash += bm_units * price_bm
                                bm_units = 0.0
                            if t_sym in holdings_shares:
                                holdings_shares[t_sym] = max(0.0, holdings_shares[t_sym] - abs(sh))
                                holdings_cost_fallback[t_sym] = max(0.0, holdings_cost_fallback[t_sym] - amt)

                # Bereken waarde van eigen portefeuille op dag d
                port_val_d = 0.0
                for t_sym, qty in holdings_shares.items():
                    if qty > 0.0001:
                        if t_sym in ticker_daily_eur and not pd.isna(ticker_daily_eur[t_sym].loc[d]):
                            port_val_d += qty * float(ticker_daily_eur[t_sym].loc[d])
                        else:
                            port_val_d += holdings_cost_fallback.get(t_sym, 0.0)

                dates_out.append(d)
                invested_out.append(net_invested)
                bm_val_out.append((bm_units * price_bm) + realized_bm_cash)
                port_val_out.append(port_val_d)

            df_res = pd.DataFrame({
                'Datum': dates_out,
                'Cumulatieve Netto Inleg (€)': invested_out,
                'DCA in Benchmark (€)': bm_val_out,
                'Jouw Portfolio (€)': port_val_out
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
    # 7. VERBETERDE DATA ENGINE & AI SCORE (GEEN N/A'S MEER)
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

            # Bepaal de lokale valuta en actuele koers van het originele aandeel
            raw_curr = self.yf_info.get('currency')
            if not raw_curr:
                if original_ticker.endswith('.CO'): raw_curr = 'DKK'
                elif original_ticker.endswith(('.AS', '.DE', '.PA', '.MC', '.MI', '.BR', '.F', '.DU')): raw_curr = 'EUR'
                elif original_ticker.endswith('.L'): raw_curr = 'GBP'
                else: raw_curr = 'USD'
            self.yf_info['currency'] = raw_curr

            if not (self.yf_info.get('currentPrice') or self.yf_info.get('regularMarketPrice')):
                try:
                    h = self.yf_obj.history(period="1y")
                    if h is not None and not h.empty:
                        self.yf_info['currentPrice'] = float(h['Close'].iloc[-1])
                        self.yf_info['fiftyTwoWeekLow'] = float(h['Low'].min())
                        self.yf_info['fiftyTwoWeekHigh'] = float(h['High'].max())
                except Exception:
                    pass

            # 1. Vul ontbrekende ratio's én omgerekende per-share waarden aan vanuit een ECHT US-equivalent (ADR/nyse)
            if us_ticker and us_ticker != original_ticker:
                try:
                    us_info = yf.Ticker(us_ticker).info or {}
                    price_only_keys = {'currentPrice', 'regularMarketPrice', 'regularMarketPreviousClose', 'currency', 'fiftyTwoWeekLow', 'fiftyTwoWeekHigh'}
                    per_share_keys = {'trailingEps', 'forwardEps', 'bookValue', 'targetMeanPrice'}

                    orig_p = self.yf_info.get('currentPrice') or self.yf_info.get('regularMarketPrice')
                    us_p = us_info.get('currentPrice') or us_info.get('regularMarketPrice') or us_info.get('regularMarketPreviousClose')
                    # Schaal-ratio tussen EU-notering en US ADR (corrigeert tegelijk voor valuta én ADR-bundeling)
                    price_scale = (float(orig_p) / float(us_p)) if (orig_p and us_p and float(us_p) > 0) else get_currency_rate(us_info.get('currency', 'USD'), raw_curr)

                    for k, v in us_info.items():
                        if v is None:
                            continue
                        if k not in price_only_keys and k not in per_share_keys:
                            if self.yf_info.get(k) is None:
                                self.yf_info[k] = v
                        elif k in per_share_keys and self.yf_info.get(k) is None:
                            try:
                                self.yf_info[k] = float(v) * price_scale
                            except Exception:
                                pass
                except Exception:
                    pass

            # 2. Jaarrekening & Balans Fallback: als Yahoo .info bij Europese aandelen leeg is, bereken zelf uit statements!
            self._enrich_from_financial_statements()

            # 3. Dividend Fallback vanuit daadwerkelijke uitkeringen (tk.dividends) als dividendYield ontbreekt
            cp = self.yf_info.get('currentPrice') or self.yf_info.get('regularMarketPrice')
            if self.yf_info.get('dividendYield') is None and self.yf_info.get('trailingAnnualDividendYield') is None and cp and cp > 0:
                try:
                    divs = self.yf_obj.dividends
                    if divs is not None and not divs.empty:
                        if divs.index.tz is not None:
                            divs = divs.copy()
                            divs.index = divs.index.tz_localize(None)
                        last_yr_div = float(divs[divs.index > datetime.now() - timedelta(days=365)].sum())
                        if last_yr_div > 0:
                            self.yf_info['dividendYield'] = last_yr_div / float(cp)
                except Exception:
                    pass

            # 4. Onderlinge wiskundige kruisberekening (P/E <-> EPS en P/B <-> BookValue)
            eps_t = self.yf_info.get('trailingEps')
            pe_t = self.yf_info.get('trailingPE') or self.yf_info.get('forwardPE')
            if not self.yf_info.get('trailingPE') and cp and eps_t and eps_t != 0:
                self.yf_info['trailingPE'] = float(cp) / float(eps_t)
            elif self.yf_info.get('trailingEps') is None and cp and pe_t and pe_t > 0:
                self.yf_info['trailingEps'] = float(cp) / float(pe_t)

            pb_t = self.yf_info.get('priceToBook')
            bv_t = self.yf_info.get('bookValue')
            if self.yf_info.get('bookValue') is None and cp and pb_t and pb_t > 0:
                self.yf_info['bookValue'] = float(cp) / float(pb_t)
            elif self.yf_info.get('priceToBook') is None and cp and bv_t and bv_t > 0:
                self.yf_info['priceToBook'] = float(cp) / float(bv_t)

            is_etf = 'ETF' in str(self.yf_info.get('quoteType', '')).upper() or KNOWN_TICKER_META.get(original_ticker, {}).get('type') == 'ETF'
            sec, cnt = _resolve_sector_and_country(original_ticker, self.yf_info, is_etf=is_etf)
            self.yf_info['sector'] = sec
            self.yf_info['country'] = cnt

            self.finviz_data = get_finviz_data(original_ticker)

        def _enrich_from_financial_statements(self):
            """Berekent ROE, Winstmarge, Debt/Equity, Current Ratio en EPS rechtstreeks uit de Yahoo jaarrekening als .info ze mist."""
            needed = ('returnOnEquity', 'profitMargins', 'debtToEquity', 'currentRatio', 'trailingEps')
            if all(self.yf_info.get(k) is not None for k in needed):
                return
            try:
                inc = self.yf_obj.income_stmt
                bal = self.yf_obj.balance_sheet

                def _get_row(df, candidates):
                    if df is None or df.empty:
                        return None
                    for c in candidates:
                        if c in df.index:
                            s = df.loc[c].dropna()
                            if not s.empty:
                                return float(s.iloc[0])
                    return None

                net_inc = _get_row(inc, ['Net Income', 'Net Income Common Stockholders', 'NetIncome'])
                tot_rev = _get_row(inc, ['Total Revenue', 'Operating Revenue', 'TotalRevenue'])
                basic_eps = _get_row(inc, ['Diluted EPS', 'Basic EPS'])

                equity = _get_row(bal, ['Stockholders Equity', 'Total Equity Gross Minority Interest', 'Common Stock Equity'])
                tot_debt = _get_row(bal, ['Total Debt', 'Long Term Debt And Capital Lease Obligation', 'Long Term Debt'])
                curr_assets = _get_row(bal, ['Current Assets', 'Total Current Assets'])
                curr_liab = _get_row(bal, ['Current Liabilities', 'Total Current Liabilities'])
                shares_out = _get_row(bal, ['Ordinary Shares Number', 'Share Issued']) or self.yf_info.get('sharesOutstanding')

                if self.yf_info.get('profitMargins') is None and net_inc is not None and tot_rev and tot_rev > 0:
                    self.yf_info['profitMargins'] = net_inc / tot_rev
                if self.yf_info.get('returnOnEquity') is None and net_inc is not None and equity and equity > 0:
                    self.yf_info['returnOnEquity'] = net_inc / equity
                if self.yf_info.get('debtToEquity') is None and tot_debt is not None and equity and equity > 0:
                    self.yf_info['debtToEquity'] = (tot_debt / equity) * 100.0
                if self.yf_info.get('currentRatio') is None and curr_assets is not None and curr_liab and curr_liab > 0:
                    self.yf_info['currentRatio'] = curr_assets / curr_liab
                if self.yf_info.get('trailingEps') is None:
                    if basic_eps is not None:
                        self.yf_info['trailingEps'] = basic_eps
                    elif net_inc is not None and shares_out and shares_out > 0:
                        self.yf_info['trailingEps'] = net_inc / float(shares_out)
                if self.yf_info.get('bookValue') is None and equity is not None and shares_out and shares_out > 0:
                    self.yf_info['bookValue'] = equity / float(shares_out)
            except Exception:
                pass

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
            if peg_yf is not None and float(peg_yf) > 0:
                values.append(float(peg_yf)); sources.append("Yahoo")
            if self.finviz_data.get('peg') is not None and float(self.finviz_data.get('peg')) > 0:
                values.append(float(self.finviz_data.get('peg'))); sources.append("Finviz")
            if not values:
                pe_v = self.yf_info.get('trailingPE') or self.yf_info.get('forwardPE') or self.finviz_data.get('pe')
                eg_v = self.yf_info.get('earningsGrowth') or self.yf_info.get('earningsQuarterlyGrowth') or self.yf_info.get('revenueGrowth')
                if pe_v and eg_v and float(eg_v) > 0.02:
                    est_peg = float(pe_v) / (float(eg_v) * 100.0)
                    if 0 < est_peg < 20:
                        return est_peg, "Yahoo (Berekend)"
                # Tweede fallback: schat groei uit verschil tussen trailingPE en forwardPE
                t_pe = self.yf_info.get('trailingPE')
                f_pe = self.yf_info.get('forwardPE')
                if t_pe and f_pe and t_pe > f_pe > 0:
                    implied_growth_pct = ((float(t_pe) / float(f_pe)) - 1.0) * 100.0
                    if implied_growth_pct > 2.0:
                        est_peg2 = float(t_pe) / implied_growth_pct
                        if 0 < est_peg2 < 15:
                            return est_peg2, "Implied (Fwd P/E)"
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
                fv_val = float(self.finviz_data.get(key_finviz))
                # Schaal EPS van Finviz (USD) naar de lokale valuta als dit een Europese notering is
                if key_finviz == 'eps' and self.yf_info.get('currency', 'USD') != 'USD':
                    fv_val = fv_val * get_currency_rate('USD', self.yf_info.get('currency', 'EUR'))
                norm_fv = self._normalize_value(fv_val, is_percent, is_dividend, is_debt_equity)
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
        # Gebruik uitsluitend een geverifieerde US ticker (geen foutieve .split('.')[0])
        us_equivalent = _resolve_finviz_ticker(selected_ticker)
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
        if not target_val and engine.finviz_data.get('target'):
            fv_t = float(engine.finviz_data.get('target'))
            if info.get('currency', 'USD') == 'USD':
                target_val = fv_t

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

            # SUB-TAB 3: EERLIJKE BENCHMARK & RISICO (VOLLEDIGE TIJDREEKS + ECHTE BETA)
            with sub_tabs[2]:
                st.markdown("#### 🏆 Aandelen vs. All-World Benchmark Analyse")
                st.caption("Vergelijk de historische waardeontwikkeling van jouw portefeuille met wat exact dezelfde euro-inleg op dezelfde dagen in een All-World ETF of S&P 500 had opgeleverd.")

                bm_col1, bm_col2 = st.columns([1.15, 2.85])
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

                    # Bereken gewogen Beta van zowel de geselecteerde scope als de gehele portefeuille
                    filter_mode = "STOCKS_ONLY" if "Losse Aandelen" in bm_scope else "ALL"
                    scoped_positions = [p for p in portfolio_data if (p['Type'] == 'Aandeel' if filter_mode == "STOCKS_ONLY" else True) and p['Waarde (€)'] > 0 and p.get('Beta') is not None]
                    if scoped_positions:
                        w_beta_num = sum(float(p['Beta']) * p['Waarde (€)'] for p in scoped_positions)
                        w_beta_den = sum(p['Waarde (€)'] for p in scoped_positions)
                        port_beta = w_beta_num / w_beta_den if w_beta_den > 0 else 1.0
                    else:
                        port_beta = 1.0

                    beta_label = "Defensiever dan markt (< 0.95)" if port_beta < 0.95 else "Marktconform (0.95 - 1.10)" if port_beta <= 1.10 else "Offensiever / Volatieler (> 1.10)"
                    st.metric(
                        "Gewogen Beta (Selectie)",
                        f"{port_beta:.2f}",
                        delta=beta_label,
                        delta_color="off",
                        help="Gewogen gemiddelde Beta van je posities (op basis van Yahoo Finance & 1-jaars empirische koerscovariantie t.o.v. de wereldmarkt)."
                    )

                with bm_col2:
                    stocks_roi = (stock_stats['profit'] / stock_stats['active_inleg'] * 100.0) if stock_stats['active_inleg'] > 0 else 0.0
                    etf_roi = (etf_stats['profit'] / etf_stats['active_inleg'] * 100.0) if etf_stats['active_inleg'] > 0 else 0.0

                    # Gebruik echte transacties indien aanwezig, anders de aankoopdatums en netto inleg van actieve posities
                    raw_txs = st.session_state.get('transactions', [])
                    if raw_txs:
                        sim_tx = raw_txs
                    else:
                        if filter_mode == "STOCKS_ONLY":
                            sim_tx = [
                                {'ticker': p['Ticker'], 'type': 'BUY', 'shares': float(p['Aantal']), 'asset_class': 'STOCK', 'amount_eur': float(p['Netto Inleg (€)']), 'date': str(p['Datum'] or '2024-06-01')}
                                for p in portfolio_data if p['Type'] == 'Aandeel' and float(p['Netto Inleg (€)']) > 0
                            ]
                        else:
                            sim_tx = [
                                {'ticker': p['Ticker'], 'type': 'BUY', 'shares': float(p['Aantal']), 'asset_class': 'FUND' if p['Type'] == 'ETF' else 'STOCK', 'amount_eur': float(p['Netto Inleg (€)']), 'date': str(p['Datum'] or '2024-06-01')}
                                for p in portfolio_data if float(p['Netto Inleg (€)']) > 0
                            ]

                    actual_val_now = (stock_stats['value'] + stock_stats['dividends']) if filter_mode == "STOCKS_ONLY" else (total_val_eur + total_received_div_eur)
                    active_inleg_scope = stock_stats['active_inleg'] if filter_mode == "STOCKS_ONLY" else active_inleg_sum

                    tx_json = json.dumps(sim_tx, default=str)
                    with st.spinner("Historische portfoliolijn en DCA-benchmark doorrekenen..."):
                        df_dca_bm, bm_final_val = get_dca_benchmark_comparison(tx_json, bm_choice[0], filter_mode=filter_mode)

                    if df_dca_bm is not None and not df_dca_bm.empty:
                        # Schaal de historische portfoliolijn zodat het eindpunt naadloos aansluit op de actuele live waarde (incl. dividend)
                        last_sim_port = float(df_dca_bm['Jouw Portfolio (€)'].iloc[-1])
                        if last_sim_port > 0 and actual_val_now > 0:
                            scale_factor = actual_val_now / last_sim_port
                            df_dca_bm['Jouw Portfolio (€)'] = df_dca_bm['Jouw Portfolio (€)'] * scale_factor

                        bm_inleg_end = float(df_dca_bm['Cumulatieve Netto Inleg (€)'].iloc[-1])
                        # Indien er oude verkopen in transacties zaten waardoor bm_inleg_end afwijkt van huidige actieve inleg, normaliseer op actuele schaal
                        if bm_inleg_end > 0 and active_inleg_scope > 0 and abs(bm_inleg_end - active_inleg_scope) / active_inleg_scope > 0.05:
                            inleg_ratio = active_inleg_scope / bm_inleg_end
                            df_dca_bm['Cumulatieve Netto Inleg (€)'] *= inleg_ratio
                            df_dca_bm['DCA in Benchmark (€)'] *= inleg_ratio
                            bm_final_val = float(df_dca_bm['DCA in Benchmark (€)'].iloc[-1])

                        port_final_val = float(df_dca_bm['Jouw Portfolio (€)'].iloc[-1])
                        inleg_final_val = float(df_dca_bm['Cumulatieve Netto Inleg (€)'].iloc[-1])
                        diff_eur_vs_bm = port_final_val - bm_final_val
                        port_roi_bm = ((port_final_val - inleg_final_val) / inleg_final_val * 100.0) if inleg_final_val > 0 else 0.0
                        bm_roi_pct = ((bm_final_val - inleg_final_val) / inleg_final_val * 100.0) if inleg_final_val > 0 else 0.0

                        b_kpi1, b_kpi2, b_kpi3 = st.columns(3)
                        b_kpi1.metric("Jouw Waarde (incl. Div)", f"€ {port_final_val:,.0f}", f"{port_roi_bm:+.2f}% op inleg")
                        b_kpi2.metric(f"Waarde in {bm_choice[0]}", f"€ {bm_final_val:,.0f}", f"{bm_roi_pct:+.2f}% op inleg")
                        b_kpi3.metric(
                            "Verschil vs. Benchmark (Alpha)",
                            f"€ {diff_eur_vs_bm:+,.0f}",
                            delta=f"{port_roi_bm - bm_roi_pct:+.2f}% outperformance",
                            delta_color="normal" if diff_eur_vs_bm >= 0 else "inverse"
                        )

                        fig_bm = go.Figure()
                        fig_bm.add_trace(go.Scatter(
                            x=df_dca_bm['Datum'], y=df_dca_bm['Cumulatieve Netto Inleg (€)'],
                            mode='lines', name='Netto Inleg (€)',
                            line=dict(color=MATTE_COLORS["grey"], width=2, dash='dot'),
                            hovertemplate="Inleg: € %{y:,.0f}<extra></extra>"
                        ))
                        fig_bm.add_trace(go.Scatter(
                            x=df_dca_bm['Datum'], y=df_dca_bm['DCA in Benchmark (€)'],
                            mode='lines', name=f'DCA in {bm_choice[1]} (€ {bm_final_val:,.0f})',
                            line=dict(color=MATTE_COLORS["orange"], width=2.5),
                            hovertemplate=f"{bm_choice[0]}: € %{{y:,.0f}}<extra></extra>"
                        ))
                        fig_bm.add_trace(go.Scatter(
                            x=df_dca_bm['Datum'], y=df_dca_bm['Jouw Portfolio (€)'],
                            mode='lines', name=f'Jouw Selectie incl. Div (€ {port_final_val:,.0f})',
                            line=dict(color=MATTE_COLORS["green"] if diff_eur_vs_bm >= 0 else MATTE_COLORS["blue"], width=3),
                            fill='tonexty',
                            fillcolor='rgba(16, 185, 129, 0.10)' if diff_eur_vs_bm >= 0 else 'rgba(37, 99, 235, 0.08)',
                            hovertemplate="Jouw Waarde: € %{y:,.0f}<extra></extra>"
                        ))
                        fig_bm.update_layout(
                            height=320, margin=dict(t=40, b=15, l=10, r=20),
                            yaxis_title="Waarde (€)", hovermode="x unified",
                            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                            legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="right", x=1)
                        )
                        fig_bm.update_xaxes(fixedrange=True)
                        fig_bm.update_yaxes(fixedrange=True, gridcolor='rgba(128,128,128,0.15)')
                        st.plotly_chart(fig_bm, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)
                    else:
                        st.info("Voeg eerst een aankoopdatum en positie toe om de historische DCA-benchmark te berekenen.")

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
                st.markdown("#### 💰 Verwachte Dividend Kalender (12 Maanden)")
                month_names = ['Jan', 'Feb', 'Mrt', 'Apr', 'Mei', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dec']
                div_values = [round(v, 2) for v in monthly_div_projection.values()]
                fig_div = go.Figure(data=[go.Bar(
                    x=month_names, y=div_values,
                    marker_color=MATTE_COLORS["green"],
                    text=[f"€ {v:,.0f}" if v > 0 else "" for v in div_values],
                    textposition='outside'
                )])
                max_div_m = max(div_values) if div_values and max(div_values) > 0 else 100.0
                fig_div.update_layout(height=300, margin=dict(t=35, b=20, l=10, r=10), yaxis_title="Dividend (€)", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
                fig_div.update_xaxes(fixedrange=True)
                fig_div.update_yaxes(fixedrange=True, range=[0, max_div_m * 1.25], gridcolor='rgba(128,128,128,0.15)')
                st.plotly_chart(fig_div, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

                st.divider()
                st.markdown("#### 🏅 Top Dividendbetalers in je Portfolio")
                div_payers = [p for p in portfolio_data if p['Verwacht Div/J (€)'] > 0 or p['Ontvangen Div (€)'] > 0]
                if div_payers:
                    df_div_tbl = pd.DataFrame(div_payers)[["Ticker", "Naam", "Waarde (€)", "Verwacht Div/J (€)", "YoC (%)", "Div CAGR (%)", "Ontvangen Div (€)"]].sort_values(by="Verwacht Div/J (€)", ascending=False)
                    st.dataframe(
                        df_div_tbl,
                        column_config={
                            "Waarde (€)": st.column_config.NumberColumn("Waarde (€)", format="€ %.2f"),
                            "Verwacht Div/J (€)": st.column_config.NumberColumn("Verwacht / Jaar (€)", format="€ %.2f"),
                            "YoC (%)": st.column_config.NumberColumn("Yield on Cost", format="%.2f%%"),
                            "Div CAGR (%)": st.column_config.NumberColumn("3J Div Groei (p/j)", format="%+.1f%%"),
                            "Ontvangen Div (€)": st.column_config.NumberColumn("Totaal Ontvangen (€)", format="€ %.2f")
                        },
                        hide_index=True, use_container_width=True
                    )
                else:
                    st.info("Nog geen dividendbetalende posities gedetecteerd.")

            # SUB-TAB 5: DEEP-DIVE & FIRE CALCULATOR
            with sub_tabs[4]:
                st.markdown("#### 🔍 Deep-Dive per Positie: Laatste Kwartaalcijfers (EPS)")
                if earnings_history_store:
                    options = list(earnings_history_store.keys())
                    selected_earn_ticker = st.selectbox(
                        "Kies een aandeel uit je portfolio:",
                        options=options,
                        format_func=lambda x: f"{ticker_details_map.get(x, {}).get('shortName', x)} ({x})"
                    )
                    if selected_earn_ticker:
                        full_name = ticker_details_map.get(selected_earn_ticker, {}).get('longName', selected_earn_ticker)
                        render_classic_earnings_chart(earnings_history_store[selected_earn_ticker], title_text=f"Kwartaalcijfers (EPS): {full_name}")
                else:
                    st.info("Geen recente kwartaal-earnings gevonden voor je huidige aandelen.")

                st.divider()
                st.markdown("#### 🚀 Toekomst & FIRE Vermogenscalculator")
                col_c1, col_c2 = st.columns([1, 2])
                with col_c1:
                    start_cap = st.number_input("Startkapitaal (€)", value=float(round(total_val_eur + cash_eur, 0)), step=500.0)
                    monthly_add = st.number_input("Maandelijkse Inleg (€)", value=500.0, step=50.0)
                    years_grow = st.slider("Beleggingshorizon (Jaren)", 1, 40, 15)
                    expected_roi = st.slider("Verwacht Jaarlijks Rendement (%)", 1.0, 15.0, 8.0, step=0.5)
                    fire_target_monthly = st.number_input("Gewenst Passief Inkomen p/m (4%-regel, €)", value=2500.0, step=100.0)

                with col_c2:
                    months = years_grow * 12
                    r_m = (expected_roi / 100.0) / 12.0
                    future_vals, invested_vals = [], []
                    curr_v, curr_inv = start_cap, start_cap
                    for m in range(1, months + 1):
                        curr_v = curr_v * (1 + r_m) + monthly_add
                        curr_inv += monthly_add
                        if m % 12 == 0:
                            future_vals.append(round(curr_v, 0))
                            invested_vals.append(round(curr_inv, 0))

                    years_x = [datetime.now().year + y for y in range(1, years_grow + 1)]
                    fire_needed_cap = fire_target_monthly * 12 * 25
                    fig_proj = go.Figure()
                    fig_proj.add_trace(go.Scatter(x=years_x, y=invested_vals, mode='lines', name='Eigen Inleg', line=dict(color=MATTE_COLORS["grey"], width=2.5, dash='dash')))
                    fig_proj.add_trace(go.Scatter(x=years_x, y=future_vals, mode='lines', name='Verwacht Vermogen', fill='tonexty', line=dict(color=MATTE_COLORS["green"], width=3)))
                    fig_proj.add_hline(y=fire_needed_cap, line_dash="dot", line_color=MATTE_COLORS["orange"], annotation_text=f"FIRE Doel (4%-regel): € {fire_needed_cap:,.0f}")
                    fig_proj.update_layout(
                        title=f"Eindvermogen na {years_grow} jaar: € {future_vals[-1]:,.0f} (Passief 4%: € {(future_vals[-1]*0.04)/12:,.0f} / mnd)",
                        height=330, margin=dict(t=45, b=20, l=10, r=10),
                        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', hovermode="x unified"
                    )
                    fig_proj.update_xaxes(fixedrange=True)
                    fig_proj.update_yaxes(fixedrange=True, gridcolor='rgba(128,128,128,0.15)')
                    st.plotly_chart(fig_proj, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

    # --------------------------------------------------
    # TAB 2: AANDELEN ANALYSE (GEEN N/A'S MEER)
    # --------------------------------------------------
    with tabs[1]:
        st.subheader("🔎 Fundamentele & Technische Aandelen Analyse")

        def trigger_main_search():
            q = st.session_state.search_main.strip()
            if q:
                with st.spinner(f"Zoeken naar '{q}'..."):
                    st.session_state['search_results'] = search_yahoo_strict(q)

        col_s1, col_s2, col_s3 = st.columns([3, 1, 2])
        with col_s1:
            st.text_input("Zoek op bedrijfsnaam of ticker:", key="search_main", on_change=trigger_main_search, placeholder="Bijv. ASML, Novo Nordisk, Microsoft, BESI.AS...")
        with col_s2:
            st.write(""); st.write("")
            st.button("🔍 Zoek", on_click=trigger_main_search, use_container_width=True)
        with col_s3:
            if st.session_state.get('portfolio'):
                quick_list = ["-- Kies uit je Portfolio --"] + [f"{p['ticker']} | {KNOWN_TICKER_META.get(p['ticker'], {}).get('name', p['ticker'])}" for p in st.session_state['portfolio'] if float(p.get('amount', 0)) > 0.01]
                quick_pick = st.selectbox("Snelkoppeling uit Portfolio:", quick_list)
                if quick_pick != "-- Kies uit je Portfolio --":
                    st.session_state['search_results'] = [quick_pick]

        if st.session_state.get('search_results'):
            selected_option = st.selectbox("Selecteer het gewenste resultaat:", st.session_state['search_results'])
            selected_ticker = selected_option.split(" | ")[0].strip()

            with st.spinner(f"Fundamentele analyse & jaarrekening-ratio's laden voor {selected_ticker}..."):
                fund_data = get_cached_fundamentals(selected_ticker)

            info = fund_data['info']
            curr_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('regularMarketPreviousClose')
            currency = info.get('currency', 'USD')
            sym = "€" if currency == "EUR" else "$" if currency == "USD" else f"{currency} "
            eur_conv = get_currency_rate(currency, "EUR")

            if curr_price:
                metrics = fund_data['metrics']
                score = fund_data['score']
                summary = fund_data['summary']
                target_val = fund_data['target_val']
                graham_num = fund_data['graham_number']

                st.divider()
                h_col1, h_col2, h_col3 = st.columns([2.2, 1.3, 1.5])
                with h_col1:
                    comp_title = info.get('longName') or info.get('shortName') or KNOWN_TICKER_META.get(selected_ticker, {}).get('name', selected_ticker)
                    st.markdown(f"## {comp_title} ({selected_ticker})")
                    st.caption(f"🏭 Sector: **{info.get('sector', 'Onbekend')}** | 🌍 Land: **{info.get('country', 'Onbekend')}** | 💱 Valuta: **{currency}**")
                    if currency != "EUR":
                        st.markdown(f"### {sym}{curr_price:,.2f}  *(≈ € {curr_price * eur_conv:,.2f})*")
                    else:
                        st.markdown(f"### € {curr_price:,.2f}")

                    low_52 = info.get('fiftyTwoWeekLow')
                    high_52 = info.get('fiftyTwoWeekHigh')
                    if low_52 and high_52 and high_52 > low_52:
                        pos_52 = max(0.0, min(1.0, (curr_price - low_52) / (high_52 - low_52)))
                        st.progress(pos_52, text=f"52-Weeks Range: {sym}{low_52:,.2f} — {sym}{high_52:,.2f} ({pos_52*100:.0f}%)")

                with h_col2:
                    s_color = MATTE_COLORS["green"] if score >= 7.0 else MATTE_COLORS["orange"] if score >= 5.5 else MATTE_COLORS["red"]
                    st.markdown(f"""
                    <div style="text-align:center;">
                        <div class="score-badge" style="border: 5px solid {s_color}; color: {s_color};">{score}</div>
                        <p style="margin-top:8px; font-size:0.86rem; opacity:0.85;">{summary}</p>
                    </div>
                    """, unsafe_allow_html=True)

                with h_col3:
                    st.markdown("##### 🎯 Koersdoel & Waardering")
                    if target_val:
                        upside = ((target_val - curr_price) / curr_price) * 100
                        st.metric("Analisten Consensus Doel", f"{sym}{target_val:,.2f}", f"{upside:+.1f}% potentieel")
                    else:
                        st.metric("Analisten Consensus Doel", "Geen consensus", delta_color="off")
                    if graham_num:
                        g_diff = ((graham_num - curr_price) / curr_price) * 100
                        st.metric("Graham Fair Value", f"{sym}{graham_num:,.2f}", f"{g_diff:+.1f}% vs koers")

                # ACTIEKNOPPEN
                with st.expander("➕ Toevoegen aan Portfolio of Wishlist", expanded=False):
                    ac1, ac2 = st.columns(2)
                    with ac1:
                        st.markdown("**Direct toevoegen aan Portfolio**")
                        with st.form("add_port_form"):
                            f_shares = st.number_input("Aantal aandelen", min_value=0.0001, value=1.0, step=1.0)
                            f_inleg = st.number_input("Totale aankoopwaarde in EUR (€)", min_value=0.0, value=float(round(curr_price * eur_conv, 2)), step=10.0)
                            f_date = st.date_input("Aankoopdatum", value=datetime.now().date())
                            if st.form_submit_button("📥 Toevoegen aan Portfolio", use_container_width=True):
                                st.session_state['portfolio'].append({
                                    'ticker': selected_ticker, 'amount': f_shares,
                                    'net_inleg': f_inleg, 'total_dividends': 0.0,
                                    'class': 'FUND' if 'ETF' in str(info.get('quoteType', '')).upper() else 'Aandeel',
                                    'currency': currency, 'symbol': sym.strip(), 'buy_date': f_date
                                })
                                save_portfolio_to_db()
                                st.success(f"{selected_ticker} toegevoegd aan je portfolio!")
                    with ac2:
                        st.markdown("**Opnemen op je Wishlist**")
                        with st.form("add_wish_form"):
                            default_buy_target = round(curr_price * 0.90, 2)
                            w_target = st.number_input(f"Jouw Koopdoel / Limit ({currency})", min_value=0.0, value=float(default_buy_target), step=1.0)
                            w_notes = st.text_input("Korte notitie (optioneel)", placeholder="Bijv. Kopen bij dip onder MA200")
                            if st.form_submit_button("❤️ Toevoegen aan Wishlist", use_container_width=True):
                                if not any(w.get('ticker') == selected_ticker for w in st.session_state['wishlist']):
                                    st.session_state['wishlist'].append({
                                        'ticker': selected_ticker, 'name': comp_title,
                                        'target_buy': w_target, 'currency': currency, 'notes': w_notes
                                    })
                                    save_portfolio_to_db()
                                    st.success(f"{selected_ticker} staat nu op je Wishlist!")
                                else:
                                    st.warning(f"{selected_ticker} staat al op je Wishlist.")

                # KOERSGRAFIEK MET SMA50, SMA200 & RSI
                st.divider()
                c_head, c_per, c_ind = st.columns([2, 1.5, 1.5])
                with c_head:
                    st.markdown("#### 📈 Koersverloop & Technische Indicatoren")
                with c_per:
                    period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "YTD": "ytd", "1J": "1y", "5J": "5y", "Max": "max"}
                    sel_per_label = st.segmented_control("Periode", options=list(period_map.keys()), default="1J")
                    sel_per = period_map.get(sel_per_label, "1y")
                with c_ind:
                    show_sma = st.checkbox("Toon SMA 50 & SMA 200 + RSI(14)", value=True)

                hist_chart = yf.Ticker(selected_ticker).history(period=sel_per)
                if not hist_chart.empty:
                    if hist_chart.index.tz is not None:
                        hist_chart.index = hist_chart.index.tz_localize(None)
                    hist_chart['SMA50'] = hist_chart['Close'].rolling(50).mean()
                    hist_chart['SMA200'] = hist_chart['Close'].rolling(200).mean()

                    delta_c = hist_chart['Close'].diff()
                    gain = delta_c.where(delta_c > 0, 0.0).rolling(14).mean()
                    loss = (-delta_c.where(delta_c < 0, 0.0)).rolling(14).mean()
                    rs = gain / loss.replace(0, np.nan)
                    hist_chart['RSI'] = 100 - (100 / (1 + rs))

                    is_positive_trend = hist_chart['Close'].iloc[-1] >= hist_chart['Close'].iloc[0]
                    main_line_color = MATTE_COLORS["green"] if is_positive_trend else MATTE_COLORS["red"]

                    if show_sma and len(hist_chart) > 20:
                        fig_p = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.74, 0.26])
                        fig_p.add_trace(go.Scatter(x=hist_chart.index, y=hist_chart['Close'], mode='lines', name='Koers', line=dict(color=main_line_color, width=2.5)), row=1, col=1)
                        if hist_chart['SMA50'].notna().any():
                            fig_p.add_trace(go.Scatter(x=hist_chart.index, y=hist_chart['SMA50'], mode='lines', name='SMA 50', line=dict(color=MATTE_COLORS["blue"], width=1.6, dash='dot')), row=1, col=1)
                        if hist_chart['SMA200'].notna().any():
                            fig_p.add_trace(go.Scatter(x=hist_chart.index, y=hist_chart['SMA200'], mode='lines', name='SMA 200', line=dict(color=MATTE_COLORS["orange"], width=1.8, dash='dash')), row=1, col=1)

                        fig_p.add_trace(go.Scatter(x=hist_chart.index, y=hist_chart['RSI'], mode='lines', name='RSI (14)', line=dict(color=MATTE_COLORS["purple"], width=2)), row=2, col=1)
                        fig_p.add_hline(y=70, line_dash="dot", line_color=MATTE_COLORS["red"], row=2, col=1)
                        fig_p.add_hline(y=30, line_dash="dot", line_color=MATTE_COLORS["green"], row=2, col=1)
                        fig_p.update_yaxes(title_text=f"Koers ({currency})", fixedrange=True, gridcolor='rgba(128,128,128,0.15)', row=1, col=1)
                        fig_p.update_yaxes(title_text="RSI", range=[0, 100], fixedrange=True, gridcolor='rgba(128,128,128,0.15)', row=2, col=1)
                        fig_p.update_xaxes(fixedrange=True)
                        fig_p.update_layout(height=450, margin=dict(t=20, b=15, l=10, r=10), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    else:
                        fig_p = go.Figure(go.Scatter(x=hist_chart.index, y=hist_chart['Close'], mode='lines', name='Koers', line=dict(color=main_line_color, width=2.5)))
                        fig_p.update_layout(height=340, margin=dict(t=20, b=15, l=10, r=10), yaxis_title=f"Koers ({currency})", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', hovermode="x unified")
                        fig_p.update_xaxes(fixedrange=True)
                        fig_p.update_yaxes(fixedrange=True, gridcolor='rgba(128,128,128,0.15)')

                    st.plotly_chart(fig_p, use_container_width=True, config=PLOTLY_CONFIG_NO_ZOOM)

                # FUNDAMENTALS GRID (MET AUTOMATISCHE JAARREKENING FALLBACK)
                st.divider()
                st.markdown("#### 🧮 Fundamentele Kerncijfers & Ratio's")
                m_cols = st.columns(4)
                metric_specs = [
                    ("K/W Verhouding (P/E)", 'pe', "{:.2f}", TOOLTIPS['pe'], lambda v: "Gunstig" if 0 < v < 20 else "Stevig" if v > 30 else "Marktconform"),
                    ("PEG Ratio", 'peg', "{:.2f}", TOOLTIPS['peg'], lambda v: "Ondergewaardeerd" if 0 < v < 1.5 else "Duur" if v > 2.5 else "Redelijk"),
                    ("Winst per Aandeel (EPS)", 'eps', f"{sym}{{:.2f}}", TOOLTIPS['eps'], lambda v: "Winstgevend" if v > 0 else "Verlieslatend"),
                    ("Netto Winstmarge", 'margin', "{:.1f}%", TOOLTIPS['margin'], lambda v: "Sterke Moat" if v > 20 else "Gezond" if v > 10 else "Laag"),
                    ("Rendement Eigen Verm. (ROE)", 'roe', "{:.1f}%", TOOLTIPS['roe'], lambda v: "Topkwaliteit" if v > 15 else "Gemiddeld"),
                    ("Schuldratio (Debt/Eq)", 'de', "{:.2f}", TOOLTIPS['de'], lambda v: "Sterke Balans" if v < 1.0 else "Hoge Schuld" if v > 2.0 else "Acceptabel"),
                    ("Current Ratio (Liquiditeit)", 'cr', "{:.2f}", TOOLTIPS['cr'], lambda v: "Ruime Buffer" if v > 1.5 else "Krap" if v < 1.0 else "Gezond"),
                    ("Dividend Rendement", 'div', "{:.2f}%", TOOLTIPS['div'], lambda v: "Hoog Dividend" if v > 3.0 else "Groei/Laag" if v == 0 else "Regulier")
                ]
                for idx, (label, key, fmt, tip, eval_fn) in enumerate(metric_specs):
                    val, src = metrics.get(key, (None, "N/A"))
                    with m_cols[idx % 4]:
                        if val is not None:
                            st.metric(label, fmt.format(val), delta=f"{eval_fn(val)} ({src})", delta_color="off", help=tip)
                        else:
                            st.metric(label, "Niet van toepassing" if key == 'div' else "N/A", delta="Geen opgave", delta_color="off", help=tip)

                # KWARTAALCIJFERS & NIEUWS
                st.divider()
                col_earn_an, col_news_an = st.columns([1.35, 1.65])
                with col_earn_an:
                    earn_list = get_reliable_earnings(selected_ticker)
                    render_classic_earnings_chart(earn_list, title_text="📊 Kwartaal Earnings (Verwacht vs. Werkelijk)")

                with col_news_an:
                    st.markdown("##### 📰 Laatste Beursnieuws & Video Analyse")
                    news_items = get_google_news(f"{comp_title} {selected_ticker} stock")
                    if news_items:
                        for n in news_items:
                            st.markdown(f"""
                            <div class="news-card">
                                <a href="{n['link']}" target="_blank">{n['title']}</a>
                                <div style="font-size:0.78rem; opacity:0.7; margin-top:3px;">🕒 {n['date']}</div>
                            </div>
                            """, unsafe_allow_html=True)
                    else:
                        st.info("Geen recente nieuwsartikelen gevonden.")

                    with st.expander("▶️ Bekijk recente YouTube Aandelenanalyse"):
                        vid_id = get_youtube_video_id(f"{comp_title} {selected_ticker}")
                        if vid_id:
                            st.video(f"https://www.youtube.com/watch?v={vid_id}")
                        else:
                            st.caption("Geen directe video gevonden.")

    # --------------------------------------------------
    # TAB 3: WISHLIST & KOOPZONES
    # --------------------------------------------------
    with tabs[2]:
        st.subheader("❤️ Wishlist & Koopzone Bewaking")
        st.caption("Volg interessante aandelen en zie direct welk aandeel in je gewenste koopzone (of onder het analistendoel) noteert.")

        if not st.session_state['wishlist']:
            st.info("Je Wishlist is momenteel leeg. Zoek een aandeel op in **🔎 Aandelen Analyse** en klik op **Toevoegen aan Wishlist**.")
        else:
            wish_tickers = tuple(sorted(set(w['ticker'] for w in st.session_state['wishlist'])))
            with st.spinner("Live koersen voor Wishlist ophalen..."):
                wish_batch = fetch_batch_portfolio_data(wish_tickers)

            wish_rows = []
            for w in st.session_state['wishlist']:
                t = w['ticker']
                ext = wish_batch.get(t) or {}
                live_p = float(ext.get('live_price', 0.0))
                target_b = float(w.get('target_buy', 0.0))
                analyst_t = float(ext.get('target_price') or 0.0)
                curr = ext.get('currency_code') or w.get('currency', 'USD')
                dist_to_buy = ((live_p - target_b) / target_b * 100.0) if (target_b > 0 and live_p > 0) else 0.0
                upside_an = ((analyst_t - live_p) / live_p * 100.0) if (analyst_t > 0 and live_p > 0) else 0.0

                if target_b > 0 and live_p > 0 and live_p <= target_b:
                    status_lbl = "🟢 IN KOOPZONE"
                elif target_b > 0 and live_p > 0 and dist_to_buy <= 5.0:
                    status_lbl = "🟠 BIJNA (<5%)"
                else:
                    status_lbl = "⏳ Wachten"

                wish_rows.append({
                    "Verwijder": False,
                    "Ticker": t,
                    "Naam": ext.get('shortName') or w.get('name', t),
                    "Valuta": curr,
                    "Huidige Koers": round(live_p, 2),
                    "Dag %": round(ext.get('day_change_pct', 0.0), 2),
                    "Jouw Koopdoel": round(target_b, 2),
                    "Afstand tot Koopdoel (%)": round(dist_to_buy, 1),
                    "Analisten Doel": round(analyst_t, 2) if analyst_t > 0 else 0.0,
                    "Analisten Upside (%)": round(upside_an, 1),
                    "Status": status_lbl,
                    "Notitie": w.get('notes', '')
                })

            df_wish = pd.DataFrame(wish_rows)
            edited_wish = st.data_editor(
                df_wish,
                column_config={
                    "Verwijder": st.column_config.CheckboxColumn("🗑️", default=False, width="small"),
                    "Ticker": st.column_config.TextColumn("Ticker", disabled=True, width="small"),
                    "Naam": st.column_config.TextColumn("Bedrijf", disabled=True),
                    "Valuta": st.column_config.TextColumn("Val.", disabled=True, width="small"),
                    "Huidige Koers": st.column_config.NumberColumn("Koers", format="%.2f", disabled=True),
                    "Dag %": st.column_config.NumberColumn("Dag %", format="%+.2f%%", disabled=True),
                    "Jouw Koopdoel": st.column_config.NumberColumn("Jouw Koopdoel", format="%.2f", disabled=False),
                    "Afstand tot Koopdoel (%)": st.column_config.NumberColumn("Afstand %", format="%+.1f%%", disabled=True),
                    "Analisten Doel": st.column_config.NumberColumn("Analisten Doel", format="%.2f", disabled=True),
                    "Analisten Upside (%)": st.column_config.NumberColumn("Upside %", format="%+.1f%%", disabled=True),
                    "Status": st.column_config.TextColumn("Signaal", disabled=True),
                    "Notitie": st.column_config.TextColumn("Notitie", disabled=False)
                },
                hide_index=True, use_container_width=True, key="wishlist_editor"
            )

            if not df_wish[["Verwijder", "Jouw Koopdoel", "Notitie"]].equals(edited_wish[["Verwijder", "Jouw Koopdoel", "Notitie"]]):
                new_w_list = []
                for _, r in edited_wish.iterrows():
                    if not r["Verwijder"]:
                        new_w_list.append({
                            'ticker': r['Ticker'], 'name': r['Naam'],
                            'target_buy': float(r['Jouw Koopdoel']),
                            'currency': r['Valuta'], 'notes': r['Notitie']
                        })
                st.session_state['wishlist'] = new_w_list
                save_portfolio_to_db()
                st.toast("Wishlist bijgewerkt!", icon="✅")
                st.rerun()

    # --------------------------------------------------
    # TAB 4: BOX 3 BELASTING (FORFAITAIR VS WERKELIJK)
    # --------------------------------------------------
    with tabs[3]:
        st.subheader("🇳🇱 Box 3 Vermogensrendementsheffing (2025 / 2026)")
        st.markdown("Vergelijk de **Forfaitaire Overbruggingswet** met de **Tegenbewijsregeling (Werkelijk Rendement)** zodat je nooit te veel Box 3-belasting betaalt.")

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            st.markdown("##### 📋 1. Jouw Situatie & Vermogen op Peildatum (1 januari)")
            b3_year = st.selectbox("Belastingjaar", [2026, 2025], index=0)
            has_partner = st.checkbox("Ik heb een fiscaal partner (verdubbelt heffingsvrij vermogen)", value=False)
            beleggingen_jan1 = st.number_input("Waarde Beleggingen (Portfolio + Crypto + Vastgoed)", value=float(round(global_total_val_eur, 0)), step=1000.0)
            spaargeld_jan1 = st.number_input("Waarde Bank- en Spaartegoeden (€)", value=float(st.session_state['settings'].get('cash_eur', 0.0)), step=1000.0)
            schulden_jan1 = st.number_input("Box 3 Schulden (excl. eigenwoningschuld)", value=0.0, step=1000.0)

        with col_b2:
            st.markdown("##### 📈 2. Werkelijk Behaald Rendement (Voor Tegenbewijsregeling)")
            default_werkelijk = round(global_total_year_change_eur + global_total_expected_div_eur, 0)
            werkelijk_koers_div = st.number_input("Werkelijke Koerswinst + Dividend in dit jaar (€)", value=float(default_werkelijk), step=250.0)
            werkelijk_rente = st.number_input("Ontvangen Spaarrente in dit jaar (€)", value=float(round(spaargeld_jan1 * 0.02, 0)), step=50.0)

            # Parameters per jaar
            vrijstelling_pp = 57684.0 if b3_year == 2025 else 59357.0
            schuldendrempel_pp = 3800.0
            forfait_sparen = 0.0144 if b3_year == 2025 else 0.0150
            forfait_beleggen = 0.0588 if b3_year == 2025 else 0.0604
            forfait_schulden = 0.0262 if b3_year == 2025 else 0.0270
            belasting_tarief = 0.36

            vrijstelling_tot = vrijstelling_pp * (2 if has_partner else 1)
            schuldendrempel_tot = schuldendrempel_pp * (2 if has_partner else 1)
            aftrekbare_schuld = max(0.0, schulden_jan1 - schuldendrempel_tot)

            bezittingen_tot = spaargeld_jan1 + beleggingen_jan1
            rendementsgrondslag = max(0.0, bezittingen_tot - aftrekbare_schuld)
            grondslag_sparen_beleggen = max(0.0, rendementsgrondslag - vrijstelling_tot)
            aandeel_grondslag = (grondslag_sparen_beleggen / rendementsgrondslag) if rendementsgrondslag > 0 else 0.0

            forfaitair_rendement = max(0.0, (spaargeld_jan1 * forfait_sparen) + (beleggingen_jan1 * forfait_beleggen) - (aftrekbare_schuld * forfait_schulden))
            belastbaar_forfaitair = forfaitair_rendement * aandeel_grondslag
            tax_forfaitair = belastbaar_forfaitair * belasting_tarief

            totaal_werkelijk_rendement = max(0.0, werkelijk_koers_div + werkelijk_rente)
            tax_werkelijk = totaal_werkelijk_rendement * belasting_tarief if grondslag_sparen_beleggen > 0 else 0.0
            optimal_tax = min(tax_forfaitair, tax_werkelijk)

        st.divider()
        bk1, bk2, bk3, bk4 = st.columns(4)
        bk1.metric("Heffingsvrij Vermogen", f"€ {vrijstelling_tot:,.0f}", f"Belastbaar: € {grondslag_sparen_beleggen:,.0f}", delta_color="off")
        bk2.metric("Belasting Forfaitair Stelsel", f"€ {tax_forfaitair:,.2f}", f"Fictief: € {belastbaar_forfaitair:,.0f}", delta_color="off")
        bk3.metric("Belasting Werkelijk Rendement", f"€ {tax_werkelijk:,.2f}", f"Werkelijk: € {totaal_werkelijk_rendement:,.0f}", delta_color="off")
        besparing = abs(tax_forfaitair - tax_werkelijk)
        gunstigste = "Tegenbewijs (Werkelijk)" if tax_werkelijk < tax_forfaitair else "Forfaitair Stelsel"
        bk4.metric("Laagste Te Betalen Belasting", f"€ {optimal_tax:,.2f}", f"Kies: {gunstigste} (Bespaar € {besparing:,.0f})")
