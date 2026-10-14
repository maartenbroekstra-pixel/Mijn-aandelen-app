import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import json
import re
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import streamlit_authenticator as stauth

# ==================================================
# 1. CONFIGURATIE, STYLING & AUTHENTICATIE
# ==================================================
st.set_page_config(
    page_title="Aandelen Dashboard Pro",
    layout="wide",
    page_icon="📈",
    initial_sidebar_state="expanded"
)

# Custom SaaS / FinTech CSS
st.markdown("""
<style>
    /* Algemene layout & typografie */
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2.5rem;
    }
    /* Metric cards styling */
    div[data-testid="stMetric"] {
        background-color: rgba(128, 128, 128, 0.06);
        border: 1px solid rgba(128, 128, 128, 0.15);
        padding: 14px 16px;
        border-radius: 12px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.03);
        transition: transform 0.15s ease, border-color 0.15s ease;
    }
    div[data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        border-color: rgba(69, 123, 157, 0.5);
    }
    div[data-testid="stMetricLabel"] > div {
        font-size: 0.82rem !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        opacity: 0.8;
    }
    div[data-testid="stMetricValue"] > div {
        font-size: 1.45rem !important;
        font-weight: 700 !important;
    }
    /* Tabs styling */
    button[data-baseweb="tab"] {
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        padding: 10px 18px !important;
    }
    /* Badges & News cards */
    .news-card {
        padding: 10px 14px;
        border-radius: 8px;
        background-color: rgba(128, 128, 128, 0.05);
        border-left: 4px solid #457B9D;
        margin-bottom: 8px;
    }
    .news-card a {
        text-decoration: none;
        font-weight: 600;
    }
    .news-card a:hover {
        text-decoration: underline;
    }
    .score-badge {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 92px;
        height: 92px;
        border-radius: 50%;
        font-size: 34px;
        font-weight: 800;
        margin: auto;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
</style>
""", unsafe_allow_html=True)

names = ['Maarten Broekstra']
usernames = ['admin']
passwords = ['6f9a2b8c4d1e7f3a9b0c5e8d2f1a4c7b9e0d1f3a6b8c5d2e7f9a1b0c4e8d2f']

credentials = {
    'usernames': {
        usernames[0]: {
            'name': names[0],
            'password': passwords[0]
        }
    }
}

authenticator = stauth.Authenticate(
    credentials,
    'portfolio_dashboard_cookie',
    'abcdef',
    cookie_expiry_days=30
)

# ==================================================
# 2. DATABASE & ATOMIC PERSISTENCE
# ==================================================
DB_FILE = 'portfolio_db.json'

def _parse_date_safe(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val).split('T')[0], "%Y-%m-%d").date()
    except Exception:
        return None

def load_portfolio_from_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                port = data.get('portfolio', [])
                for p in port:
                    p['buy_date'] = _parse_date_safe(p.get('buy_date'))
                wishlist = data.get('wishlist', [])
                dca_history = data.get('dca_history', {})
                transactions = data.get('transactions', [])
                for t in transactions:
                    t['date'] = _parse_date_safe(t.get('date')) or datetime.now().date()
                settings = data.get('settings', {'cash_eur': 0.0, 'max_weight_alert': 15.0})
                return port, wishlist, dca_history, transactions, settings
        except Exception:
            pass
    return [], [], {}, [], {'cash_eur': 0.0, 'max_weight_alert': 15.0}

def save_portfolio_to_db():
    """Schrijft data atomair weg via een tijdelijk bestand zodat de JSON nooit corrupt raakt."""
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
    # 4. CONSTANTEN & MAPPINGS
    # ==================================================
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }

    EU_TO_US_MAP = {
        "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
        "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
        "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
        "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
        "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
        "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD", "SHELL.AS": "SHEL",
        "WKL.AS": "WTKWY", "9PDA.DU": "PROSY", "RAND.AS": "RANJY", "AKZA.AS": "AKZOY",
        "KPN.AS": "KKPNY", "NN.AS": "NNGP", "ASM.AS": "ASMIY"
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
        "blue": "#457B9D", "green": "#2A9D8F", "red": "#E76F51", "yellow": "#E9C46A",
        "charcoal": "#264653", "orange": "#F4A261", "purple": "#9B5DE5", "grey": "#A8DADC"
    }

    SECTOR_COLORS = {
        "Technology": MATTE_COLORS["blue"], "Financial Services": MATTE_COLORS["charcoal"],
        "Healthcare": MATTE_COLORS["green"], "Consumer Cyclical": MATTE_COLORS["red"],
        "Industrials": MATTE_COLORS["purple"], "Consumer Defensive": MATTE_COLORS["orange"],
        "Energy": MATTE_COLORS["yellow"], "Communication Services": "#D62828",
        "Real Estate": "#8D6E63", "Basic Materials": "#5F9EA0",
        "Utilities": "#76FF03", "ETF/Overig": "#90A4AE", "Onbekend": "#B0BEC5"
    }

    # ==================================================
    # 5. STATE MANAGEMENT & INITIAL LOAD
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
    if 'tax_val_jan1' not in st.session_state:
        st.session_state.tax_val_jan1 = 0.0
    if 'tax_val_dec31' not in st.session_state:
        st.session_state.tax_val_dec31 = 0.0

    # ==================================================
    # 6. DATA FETCHING & CACHING ENGINE (PARALLEL & ROBUUST)
    # ==================================================
    @st.cache_data(ttl=900, show_spinner=False)
    def get_finviz_data(ticker):
        d = {}
        if not ticker or "." in ticker:
            return d
        try:
            url = f"https://finviz.com/quote.ashx?t={ticker}"
            r = requests.get(url, headers=HEADERS, timeout=4)
            if r.status_code == 200:
                def parse(label):
                    m = re.search(rf'>{label}</td>.*?<b>\s*([\d\.-]+)%?\s*</b>', r.text, re.DOTALL)
                    return float(m.group(1)) if m and m.group(1) not in ('-', '') else None
                d['peg'] = parse('PEG')
                d['target'] = parse('Target Price')
                d['eps'] = parse(r'EPS \(ttm\)')
                d['pe'] = parse('P/E')
                d['de'] = parse('Debt/Eq')
                d['roe'] = parse('ROE')
                d['margin'] = parse('Profit Margin')
                d['div'] = parse('Dividend %')
        except Exception:
            pass
        return d

    @st.cache_data(ttl=600, show_spinner=False)
    def get_currency_rate(from_curr, to_curr="EUR"):
        if not from_curr or from_curr.upper() == to_curr.upper():
            return 1.0
        from_curr = from_curr.upper()
        # GBp (Britse pence) correctie
        if from_curr in ("GBP", "GBX"):
            try:
                data = yf.Ticker("GBPEUR=X").history(period="5d")
                rate = float(data['Close'].iloc[-1]) if not data.empty else 1.17
                return rate
            except Exception:
                return 1.17
        try:
            ticker = "EURUSD=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
            data = yf.Ticker(ticker).history(period="5d")
            if data.empty:
                return 1.0
            last_close = float(data['Close'].iloc[-1])
            return (1.0 / last_close) if from_curr == "USD" else last_close
        except Exception:
            return 1.0

    def _fetch_yahoo_earnings_raw(ticker):
        history = []
        try:
            tk = yf.Ticker(ticker)
            df = tk.earnings_dates
            if df is not None and not df.empty:
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                current_year = datetime.now().year
                if not any((df.index.year == current_year) | (df.index.year == current_year - 1)):
                    return None
                df_past = df[df.index < datetime.now()].dropna(subset=['Reported EPS', 'EPS Estimate']).sort_index(ascending=False).head(4)
                for date_idx, row in df_past.iterrows():
                    est, rep, surp = row['EPS Estimate'], row['Reported EPS'], row.get('Surprise(%)', np.nan)
                    if pd.isna(surp) and est != 0:
                        surp = ((rep - est) / abs(est)) * 100
                    elif pd.isna(surp):
                        surp = 0.0
                    history.append({
                        'date': date_idx.strftime('%Y-%m-%d'),
                        'reported': float(rep),
                        'estimate': float(est),
                        'surprise': float(surp)
                    })
                if history:
                    history.reverse()
                    return history
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

    def _calc_div_cagr_from_series(divs):
        try:
            if divs is None or divs.empty:
                return 0.0
            if divs.index.tz is not None:
                divs = divs.copy()
                divs.index = divs.index.tz_localize(None)
            yearly = divs.resample('YE').sum()
            yearly = yearly[yearly > 0]
            # Gebruik volledige kalenderjaren indien mogelijk
            if len(yearly) >= 4:
                return float((((yearly.iloc[-1] / yearly.iloc[-4]) ** (1 / 3)) - 1) * 100)
        except Exception:
            pass
        return 0.0

    @st.cache_data(ttl=86400, show_spinner=False)
    def get_div_cagr(ticker):
        try:
            tk = yf.Ticker(ticker)
            return _calc_div_cagr_from_series(tk.dividends)
        except Exception:
            return 0.0

    def _fetch_single_ticker_extended(ticker):
        """Pure Python worker functie (zonder Streamlit-calls) zodat ThreadPoolExecutor 100% veilig werkt."""
        try:
            tk = yf.Ticker(ticker)
            info = {}
            try:
                info = tk.info or {}
            except Exception:
                info = {}

            fast_price = None
            fast_prev = None
            try:
                fi = tk.fast_info
                fast_price = getattr(fi, 'last_price', None)
                fast_prev = getattr(fi, 'previous_close', None)
            except Exception:
                pass

            current_price = info.get('currentPrice') or info.get('regularMarketPrice') or fast_price or info.get('regularMarketPreviousClose')
            previous_close = info.get('regularMarketPreviousClose') or fast_prev

            if not current_price:
                return None

            q_type = 'ETF' if 'ETF' in str(info.get('quoteType', 'EQUITY')).upper() else 'Aandeel'

            # Earnings
            earnings_history_list = _fetch_yahoo_earnings_raw(ticker)
            if not earnings_history_list and ticker in EU_TO_US_MAP:
                earnings_history_list = _fetch_yahoo_earnings_raw(EU_TO_US_MAP[ticker])
            earnings_history_list = earnings_history_list or []
            earnings_surprise = (
                {'date': earnings_history_list[-1]['date'], 'surprise_pct': earnings_history_list[-1]['surprise']}
                if earnings_history_list else None
            )

            # Dividenden & CAGR in 1 keer uit tk.dividends
            dividend_months = []
            div_cagr = 0.0
            try:
                hist_div = tk.dividends
                if hist_div is not None and not hist_div.empty:
                    if hist_div.index.tz is not None:
                        hist_div.index = hist_div.index.tz_localize(None)
                    div_cagr = _calc_div_cagr_from_series(hist_div)
                    recent_divs = hist_div[hist_div.index > datetime.now() - timedelta(days=365)]
                    for date_val, amount in recent_divs.items():
                        dividend_months.append({'month': int(date_val.month), 'amount': float(amount)})
            except Exception:
                pass

            # YTD Startkoers
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

            # Normaliseer fallback dividend yield (altijd als decimaal bijv. 0.025 voor 2.5%)
            raw_yield = info.get('trailingAnnualDividendYield') or info.get('dividendYield')
            if raw_yield is not None:
                raw_yield = float(raw_yield)
                if raw_yield > 0.40:  # Yahoo gaf percentage bijv. 2.5 ipv 0.025
                    raw_yield = raw_yield / 100.0

            raw_curr = info.get('currency', 'EUR')
            # Indien GBp (Britse pence), prijs door 100 delen en GBP gebruiken
            if raw_curr == 'GBp':
                current_price = float(current_price) / 100.0
                if previous_close:
                    previous_close = float(previous_close) / 100.0
                if ytd_start_price:
                    ytd_start_price = float(ytd_start_price) / 100.0
                raw_curr = 'GBP'

            return {
                'type': q_type,
                'earnings': earnings_surprise,
                'earnings_history': earnings_history_list,
                'dividend_history': dividend_months,
                'div_cagr': div_cagr,
                'sector': info.get('sector', 'ETF/Overig' if q_type == 'ETF' else 'Onbekend'),
                'country': info.get('country', 'Wereldwijd' if q_type == 'ETF' else 'Onbekend'),
                'live_price': float(current_price),
                'day_change_pct': float(day_change_pct),
                'target_price': info.get('targetMeanPrice'),
                'previous_close': float(previous_close) if previous_close else float(current_price),
                'ytd_start_price': ytd_start_price,
                'currency_code': raw_curr,
                'currency_symbol': '€' if raw_curr == 'EUR' else '$' if raw_curr == 'USD' else info.get('currencySymbol', raw_curr),
                'shortName': info.get('shortName') or info.get('longName') or ticker,
                'longName': info.get('longName') or info.get('shortName') or ticker,
                'fallback_div_rate': info.get('trailingAnnualDividendRate') or info.get('dividendRate'),
                'fallback_div_yield': raw_yield,
                'beta': info.get('beta')
            }
        except Exception:
            return None

    @st.cache_data(ttl=1800, show_spinner=False)
    def get_extended_portfolio_data(ticker):
        return _fetch_single_ticker_extended(ticker)

    @st.cache_data(ttl=1800, show_spinner=False)
    def fetch_batch_portfolio_data(tickers_tuple):
        """Haalt alle tickers razendsnel parallel op via ThreadPoolExecutor."""
        results = {}
        if not tickers_tuple:
            return results
        max_workers = min(10, len(tickers_tuple))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ticker = {executor.submit(_fetch_single_ticker_extended, t): t for t in tickers_tuple}
            for future in as_completed(future_to_ticker):
                t = future_to_ticker[future]
                try:
                    results[t] = future.result()
                except Exception:
                    results[t] = None
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
                    if hist.index.tz is not None:
                        hist.index = hist.index.tz_localize(None)
                    df[t] = hist['Close']
            except Exception:
                pass
        return df.pct_change().corr() if not df.empty and len(df.columns) > 1 else None

    @st.cache_data(ttl=3600, show_spinner=False)
    def get_benchmark_comparison(weights_dict, benchmark_ticker="^GSPC", period="1y"):
        """Berekent de genormaliseerde performance (start = 100) van de huidige portfolioweging vs een benchmark."""
        try:
            tickers = list(weights_dict.keys())
            if not tickers:
                return None
            df_prices = pd.DataFrame()
            for t in tickers:
                h = yf.Ticker(t).history(period=period)
                if not h.empty:
                    if h.index.tz is not None:
                        h.index = h.index.tz_localize(None)
                    df_prices[t] = h['Close']

            bm = yf.Ticker(benchmark_ticker).history(period=period)
            if bm.empty or df_prices.empty:
                return None
            if bm.index.tz is not None:
                bm.index = bm.index.tz_localize(None)

            df_prices = df_prices.ffill().bfill()
            common_idx = df_prices.index.intersection(bm.index)
            if len(common_idx) < 5:
                return None

            df_prices = df_prices.loc[common_idx]
            bm_series = bm.loc[common_idx, 'Close']

            # Normaliseer elk aandeel naar 1.0 op dag 0 en vermenigvuldig met gewicht
            norm_df = df_prices / df_prices.iloc[0]
            valid_cols = [c for c in norm_df.columns if c in weights_dict]
            total_w = sum(weights_dict[c] for c in valid_cols)
            if total_w <= 0:
                return None

            port_index = sum(norm_df[c] * (weights_dict[c] / total_w) for c in valid_cols) * 100.0
            bm_index = (bm_series / bm_series.iloc[0]) * 100.0

            return pd.DataFrame({
                'Datum': common_idx,
                'Jouw Portfolio (Huidige Weging)': port_index.values,
                'Benchmark': bm_index.values
            })
        except Exception:
            return None

    @st.cache_data(ttl=1800, show_spinner=False)
    def search_yahoo_strict(query):
        try:
            r = requests.get(
                f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&quotesCount=15",
                headers=HEADERS,
                timeout=3
            )
            return [
                f"{q.get('symbol')} | {q.get('shortname') or q.get('longname') or q.get('symbol')}"
                for q in r.json().get('quotes', []) if q.get('symbol')
            ]
        except Exception:
            return []

    @st.cache_data(ttl=1800, show_spinner=False)
    def get_google_news(query):
        try:
            r = requests.get(
                f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en",
                headers=HEADERS,
                timeout=4
            )
            root = ET.fromstring(r.content)
            return [
                {
                    "title": i.find('title').text,
                    "link": i.find('link').text,
                    "date": i.find('pubDate').text[:16] if i.find('pubDate') is not None else ""
                }
                for i in root.findall('./channel/item')[:4]
            ]
        except Exception:
            return []

    @st.cache_data(ttl=3600, show_spinner=False)
    def get_youtube_video_id(query):
        try:
            r = requests.get(
                f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}+stock+analysis+{datetime.now().year}",
                headers=HEADERS,
                timeout=5
            )
            vids = re.findall(r"watch\?v=(\S{11})", r.text)
            return vids[0] if vids else None
        except Exception:
            return None

    # ==================================================
    # 7. DATA ENGINE & AI SCORE (GECACHET)
    # ==================================================
    class DataEngine:
        def __init__(self, ticker, original_ticker):
            self.ticker = ticker
            self.original_ticker = original_ticker
            self.yf_obj = yf.Ticker(original_ticker)
            try:
                self.yf_info = self.yf_obj.info or {}
            except Exception:
                self.yf_info = {}
            self.finviz_data = get_finviz_data(self.ticker) if "." not in self.ticker else {}

        def _normalize_value(self, val, is_percent, is_dividend, is_debt_equity):
            if val is None:
                return None
            if is_debt_equity:
                return val / 100.0 if val > 5.0 else val
            if is_percent:
                if is_dividend:
                    # Zorg dat dividend yield altijd als percentage (bijv. 3.2 voor 3.2%) terugkomt
                    return val * 100.0 if 0 < abs(val) < 0.40 else val
                return val * 100.0 if abs(val) <= 1.0 and val != 0 else val
            return val

        def get_robust_peg(self):
            values, sources = [], []
            if self.yf_info.get('pegRatio'):
                values.append(float(self.yf_info.get('pegRatio')))
                sources.append("Yahoo")
            if self.finviz_data.get('peg'):
                values.append(float(self.finviz_data.get('peg')))
                sources.append("Finviz")
            return (sum(values) / len(values), " & ".join(sources)) if values else (None, "N/A")

        def get_combined_metric(self, key_yf, key_finviz=None, is_percent=False, is_dividend=False, is_debt_equity=False, use_average=True):
            valid_values, sources = [], []
            if self.yf_info.get(key_yf) is not None:
                valid_values.append(self._normalize_value(float(self.yf_info.get(key_yf)), is_percent, is_dividend, is_debt_equity))
                sources.append("Yahoo")

            if not use_average and valid_values:
                return valid_values[0], sources[0]

            if key_finviz and self.finviz_data.get(key_finviz) is not None:
                valid_values.append(self._normalize_value(float(self.finviz_data.get(key_finviz)), is_percent, is_dividend, is_debt_equity))
                sources.append("Finviz")

            if not valid_values:
                return None, "N/A"
            if not use_average:
                return valid_values[0], sources[0]

            return sum(valid_values) / len(valid_values), " & ".join(sources) + (" (Avg)" if len(sources) > 1 else "")

    def calculate_ai_score(data):
        score = 5.0
        reasons = []

        eps_val, _ = data['eps']
        if eps_val is not None:
            if eps_val > 0:
                score += 1.0
                reasons.append("Winstgevend")
            else:
                score -= 2.5
                reasons.append("Verlieslatend")

        peg_val, _ = data['peg']
        if peg_val is not None:
            if 0 < peg_val < 1.5:
                score += 1.0
                reasons.append("Gunstige PEG")
            elif peg_val > 2.5:
                score -= 0.5
            elif peg_val < 0:
                score -= 1.0

        pe_val, _ = data['pe']
        if pe_val is not None:
            if 0 < pe_val < 20:
                score += 0.5
            elif pe_val > 40:
                score -= 0.5

        de_val, _ = data['de']
        if de_val is not None:
            if de_val < 1.0:
                score += 1.0
                reasons.append("Sterke Balans")
            elif de_val > 2.0:
                score -= 1.0
                reasons.append("Hoge Schuld")

        cr_val, _ = data['cr']
        if cr_val is not None:
            if cr_val > 1.5:
                score += 0.5
            elif cr_val < 1.0:
                score -= 0.5
                reasons.append("Krappe Liquiditeit")

        margin_val, _ = data['margin']
        if margin_val is not None:
            if margin_val > 20:
                score += 1.0
                reasons.append("Hoge Winstmarge")
            elif margin_val < 5:
                score -= 0.5

        roe_val, _ = data['roe']
        if roe_val is not None:
            if roe_val > 15:
                score += 0.5
                reasons.append("Sterke ROE")

        div_val, _ = data['div']
        if div_val is not None:
            if div_val > 2.5:
                score += 0.5
                reasons.append("Mooi Dividend")

        final_score = max(0.0, min(10.0, score))
        summary = "Het bedrijf scoort gemiddeld op fundamentele ratio's."
        if final_score >= 7.5:
            summary = f"Fundamenteel sterk aandeel! Uitblinkers: {', '.join(reasons[:4]).lower()}."
        elif final_score <= 4.0:
            summary = f"Aandachtspunt: de fundamentals ogen zwak ({', '.join(reasons[:3]).lower()})."
        elif reasons:
            summary = f"Gemengd beeld. Opvallende kenmerken: {', '.join(reasons[:3]).lower()}."

        return round(final_score, 1), summary

    @st.cache_data(ttl=3600, show_spinner=False)
    def get_cached_fundamentals(selected_ticker):
        """Gecachete wrapper rond DataEngine zodat Wishlist & Analyse razendsnel laden."""
        us_equivalent = EU_TO_US_MAP.get(
            selected_ticker,
            selected_ticker.split('.')[0] if '.' in selected_ticker else selected_ticker
        )
        engine = DataEngine(us_equivalent, selected_ticker)
        info = engine.yf_info

        metrics = {
            'peg': engine.get_robust_peg(),
            'pe': engine.get_combined_metric('trailingPE', 'pe', use_average=False),
            'div': engine.get_combined_metric('dividendYield', 'div', is_percent=True, is_dividend=True, use_average=False),
            'roe': engine.get_combined_metric('returnOnEquity', 'roe', is_percent=True),
            'de': engine.get_combined_metric('debtToEquity', 'de', is_debt_equity=True),
            'cr': engine.get_combined_metric('currentRatio', None),
            'pb': engine.get_combined_metric('priceToBook', None),
            'margin': engine.get_combined_metric('profitMargins', 'margin', is_percent=True),
            'eps': engine.get_combined_metric('trailingEps', 'eps')
        }
        score, summary = calculate_ai_score(metrics)
        target_val = info.get('targetMeanPrice') or engine.finviz_data.get('target')

        # Graham Number schatting (indien EPS > 0 en BookValue > 0)
        bvps = info.get('bookValue')
        eps_v = metrics['eps'][0]
        graham_number = None
        if eps_v and eps_v > 0 and bvps and bvps > 0:
            graham_number = float((22.5 * eps_v * bvps) ** 0.5)

        return {
            'info': info,
            'metrics': metrics,
            'score': score,
            'summary': summary,
            'target_val': target_val,
            'graham_number': graham_number
        }

    # ==================================================
    # 8. TRADE REPUBLIC CSV PARSER
    # ==================================================
    def parse_tr_csv(uploaded_file):
        df = pd.read_csv(uploaded_file)

        portfolio_dict = {}
        dca_history = {}
        transactions_list = []

        valid_types = ['BUY', 'SELL', 'DIVIDEND', 'MIGRATION', 'SPLIT', 'REVERSE_SPLIT']
        df_trades = df[df['type'].isin(valid_types) & df['asset_class'].isin(['STOCK', 'FUND'])].copy()

        for _, row in df_trades.sort_values('date').iterrows():
            isin = row['symbol']
            t_type = row['type']
            a_class = row['asset_class']

            try:
                shares_raw = float(row['shares'])
            except Exception:
                shares_raw = 0.0
            try:
                price = float(row['price'])
            except Exception:
                price = 0.0
            try:
                amount_val = float(row['amount'])
            except Exception:
                amount_val = 0.0
            date_val = _parse_date_safe(row.get('date')) or datetime.now().date()

            if pd.isna(isin) or str(isin).strip() == '':
                continue

            ticker = ISIN_TO_YF.get(isin, isin)

            transactions_list.append({
                'ticker': ticker,
                'type': t_type,
                'shares': shares_raw,
                'amount_eur': abs(amount_val) if amount_val != 0 else (abs(shares_raw) * price),
                'date': date_val
            })

            if isin not in portfolio_dict:
                portfolio_dict[isin] = {
                    'shares': 0.0,
                    'total_buys': 0.0,
                    'total_sells': 0.0,
                    'total_dividends': 0.0,
                    'class': 'FUND' if a_class == 'FUND' else 'Aandeel',
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
                portfolio_dict[isin]['shares'] -= abs_shares
                if portfolio_dict[isin]['shares'] < 0:
                    portfolio_dict[isin]['shares'] = 0.0
                cash_received = abs(amount_val) if amount_val != 0 else (abs_shares * price)
                portfolio_dict[isin]['total_sells'] += cash_received

            elif t_type == 'DIVIDEND':
                portfolio_dict[isin]['total_dividends'] += abs(amount_val)

            elif t_type in ['MIGRATION', 'SPLIT', 'REVERSE_SPLIT']:
                portfolio_dict[isin]['shares'] += shares_raw

        new_portfolio = []
        for isin, data in portfolio_dict.items():
            ticker = ISIN_TO_YF.get(isin, isin)
            net_inleg = data['total_buys'] - data['total_sells']
            new_portfolio.append({
                'ticker': ticker,
                'amount': data['shares'],
                'total_buys': data['total_buys'],
                'total_sells': data['total_sells'],
                'total_dividends': data['total_dividends'],
                'net_inleg': net_inleg,
                'class': data['class'],
                'currency': 'EUR',
                'symbol': '€',
                'buy_date': data['buy_date']
            })
        return new_portfolio, dca_history, transactions_list

    # ==================================================
    # 9. SIDEBAR: PROFIEL, INSTELLINGEN & BACKUP
    # ==================================================
    user_name = st.session_state.get("name", "Gebruiker")
    st.sidebar.markdown(f"### 👤 {user_name}")
    authenticator.logout('Uitloggen', 'sidebar')
    st.sidebar.divider()

    with st.sidebar.expander("⚙️ Portfolio Instellingen & Cash", expanded=False):
        cash_val = st.number_input(
            "Vrije Cash / Kasgeld (€)",
            min_value=0.0,
            value=float(st.session_state['settings'].get('cash_eur', 0.0)),
            step=100.0,
            help="Optioneel: je vrije kasgeld bij de broker voor een compleet vermogensoverzicht."
        )
        max_w_val = st.slider(
            "Concentratierisico Waarschuwing (%)",
            min_value=5,
            max_value=50,
            value=int(st.session_state['settings'].get('max_weight_alert', 15)),
            help="Geeft een melding als één los aandeel groter wordt dan dit percentage van je portfolio."
        )
        if st.button("💾 Instellingen Opslaan", use_container_width=True):
            st.session_state['settings']['cash_eur'] = cash_val
            st.session_state['settings']['max_weight_alert'] = float(max_w_val)
            save_portfolio_to_db()
            st.toast("Instellingen opgeslagen!", icon="✅")

    st.sidebar.header("📂 Data & Synchronisatie")
    st.sidebar.caption("💾 Wijzigingen worden automatisch atomair opgeslagen.")

    uploaded_csv = st.sidebar.file_uploader(
        "📈 Importeer Broker CSV",
        type=['csv'],
        help="Upload je Trade Republic transactie-export"
    )
    if uploaded_csv and st.sidebar.button("Verwerk CSV", type="primary", use_container_width=True):
        try:
            parsed_portfolio, parsed_dca, parsed_tx = parse_tr_csv(uploaded_csv)
            if parsed_portfolio:
                st.session_state['portfolio'] = parsed_portfolio
                st.session_state['dca_history'] = parsed_dca
                st.session_state['transactions'] = parsed_tx
                save_portfolio_to_db()
                active_count = len([p for p in parsed_portfolio if p['amount'] > 0.01])
                st.sidebar.success(f"Ingeladen: {active_count} actieve posities & historie!")
                time.sleep(0.8)
                st.rerun()
            else:
                st.sidebar.warning("Geen geldige transacties gevonden in CSV.")
        except Exception as e:
            st.sidebar.error(f"Fout bij verwerken CSV: {e}")

    st.sidebar.divider()

    col_dl1, col_dl2 = st.sidebar.columns(2)
    if st.session_state.get('portfolio'):
        df_export = pd.DataFrame(st.session_state['portfolio'])
        csv_data = df_export.to_csv(index=False).encode('utf-8')
        col_dl1.download_button(
            label="⬇️ CSV",
            data=csv_data,
            file_name="portfolio_export.csv",
            mime="text/csv",
            use_container_width=True,
            help="Exporteer naar Excel/CSV"
        )

    json_export = json.dumps({
        'portfolio': st.session_state.get('portfolio', []),
        'wishlist': st.session_state.get('wishlist', []),
        'dca_history': st.session_state.get('dca_history', {}),
        'transactions': st.session_state.get('transactions', []),
        'settings': st.session_state.get('settings', {})
    }, default=str, indent=2)

    col_dl2.download_button(
        label="⬇️ JSON",
        data=json_export,
        file_name=f"aandelen_backup_{datetime.now().strftime('%Y%m%d')}.json",
        mime="application/json",
        use_container_width=True,
        help="Download volledige JSON-backup"
    )

    uploaded_file = st.sidebar.file_uploader("⬆️ Herstel Backup (JSON)", type=['json'])
    if uploaded_file and st.sidebar.button("📂 Backup Herstellen", use_container_width=True):
        try:
            loaded_data = json.load(uploaded_file)
            if 'portfolio' in loaded_data:
                port = loaded_data['portfolio']
                for p in port:
                    p['buy_date'] = _parse_date_safe(p.get('buy_date'))
                st.session_state['portfolio'] = port
            if 'wishlist' in loaded_data:
                st.session_state['wishlist'] = loaded_data['wishlist']
            elif 'w' in loaded_data:
                st.session_state['wishlist'] = loaded_data['w']
            if 'dca_history' in loaded_data:
                st.session_state['dca_history'] = loaded_data['dca_history']
            if 'transactions' in loaded_data:
                txs = loaded_data['transactions']
                for t in txs:
                    t['date'] = _parse_date_safe(t.get('date')) or datetime.now().date()
                st.session_state['transactions'] = txs
            if 'settings' in loaded_data:
                st.session_state['settings'] = loaded_data['settings']

            save_portfolio_to_db()
            st.toast("Backup succesvol hersteld!", icon="✅")
            time.sleep(0.8)
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Fout bij laden bestand: {e}")

    st.sidebar.divider()
    if st.sidebar.button("🔄 Live Koersen Verversen", use_container_width=True):
        st.cache_data.clear()
        st.toast("Cache geleegd, live data wordt opgehaald!", icon="🔄")
        st.rerun()
    st.sidebar.caption(f"Streamlit v{st.__version__} • Pro Edition")

    # ==================================================
    # 10. HOOFDNAVIGATIE TABS
    # ==================================================
    tabs = st.tabs([
        "📊 Portfolio Pro",
        "🔎 Aandelen Analyse",
        "❤️ Wishlist & Koopzones",
        "🇳🇱 Box 3 Belasting"
    ])

    # --------------------------------------------------
    # TAB 1: PORTFOLIO PRO (Nu als primaire starttab!)
    # --------------------------------------------------
    with tabs[0]:
        if not st.session_state['portfolio']:
            st.info("👋 Welkom in **Aandelen Dashboard Pro**! Je portfolio is nog leeg. Upload links in het menu je **Trade Republic CSV** of een **JSON-backup**, of voeg een aandeel toe via het tabblad **🔎 Aandelen Analyse**.")
        else:
            # 1. Parallel ophalen van alle koersdata (gecachet)
            all_port_tickers = tuple(sorted(set(item['ticker'] for item in st.session_state['portfolio'])))
            with st.spinner("⚡ Live beursdata en valuta's synchroniseren..."):
                batch_ext_data = fetch_batch_portfolio_data(all_port_tickers)

            portfolio_data = []
            monthly_div_projection = {i: 0.0 for i in range(1, 13)}
            sector_distribution = {}
            country_distribution = {}
            currency_distribution = {}

            total_val_eur = 0.0
            total_inleg_eur = 0.0
            total_winst_eur = 0.0
            total_received_div_eur = 0.0

            total_day_change_eur = 0.0
            total_year_change_eur = 0.0
            total_potential_eur = 0.0
            total_expected_div_eur = 0.0
            closed_positions_profit_eur = 0.0

            etf_stats = {'value': 0.0, 'inleg': 0.0, 'profit': 0.0, 'dividends': 0.0}
            stock_stats = {'value': 0.0, 'inleg': 0.0, 'profit': 0.0, 'dividends': 0.0}

            failed_tickers = []
            earnings_history_store = {}
            ticker_details_map = {}
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
                    if amount > 0.01:
                        failed_tickers.append(ticker)
                    ext_data = {
                        'live_price': 0.0, 'target_price': 0.0, 'previous_close': 0.0, 'ytd_start_price': 0.0,
                        'type': 'ETF' if p_class == 'FUND' else 'Aandeel', 'sector': 'Onbekend', 'country': 'Onbekend',
                        'currency_code': item.get('currency', 'EUR'), 'currency_symbol': item.get('symbol', '€'),
                        'shortName': f"⚠️ {ticker}", 'longName': ticker,
                        'earnings': None, 'dividend_history': [], 'div_cagr': 0.0, 'beta': None
                    }

                live_price = float(ext_data.get('live_price', 0.0))
                curr_code = ext_data.get('currency_code', 'EUR')
                eur_rate = get_currency_rate(curr_code, "EUR")

                # Originele Cashflow Winstformule: Actuele Waarde - Netto Inleg + Ontvangen Dividend
                val_eur = (live_price * amount * eur_rate) if amount > 0.01 else 0.0
                total_profit_eur = val_eur - net_inleg_eur + dividends_eur

                total_val_eur += val_eur
                total_inleg_eur += net_inleg_eur
                total_winst_eur += total_profit_eur
                total_received_div_eur += dividends_eur

                if amount <= 0.01:
                    closed_positions_profit_eur += total_profit_eur

                is_etf = (ext_data.get('type') == 'ETF' or p_class == 'FUND')
                if is_etf:
                    etf_stats['inleg'] += net_inleg_eur
                    etf_stats['value'] += val_eur
                    etf_stats['profit'] += total_profit_eur
                    etf_stats['dividends'] += dividends_eur
                else:
                    stock_stats['inleg'] += net_inleg_eur
                    stock_stats['value'] += val_eur
                    stock_stats['profit'] += total_profit_eur
                    stock_stats['dividends'] += dividends_eur

                if amount > 0.01:
                    used_target = ext_data.get('target_price') if ext_data.get('target_price') else live_price
                    total_potential_eur += float(used_target) * amount * eur_rate

                    day_eur_pos = 0.0
                    if not data_failed:
                        if ext_data.get('previous_close'):
                            day_eur_pos = (live_price - float(ext_data['previous_close'])) * amount * eur_rate
                            total_day_change_eur += day_eur_pos

                        # Zuivere YTD berekening in EUR
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

                    sec_name = 'ETF/Overig' if is_etf and ext_data.get('sector') in ('Onbekend', None) else ext_data.get('sector', 'Onbekend')
                    cnt_name = 'Wereldwijd (ETF)' if is_etf and ext_data.get('country') in ('Onbekend', None) else ext_data.get('country', 'Onbekend')

                    sector_distribution[sec_name] = sector_distribution.get(sec_name, 0.0) + val_eur
                    country_distribution[cnt_name] = country_distribution.get(cnt_name, 0.0) + val_eur
                    currency_distribution[curr_code] = currency_distribution.get(curr_code, 0.0) + val_eur

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
                        "Beta": ext_data.get('beta'),
                        "Verwijder": False
                    })

            if failed_tickers:
                st.warning(f"⚠️ Yahoo Finance kon tijdelijk geen koers ophalen voor: **{', '.join(failed_tickers)}**.")

            for p in portfolio_data:
                p['Weging'] = (p['Waarde (€)'] / total_val_eur * 100.0) if total_val_eur > 0 else 0.0

            portfolio_data.sort(key=lambda x: x["Waarde (€)"], reverse=True)

            # ==========================================
            # EXECUTIVE KPI BAR (Altijd zichtbaar bovenaan)
            # ==========================================
            cash_eur = float(st.session_state['settings'].get('cash_eur', 0.0))
            total_net_worth = total_val_eur + cash_eur
            pct_total_profit = (total_winst_eur / total_inleg_eur * 100.0) if total_inleg_eur > 0 else 0.0
            yield_pct = (total_expected_div_eur / total_val_eur * 100.0) if total_val_eur > 0 else 0.0
            yoc_total_pct = (total_expected_div_eur / total_inleg_eur * 100.0) if total_inleg_eur > 0 else 0.0
            day_pct_total = (total_day_change_eur / (total_val_eur - total_day_change_eur) * 100.0) if (total_val_eur - total_day_change_eur) > 0 else 0.0
            ytd_pct = (total_year_change_eur / (total_val_eur - total_year_change_eur) * 100.0) if (total_val_eur - total_year_change_eur) > 0 else 0.0
            upside_pct = ((total_potential_eur - total_val_eur) / total_val_eur * 100.0) if total_val_eur > 0 else 0.0

            k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
            k1.metric(
                "Belegd Vermogen",
                f"€ {total_val_eur:,.2f}",
                delta=f"Incl. cash: € {total_net_worth:,.0f}" if cash_eur > 0 else f"{len(portfolio_data)} posities",
                delta_color="off"
            )
            k2.metric(
                "Netto Inleg",
                f"€ {total_inleg_eur:,.2f}",
                delta=f"Ontv. Div: € {total_received_div_eur:,.0f}",
                delta_color="off",
                help="Som van alle aankopen minus verkopen (Cashflow-methode)."
            )
            k3.metric(
                "Totale Winst",
                f"€ {total_winst_eur:,.2f}",
                delta=f"{pct_total_profit:+.2f}%",
                help="Actuele Waarde minus Netto Inleg + Ontvangen Dividenden (inclusief gesloten posities)."
            )
            k4.metric(
                "Dag Resultaat",
                f"€ {total_day_change_eur:+,.2f}",
                delta=f"{day_pct_total:+.2f}% vandaag"
            )
            k5.metric(
                "Jaar Resultaat (YTD)",
                f"€ {total_year_change_eur:+,.2f}",
                delta=f"{ytd_pct:+.2f}% YTD"
            )
            k6.metric(
                "Verwacht Dividend",
                f"€ {total_expected_div_eur:,.2f}",
                delta=f"€ {total_expected_div_eur/12:,.0f} / mnd",
                delta_color="off"
            )
            k7.metric(
                "Dividend Yield",
                f"{yield_pct:.2f}%",
                delta=f"YoC: {yoc_total_pct:.2f}%",
                delta_color="off",
                help=f"Huidige Yield: {yield_pct:.2f}% | Yield on Cost (op netto inleg): {yoc_total_pct:.2f}% | Analisten Koersdoel Potentieel: {upside_pct:+.1f}%"
            )

            # Concentratierisico check op losse aandelen
            max_w_limit = float(st.session_state['settings'].get('max_weight_alert', 15.0))
            overweight_stocks = [
                f"**{p['Naam']}** ({p['Weging']:.1f}%)"
                for p in portfolio_data
                if p['Type'] == 'Aandeel' and p['Weging'] > max_w_limit
            ]
            if overweight_stocks:
                st.caption(f"⚠️ **Concentratie-indicator:** Losse posities boven je drempel van {max_w_limit:.0f}%: {', '.join(overweight_stocks)}")

            st.write("")

            # ==========================================
            # SUB-TABS BINNEN PORTFOLIO PRO (Strakke UX)
            # ==========================================
            sub_tabs = st.tabs([
                "🗺️ Overzicht & Heatmap",
                "📋 Posities & Beheer",
                "📈 Prestaties, DCA & Risico",
                "💰 Dividend & Cashflow",
                "🎯 Deep-Dive & FIRE Calculator"
            ])

            # ------------------------------------------
            # SUB-TAB 1: OVERZICHT & HEATMAP
            # ------------------------------------------
            with sub_tabs[0]:
                col_hm_hdr, col_hm_opt = st.columns([3, 1])
                with col_hm_hdr:
                    st.markdown("#### 🗺️ Interactieve Portfolio Treemap")
                    st.caption("Grootte van het blok = Actuele Waarde (€). Kleur = Dagresultaat (%) of Totale Winst (%).")
                with col_hm_opt:
                    hm_color_mode = st.selectbox(
                        "Kleur op basis van:",
                        ["Dag %", "Rendement (%)"],
                        index=0,
                        label_visibility="collapsed"
                    )

                if portfolio_data:
                    df_tm = pd.DataFrame(portfolio_data)
                    df_tm['Label'] = df_tm.apply(
                        lambda r: f"<b>{r['Ticker']}</b><br>€ {r['Waarde (€)']:,.0f}<br>{r[hm_color_mode]:+.1f}%",
                        axis=1
                    )
                    scale_bound = 4.0 if hm_color_mode == "Dag %" else 40.0
                    fig_tm = px.treemap(
                        df_tm,
                        path=[px.Constant("Portfolio"), 'Type', 'Sector', 'Ticker'],
                        values='Waarde (€)',
                        color=hm_color_mode,
                        color_continuous_scale=['#E76F51', '#264653', '#2A9D8F'],
                        color_continuous_midpoint=0,
                        range_color=[-scale_bound, scale_bound],
                        hover_data={
                            'Naam': True,
                            'Waarde (€)': ':,.2f',
                            'Totale Winst (€)': ':,.2f',
                            'Rendement (%)': ':.1f',
                            'Dag %': ':.2f',
                            'Weging': ':.1f'
                        }
                    )
                    fig_tm.update_traces(
                        textinfo="label+text",
                        hovertemplate="<b>%{customdata[0]} (%{label})</b><br>Waarde: € %{value:,.2f}<br>Winst: € %{customdata[2]:,.2f} (%{customdata[3]:+.1f}%)<br>Dag: %{customdata[4]:+.2f}%<br>Weging: %{customdata[5]:.1f}%<extra></extra>"
                    )
                    fig_tm.update_layout(
                        height=420,
                        margin=dict(t=10, b=10, l=0, r=0),
                        paper_bgcolor='rgba(0,0,0,0)'
                    )
                    st.plotly_chart(fig_tm, use_container_width=True)

                st.divider()

                # Benchmark: ETF's vs Losse Aandelen
                st.markdown("#### ⚖️ Rendement Benchmark: ETF's vs. Losse Aandelen")
                st.caption("Vergelijk direct of je losse aandelenselectie het beter doet dan je stabiele DCA in ETF's (inclusief alle verkoopwinsten en dividenden).")

                col_etf, col_stock, col_chart_vs = st.columns([1, 1, 2])
                etf_pct = (etf_stats['profit'] / etf_stats['inleg'] * 100.0) if etf_stats['inleg'] > 0 else 0.0
                stock_pct = (stock_stats['profit'] / stock_stats['inleg'] * 100.0) if stock_stats['inleg'] > 0 else 0.0

                with col_etf:
                    st.metric("ETF Totale Winst", f"€ {etf_stats['profit']:,.2f}", f"{etf_pct:+.1f}% op inleg")
                    st.caption(f"Actuele waarde: € {etf_stats['value']:,.2f} | Inleg: € {etf_stats['inleg']:,.2f}")
                with col_stock:
                    st.metric("Aandelen Totale Winst", f"€ {stock_stats['profit']:,.2f}", f"{stock_pct:+.1f}% op inleg")
                    st.caption(f"Actuele waarde: € {stock_stats['value']:,.2f} | Inleg: € {stock_stats['inleg']:,.2f}")
                with col_chart_vs:
                    df_vs = pd.DataFrame({
                        "Categorie": ["ETF", "ETF", "Losse Aandelen", "Losse Aandelen"],
                        "Waarde Type": ["Netto Inleg", "Waarde + Dividend", "Netto Inleg", "Waarde + Dividend"],
                        "Bedrag (€)": [
                            etf_stats['inleg'],
                            etf_stats['value'] + etf_stats.get('dividends', 0.0),
                            stock_stats['inleg'],
                            stock_stats['value'] + stock_stats.get('dividends', 0.0)
                        ]
                    })
                    fig_vs = px.bar(
                        df_vs, x="Categorie", y="Bedrag (€)", color="Waarde Type", barmode="group",
                        color_discrete_sequence=[MATTE_COLORS["charcoal"], MATTE_COLORS["blue"]]
                    )
                    fig_vs.update_layout(
                        height=230,
                        margin=dict(t=10, b=0, l=0, r=0),
                        plot_bgcolor='rgba(0,0,0,0)',
                        paper_bgcolor='rgba(0,0,0,0)',
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                    )
                    st.plotly_chart(fig_vs, use_container_width=True)

                st.divider()

                # Drie Donut Charts (Type, Sector, Land/Valuta)
                comp1, comp2, comp3 = st.columns(3)
                with comp1:
                    st.markdown("**Actueel Kapitaal: ETF vs. Aandelen**")
                    fig_d1 = go.Figure(data=[go.Pie(
                        labels=['ETF', 'Aandelen'],
                        values=[etf_stats['value'], stock_stats['value']],
                        hole=.62,
                        textposition='inside',
                        textinfo='percent+label'
                    )])
                    fig_d1.update_traces(marker=dict(colors=[MATTE_COLORS["blue"], MATTE_COLORS["red"]]))
                    fig_d1.update_layout(
                        showlegend=False, height=240,
                        margin=dict(t=10, b=10, l=0, r=0),
                        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)'
                    )
                    st.plotly_chart(fig_d1, use_container_width=True)

                with comp2:
                    st.markdown("**Sectorverdeling**")
                    if sector_distribution:
                        fig_d2 = go.Figure(data=[go.Pie(
                            labels=list(sector_distribution.keys()),
                            values=list(sector_distribution.values()),
                            hole=.62,
                            textposition='inside',
                            textinfo='percent'
                        )])
                        fig_d2.update_traces(marker=dict(colors=[SECTOR_COLORS.get(k, '#90A4AE') for k in sector_distribution.keys()]))
                        fig_d2.update_layout(
                            showlegend=True, legend=dict(orientation="h", y=-0.15),
                            height=240, margin=dict(t=10, b=10, l=0, r=0),
                            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)'
                        )
                        st.plotly_chart(fig_d2, use_container_width=True)

                with comp3:
                    st.markdown("**Regio / Landenverdeling**")
                    if country_distribution:
                        fig_d3 = go.Figure(data=[go.Pie(
                            labels=list(country_distribution.keys()),
                            values=list(country_distribution.values()),
                            hole=.62,
                            textposition='inside',
                            textinfo='percent'
                        )])
                        fig_d3.update_layout(
                            showlegend=True, legend=dict(orientation="h", y=-0.15),
                            height=240, margin=dict(t=10, b=10, l=0, r=0),
                            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)'
                        )
                        st.plotly_chart(fig_d3, use_container_width=True)

            # ------------------------------------------
            # SUB-TAB 2: POSITIES & BEHEER
            # ------------------------------------------
            with sub_tabs[1]:
                col_info_tbl, col_closed_metric = st.columns([3, 1])
                with col_info_tbl:
                    st.info("💡 **Interactief Beheer:** Pas *Aantal*, *Netto Inleg (€)* of *Datum* direct aan in de tabel. Gesloten posities (0 stuks) worden hier verborgen, maar hun gerealiseerde winst en dividend tellen wél mee in je totaalrendement.")
                with col_closed_metric:
                    st.metric(
                        "Resultaat Gesloten Posities",
                        f"€ {closed_positions_profit_eur:+,.2f}",
                        help="Cumulatieve gerealiseerde winst + dividend uit aandelen die je inmiddels volledig hebt verkocht."
                    )

                df_editor_source = pd.DataFrame(portfolio_data)

                column_settings = {
                    "Verwijder": st.column_config.CheckboxColumn("🗑️", default=False, width="small"),
                    "Ticker": st.column_config.TextColumn("Ticker", disabled=True, width="small"),
                    "Naam": st.column_config.TextColumn("Naam", disabled=True),
                    "Type": st.column_config.TextColumn("Type", disabled=True, width="small"),
                    "Aantal": st.column_config.NumberColumn("Aantal", min_value=0.0, required=True, step=1.0, format="%.4f"),
                    "Netto Inleg (€)": st.column_config.NumberColumn("Netto Inleg (€)", format="€ %.2f", disabled=False),
                    "Prijs (€)": st.column_config.NumberColumn("Koers (€)", format="€ %.2f", disabled=True),
                    "Waarde (€)": st.column_config.NumberColumn("Waarde (€)", format="€ %.2f", disabled=True),
                    "Dag %": st.column_config.NumberColumn("Dag %", format="%+.2f%%", disabled=True),
                    "Ontvangen Div (€)": st.column_config.NumberColumn("Ontv. Div", format="€ %.2f", disabled=True),
                    "Totale Winst (€)": st.column_config.NumberColumn("Totale Winst", format="€ %.2f", disabled=True),
                    "Rendement (%)": st.column_config.NumberColumn("ROI %", format="%+.1f%%", disabled=True),
                    "YoC (%)": st.column_config.NumberColumn("YoC %", format="%.2f%%", disabled=True, help="Yield on Cost: verwacht jaarlijks dividend gedeeld door je netto inleg."),
                    "Weging": st.column_config.ProgressColumn("Weging", format="%.1f%%", min_value=0, max_value=max(25.0, float(df_editor_source['Weging'].max()) if not df_editor_source.empty else 100.0)),
                    "Datum": st.column_config.DateColumn("Eerste Aankoop", format="DD-MM-YYYY", disabled=False),
                    "Earnings": st.column_config.TextColumn("Laatste Q", disabled=True)
                }

                edited_df = st.data_editor(
                    df_editor_source,
                    column_config=column_settings,
                    column_order=(
                        "Verwijder", "Ticker", "Naam", "Type", "Aantal",
                        "Netto Inleg (€)", "Prijs (€)", "Waarde (€)", "Dag %",
                        "Ontvangen Div (€)", "Totale Winst (€)", "Rendement (%)",
                        "YoC (%)", "Weging", "Datum", "Earnings"
                    ),
                    hide_index=True,
                    use_container_width=True,
                    key="main_portfolio_editor"
                )

                # Vergelijk alleen de bewerkbare kolommen om valse triggers te voorkomen
                editable_cols = ["Verwijder", "Aantal", "Netto Inleg (€)", "Datum"]
                if not df_editor_source[editable_cols].equals(edited_df[editable_cols]):
                    inactive_portfolio = [p for p in st.session_state['portfolio'] if float(p.get('amount', 0)) <= 0.01]
                    new_active_portfolio = []

                    for _, row in edited_df.iterrows():
                        if not row['Verwijder']:
                            orig_item = next((item for item in st.session_state['portfolio'] if item['ticker'] == row['Ticker']), None)
                            updated_item = orig_item.copy() if orig_item else {}
                            updated_item.update({
                                'ticker': row['Ticker'],
                                'amount': float(row['Aantal']),
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
                    st.warning("Let op: hiermee wis je al je lokale posities en DCA-historie. Download eerst een JSON-backup in de zijbalk.")
                    confirm_reset = st.checkbox("Ik weet zeker dat ik mijn volledige portfolio wil wissen")
                    if st.button("🗑️ Portfolio Definitief Wissen", disabled=not confirm_reset):
                        st.session_state['portfolio'] = []
                        st.session_state['dca_history'] = {}
                        st.session_state['transactions'] = []
                        save_portfolio_to_db()
                        st.rerun()

            # ------------------------------------------
            # SUB-TAB 3: PRESTATIES, DCA & RISICO
            # ------------------------------------------
            with sub_tabs[2]:
                # 1. Historical Portfolio vs Benchmark
                st.markdown("#### 🏆 Portfolio Prestatie vs. Markt Benchmark (1 Jaar)")
                st.caption("Simuleert hoe jouw huidige portfolioweging het afgelopen jaar heeft gepresteerd ten opzichte van grote beursindices (geïndexeerd op 100).")

                bm_col1, bm_col2 = st.columns([1, 3])
                with bm_col1:
                    bm_choice = st.selectbox(
                        "Kies Benchmark Index:",
                        [
                            ("VWCE.DE", "Vanguard FTSE All-World (VWCE)"),
                            ("^GSPC", "S&P 500 Index (VS)"),
                            ("^AEX", "AEX Index (Nederland)"),
                            ("^NDX", "Nasdaq-100 Index")
                        ],
                        format_func=lambda x: x[1]
                    )
                    bm_period = st.radio("Periode:", ["6mo", "1y", "2y"], index=1, horizontal=True)

                    # Bereken gewogen Beta van portfolio
                    weighted_beta = 0.0
                    beta_weight_sum = 0.0
                    for p in portfolio_data:
                        if p.get('Beta') is not None and p['Waarde (€)'] > 0:
                            weighted_beta += float(p['Beta']) * p['Waarde (€)']
                            beta_weight_sum += p['Waarde (€)']
                    port_beta = (weighted_beta / beta_weight_sum) if beta_weight_sum > 0 else 1.0
                    st.metric(
                        "Gewogen Portfolio Beta",
                        f"{port_beta:.2f}",
                        delta="Lager risico dan markt" if port_beta < 0.95 else "Marktconform" if port_beta <= 1.1 else "Offensiever dan markt",
                        delta_color="off",
                        help="Beta meet de beweeglijkheid t.o.v. de markt (1.0 = beweegt gelijk met de markt, boven 1.0 = volatieler, onder 1.0 = defensiever)."
                    )

                with bm_col2:
                    weights_map = {p['Ticker']: p['Waarde (€)'] for p in portfolio_data if p['Waarde (€)'] > 0}
                    with st.spinner("Benchmark vergelijking berekenen..."):
                        df_bm = get_benchmark_comparison(weights_map, bm_choice[0], bm_period)
                    if df_bm is not None and not df_bm.empty:
                        fig_bm = go.Figure()
                        fig_bm.add_trace(go.Scatter(
                            x=df_bm['Datum'], y=df_bm['Jouw Portfolio (Huidige Weging)'],
                            mode='lines', name='Jouw Portfolio',
                            line=dict(color=MATTE_COLORS["blue"], width=3)
                        ))
                        fig_bm.add_trace(go.Scatter(
                            x=df_bm['Datum'], y=df_bm['Benchmark'],
                            mode='lines', name=bm_choice[1],
                            line=dict(color=MATTE_COLORS["orange"], width=2, dash='dot')
                        ))
                        fig_bm.update_layout(
                            height=320,
                            margin=dict(t=10, b=10, l=0, r=0),
                            yaxis_title="Geïndexeerd (Start = 100)",
                            hovermode="x unified",
                            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                        )
                        st.plotly_chart(fig_bm, use_container_width=True)
                    else:
                        st.info("Onvoldoende historische data beschikbaar voor deze vergelijking.")

                st.divider()

                # 2. DCA Tijdlijn
                st.markdown("#### 📅 Inleg Tijdlijn (DCA) & Continuïteit")
                dca_hist = st.session_state.get('dca_history', {})
                if dca_hist:
                    df_dca = pd.DataFrame([
                        {"Maand": m, "Type": k, "Inleg (€)": v}
                        for m, cats in dca_hist.items() for k, v in cats.items()
                    ]).sort_values(by="Maand")

                    fig_dca = px.bar(
                        df_dca, x='Maand', y='Inleg (€)', color='Type',
                        title='Geïnvesteerd Kapitaal per Aankoopmaand',
                        color_discrete_sequence=[MATTE_COLORS["blue"], MATTE_COLORS["red"]]
                    )
                    fig_dca.update_layout(
                        height=330, xaxis_title="Maand van Aankoop", yaxis_title="Geïnvesteerd (€)",
                        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)'
                    )
                    st.plotly_chart(fig_dca, use_container_width=True)
                else:
                    st.info("Geen maandelijkse aankoopdatums gevonden. Upload je Trade Republic CSV in de zijbalk om de DCA-tijdlijn te vullen.")

                st.divider()

                # 3. Correlatiematrix
                st.markdown("#### 🔗 Risico & Correlatie Matrix (Top 10 Posities)")
                st.caption("Laat zien hoe sterk je grootste posities met elkaar meebewegen (**+1.0 = exact dezelfde richting**, **0.0 = onafhankelijk**, **-1.0 = tegengesteld**).")
                if len(portfolio_data) > 1:
                    top_10_tickers = tuple(p['Ticker'] for p in portfolio_data[:10])
                    with st.spinner("Correlaties berekenen..."):
                        corr_matrix = get_correlation_matrix(top_10_tickers)
                    if corr_matrix is not None:
                        fig_corr = px.imshow(
                            corr_matrix, text_auto=".2f",
                            color_continuous_scale="RdBu_r", zmin=-1, zmax=1
                        )
                        fig_corr.update_layout(
                            height=460,
                            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)'
                        )
                        st.plotly_chart(fig_corr, use_container_width=True)
                else:
                    st.info("Je hebt minimaal 2 actieve posities nodig om correlaties te berekenen.")

            # ------------------------------------------
            # SUB-TAB 4: DIVIDEND & CASHFLOW
            # ------------------------------------------
            with sub_tabs[3]:
                d_kpi1, d_kpi2, d_kpi3, d_kpi4 = st.columns(4)
                d_kpi1.metric("Verwacht Jaardividend", f"€ {total_expected_div_eur:,.2f}")
                d_kpi2.metric("Gemiddeld per Maand", f"€ {total_expected_div_eur / 12.0:,.2f}")
                d_kpi3.metric("Gemiddeld per Dag", f"€ {total_expected_div_eur / 365.0:,.2f}")
                d_kpi4.metric("Yield on Cost (YoC)", f"{yoc_total_pct:.2f}%", delta=f"Reeds ontvangen: € {total_received_div_eur:,.2f}", delta_color="off")

                st.write("")
                col_div1, col_div2 = st.columns(2)
                with col_div1:
                    st.markdown("##### 📅 Verwachte Dividenduitkeringen per Kalendermaand")
                    monthly_vals = [monthly_div_projection[i] for i in range(1, 13)]
                    if sum(monthly_vals) > 0:
                        months = ["Jan", "Feb", "Mrt", "Apr", "Mei", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]
                        avg_m = sum(monthly_vals) / 12.0
                        fig_bar = go.Figure([
                            go.Bar(
                                x=months, y=monthly_vals,
                                marker_color=MATTE_COLORS["green"],
                                text=[f"€{v:,.0f}" if v > 0 else "" for v in monthly_vals],
                                textposition="outside"
                            )
                        ])
                        fig_bar.add_hline(
                            y=avg_m, line_dash="dot", line_color=MATTE_COLORS["yellow"],
                            annotation_text=f"Gem: € {avg_m:,.0f}/mnd"
                        )
                        fig_bar.update_layout(
                            height=330,
                            margin=dict(t=20, b=10, l=0, r=0),
                            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                            xaxis=dict(showgrid=False),
                            yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.2)')
                        )
                        st.plotly_chart(fig_bar, use_container_width=True)
                    else:
                        st.info("Geen maandelijkse dividendhistorie gevonden voor de huidige posities.")

                with col_div2:
                    st.markdown("##### 🏆 Verwacht Dividend per Positie & 3-Jaars Groei (CAGR)")
                    df_div_chart = pd.DataFrame([p for p in portfolio_data if p['Verwacht Div/J (€)'] > 0])
                    if not df_div_chart.empty:
                        df_div_chart = df_div_chart.sort_values(by="Verwacht Div/J (€)", ascending=False)
                        fig_bar2 = px.bar(
                            df_div_chart, x='Ticker', y='Verwacht Div/J (€)', color='Sector',
                            hover_data={'Naam': True, 'YoC (%)': ':.2f', 'Div CAGR (%)': ':.2f'},
                            color_discrete_map=SECTOR_COLORS
                        )
                        fig_bar2.update_layout(
                            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                            height=330, margin=dict(t=20, b=10, l=0, r=0)
                        )
                        st.plotly_chart(fig_bar2, use_container_width=True)
                    else:
                        st.info("Geen dividendbetalende posities gevonden.")

            # ------------------------------------------
            # SUB-TAB 5: DEEP-DIVE & FIRE CALCULATOR
            # ------------------------------------------
            with sub_tabs[4]:
                st.markdown("#### 🔎 Earnings Surprise & Laatste Nieuws per Positie")
                available_tickers = [item['Ticker'] for item in portfolio_data]
                ticker_to_name = {item['Ticker']: item['Naam'] for item in portfolio_data}

                if available_tickers:
                    sel_port_ticker = st.selectbox(
                        "Selecteer een positie uit je portfolio:",
                        available_tickers,
                        format_func=lambda x: f"{ticker_to_name.get(x, x)} ({x})"
                    )
                    if sel_port_ticker:
                        sel_info = ticker_details_map.get(sel_port_ticker, {})
                        long_name = sel_info.get('longName', sel_port_ticker)

                        dd_col1, dd_col2 = st.columns([3, 2])
                        with dd_col1:
                            if sel_port_ticker in earnings_history_store:
                                st.markdown(f"**Kwartaalcijfers (EPS Verwacht vs. Werkelijk): {long_name}**")
                                hist_data = earnings_history_store[sel_port_ticker]
                                df_eps = pd.DataFrame(hist_data)

                                fig_eps = go.Figure()
                                fig_eps.add_trace(go.Scatter(
                                    x=df_eps['date'], y=df_eps['estimate'],
                                    name='Analisten Verwachting', mode='markers+lines',
                                    line=dict(dash='dot', color=MATTE_COLORS["grey"]),
                                    marker=dict(color=MATTE_COLORS["grey"], size=13, symbol='circle')
                                ))
                                fig_eps.add_trace(go.Scatter(
                                    x=df_eps['date'], y=df_eps['reported'],
                                    name='Werkelijk Gerapporteerd', mode='markers+lines',
                                    line=dict(color=MATTE_COLORS["blue"]),
                                    marker=dict(color=MATTE_COLORS["blue"], size=14, symbol='diamond')
                                ))
                                fig_eps.update_layout(
                                    xaxis_title="Kwartaal Datum", yaxis_title="Winst per Aandeel (EPS)",
                                    height=280, margin=dict(t=20, b=0, l=0, r=0),
                                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                                    plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)'
                                )
                                st.plotly_chart(fig_eps, use_container_width=True)

                                latest = df_eps.iloc[-1]
                                est, rep, pct = latest['estimate'], latest['reported'], latest['surprise']
                                diff = rep - est
                                m1, m2, m3, m4 = st.columns(4)
                                m1.metric("Verwacht EPS", f"{est:.2f}")
                                m2.metric("Werkelijk EPS", f"{rep:.2f}")
                                m3.metric("Verschil", f"{diff:+.2f}")
                                m4.metric("Surprise %", f"{pct:+.1f}%", delta="Beat" if pct >= 0 else "Miss", delta_color="normal" if pct >= 0 else "inverse")
                            else:
                                st.info("Geen kwartaal-earnings bekend voor deze positie (bijvoorbeeld bij ETF's).")

                        with dd_col2:
                            st.markdown(f"**📰 Laatste Nieuws: {long_name}**")
                            news = get_google_news(f"{long_name} stock earnings")
                            if news:
                                for n in news:
                                    st.markdown(f"<div class='news-card'><a href='{n['link']}' target='_blank'>{n['title']}</a><br><small>{n['date']}</small></div>", unsafe_allow_html=True)
                            else:
                                st.write("Geen recent nieuws gevonden.")

                st.divider()

                # Uitgebreide Rente-op-Rente / FIRE Calculator
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
                    current_balance = start_cap
                    total_invested = start_cap
                    for _ in years_range:
                        for _ in range(12):
                            current_balance = current_balance * (1.0 + eff_rendement / 12.0) + mnd_inleg
                            total_invested += mnd_inleg
                        values_list.append(round(current_balance, 0))
                        invested_list.append(round(total_invested, 0))

                    winst_rente = current_balance - total_invested
                    safe_withdrawal_mnd = (current_balance * 0.04) / 12.0

                    mc1, mc2, mc3 = st.columns(3)
                    mc1.metric(f"Eindvermogen ({jaren}j)", f"€ {current_balance:,.0f}")
                    mc2.metric("Rendementswinst", f"€ {winst_rente:,.0f}", delta=f"+{(winst_rente/total_invested*100):.0f}% op inleg" if total_invested > 0 else "")
                    mc3.metric("Passief Inkomen (4%-regel)", f"€ {safe_withdrawal_mnd:,.0f} / mnd", help="Volgens de 4% Safe Withdrawal Rate (FIRE) kun je dit bedrag maandelijks onttrekken.")

                    df_comp = pd.DataFrame({
                        "Jaar": years_range,
                        "Portfolio Waarde": values_list,
                        "Eigen Inleg": invested_list
                    })
                    fig_comp = px.area(
                        df_comp, x="Jaar", y=["Eigen Inleg", "Portfolio Waarde"],
                        color_discrete_sequence=[MATTE_COLORS["charcoal"], MATTE_COLORS["green"]]
                    )
                    fig_comp.update_layout(
                        height=280, margin=dict(t=10, b=0, l=0, r=0),
                        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                        hovermode="x unified"
                    )
                    st.plotly_chart(fig_comp, use_container_width=True)

    # --------------------------------------------------
    # TAB 2: AANDELEN ANALYSE (PRO SCREENER & TA)
    # --------------------------------------------------
    with tabs[1]:
        def update_search():
            st.session_state.search_main = st.session_state.search_input_widget

        col_search, col_period, col_chart_type = st.columns([3, 1, 1])
        with col_search:
            st.text_input(
                "🔍 Zoek een aandeel of ETF op naam, ticker of ISIN (druk op Enter):",
                key="search_input_widget",
                value=st.session_state.search_main,
                placeholder="Bijv. ASML.AS, Novo Nordisk, Wolters Kluwer, MSFT...",
                on_change=update_search
            )
            query = st.session_state.search_main.strip()
            if query:
                if "." in query and " " not in query:
                    st.session_state['search_results'] = [query.upper()]
                else:
                    st.session_state['search_results'] = search_yahoo_strict(query)
            else:
                st.session_state['search_results'] = []

            selected_ticker = None
            if st.session_state['search_results']:
                selection = st.selectbox("Selecteer beursnotering:", st.session_state['search_results'])
                if selection:
                    selected_ticker = selection.split(" | ")[0].strip()
            elif query and "." in query:
                selected_ticker = query.upper()

        with col_period:
            chart_period = st.selectbox("Periode", ["1mo", "6mo", "1y", "2y", "5y", "max"], index=2)
        with col_chart_type:
            chart_style = st.selectbox("Grafiekstijl", ["Lijn + SMA", "Candlestick + SMA"], index=0)

        if selected_ticker:
            try:
                with st.spinner(f"Fundamentele & technische data analyseren voor {selected_ticker}..."):
                    fund_bundle = get_cached_fundamentals(selected_ticker)

                info = fund_bundle['info']
                metrics = fund_bundle['metrics']
                score = fund_bundle['score']
                summary = fund_bundle['summary']
                target_val = fund_bundle['target_val']
                graham_number = fund_bundle['graham_number']

                raw_currency = info.get('currency', 'EUR').upper()
                curr_sym = '$' if raw_currency == 'USD' else '€' if raw_currency == 'EUR' else info.get('currencySymbol', raw_currency)
                current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('regularMarketPreviousClose')
                low_52w = info.get('fiftyTwoWeekLow')
                high_52w = info.get('fiftyTwoWeekHigh')

                # Header Card
                with st.container(border=True):
                    h1, h2, h3, h4, h5 = st.columns([2.2, 1, 1, 1, 1])
                    with h1:
                        st.markdown(f"## {info.get('shortName', selected_ticker)}")
                        st.caption(f"**Ticker:** `{selected_ticker}` • **Sector:** {info.get('sector', 'ETF/Overig')} • **Valuta:** {raw_currency}")
                    with h2:
                        st.metric("Huidige Koers", f"{curr_sym} {current_price:.2f}" if current_price else "-")
                    with h3:
                        if target_val and current_price:
                            delta_target = (target_val - current_price) / current_price
                            st.metric("Analisten Doel", f"{curr_sym} {target_val:.2f}", f"{delta_target:+.1%}")
                        else:
                            st.metric("Analisten Doel", "N/A")
                    with h4:
                        if graham_number and current_price:
                            delta_graham = (graham_number - current_price) / current_price
                            st.metric("Graham Waarde", f"{curr_sym} {graham_number:.2f}", f"{delta_graham:+.1%}", help="Fundamentele waarde volgens Benjamin Graham formule: √(22.5 × EPS × Book Value).")
                        else:
                            st.metric("52W Bereik", f"{curr_sym}{low_52w:.0f} - {curr_sym}{high_52w:.0f}" if (low_52w and high_52w) else "N/A")
                    with h5:
                        score_col = "#2A9D8F" if score >= 7.0 else "#F4A261" if score >= 5.0 else "#E76F51"
                        st.markdown(
                            f"<div class='score-badge' style='color:{score_col}; border:4px solid {score_col};'>{score}</div>",
                            unsafe_allow_html=True
                        )

                    st.info(f"🤖 **AI Fundamenteel Oordeel:** {summary}")

                # Fundamentals Grid
                st.markdown("#### 📊 Fundamentele Ratio's (Yahoo Finance & Finviz Gecombineerd)")
                def display_metric(label, key, good_fn, bad_fn, fmt="{:.2f}", is_pct=False):
                    val, src = metrics[key]
                    if val is None:
                        st.metric(label, "N/A", help=f"Bron: {src}")
                        return
                    delta_symbol = "✅ Gezond" if good_fn(val) else "⚠️ Let op" if bad_fn(val) else "⚪ Neutraal"
                    val_display = f"{val:.2f}%" if is_pct else fmt.format(val)
                    st.metric(
                        label, val_display, delta=delta_symbol, delta_color="off",
                        help=f"Exacte waarde: {val:.2f} (Bron: {src})\n\n{TOOLTIPS.get(key, '')}"
                    )

                c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
                with c1: display_metric("P/E Ratio", 'pe', lambda x: 0 < x < 20, lambda x: x > 35 or x < 0)
                with c2: display_metric("PEG Ratio", 'peg', lambda x: 0 < x < 1.5, lambda x: x > 2.5 or x < 0)
                with c3: display_metric("EPS (ttm)", 'eps', lambda x: x > 0, lambda x: x < 0)
                with c4: display_metric("Dividend", 'div', lambda x: x > 2.5, lambda x: x == 0, is_pct=True)
                with c5: display_metric("ROE", 'roe', lambda x: x > 15, lambda x: x < 5, is_pct=True)
                with c6: display_metric("Winstmarge", 'margin', lambda x: x > 20, lambda x: x < 5, is_pct=True)
                with c7: display_metric("Debt / Eq", 'de', lambda x: x < 1.0, lambda x: x > 2.0)
                with c8: display_metric("Current Ratio", 'cr', lambda x: x > 1.5, lambda x: x < 1.0)

                st.write("")

                # Grafiek (incl. SMA 50/200 & RSI-14) + Nieuws
                col_chart, col_news = st.columns([2.2, 1])
                with col_chart:
                    hist_data = yf.Ticker(selected_ticker).history(period=chart_period)
                    if not hist_data.empty:
                        hist_data['SMA50'] = hist_data['Close'].rolling(window=50).mean()
                        hist_data['SMA200'] = hist_data['Close'].rolling(window=200).mean()

                        # Bereken RSI-14
                        delta_p = hist_data['Close'].diff()
                        gain = delta_p.clip(lower=0).rolling(window=14).mean()
                        loss = (-delta_p.clip(upper=0)).rolling(window=14).mean()
                        rs = gain / loss.replace(0, np.nan)
                        hist_data['RSI'] = 100.0 - (100.0 / (1.0 + rs))
                        last_rsi = hist_data['RSI'].iloc[-1] if not pd.isna(hist_data['RSI'].iloc[-1]) else None

                        fig = make_subplots(
                            rows=2, cols=1, shared_xaxes=True,
                            vertical_spacing=0.06, row_heights=[0.74, 0.26]
                        )

                        if chart_style == "Candlestick + SMA":
