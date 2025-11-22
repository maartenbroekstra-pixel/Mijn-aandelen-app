import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import requests
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
import numpy as np

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Ultimate", layout="wide", page_icon="📈")

# --- STATE ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []
if 'search_query' not in st.session_state: st.session_state['search_query'] = ""

# --- CONSTANTEN ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

# --- TOOLTIPS ---
TOOLTIPS = {
    "peg": "PEG Ratio.\n🟢 < 1.5: Goedkoop t.o.v. groei.\n🟠 > 2.5: Duur.",
    "fwd_pe": "Forward P/E.\n🟢 < 20: Aantrekkelijk.\n🔴 > 35: Premie waardering.",
    "roe": "Return on Equity.\n🟢 > 15%: Sterk management.",
    "de": "Debt/Equity.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio.\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Price/Book.\n🟢 < 3.0: Redelijk.",
    "div_yield": "Dividend.\n🟢 > 2.5%: Goed inkomen.",
    "profit_margin": "Winstmarge.\n🟢 > 15%: Sterk bedrijf."
}

# --- HULPFUNCTIES ---
def get_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=25):
    if text and len(text) > max_len: return text[:max_len] + ".."
    return text

def clean_float(val_str):
    if not val_str: return None
    try:
        clean = re.sub(r'[^\d\.-]', '', str(val_str))
        if not clean: return None
        return float(clean)
    except: return None

@st.cache_data(ttl=300)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            return data['Close'].iloc[-1]
        return 1.0
    except: return 1.0

# --- BRON 1: FMP API (DE BESTE OPTIE) ---
@st.cache_data(ttl=3600)
def get_fmp_data(ticker, api_key):
    """Haalt data op via Financial Modeling Prep API (Gratis Key nodig)"""
    if not api_key: return {}
    data = {}
    try:
        # FMP gebruikt soms andere tickers, we proberen de standaard
        # Ratios endpoint (TTM)
        url = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{ticker}?apikey={api_key}"
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            js = r.json()
            if js:
                d = js[0]
                data['peg'] = d.get('pegRatioTTM')
                data['pe'] = d.get('priceEarningsRatioTTM') # Dit is Trailing, maar goed alternatief
                data['pb'] = d.get('priceToBookRatioTTM')
                data['roe'] = d.get('returnOnEquityTTM')
                data['de'] = d.get('debtEquityRatioTTM')
                data['cr'] = d.get('currentRatioTTM')
                data['profit_margin'] = d.get('netProfitMarginTTM')
                data['div_yield'] = d.get('dividendYielTTM')
        
        # Quote endpoint voor PE en EPS
        url2 = f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={api_key}"
        r2 = requests.get(url2, timeout=3)
        if r2.status_code == 200:
            js2 = r2.json()
            if js2:
                d2 = js2[0]
                data['eps'] = d2.get('eps')
                # Als we nog geen PE hadden:
                if not data.get('pe'): data['pe'] = d2.get('pe')

    except Exception as e:
        print(f"FMP Error: {e}")
    return data

# --- BRON 2, 3, 4: SCRAPERS ---
@st.cache_data(ttl=300)
def scrape_finviz(ticker):
    d = {}
    session = get_session()
    try:
        r = session.get(f"https://finviz.com/quote.ashx?t={ticker}", timeout=4)
        if r.status_code == 200:
            txt = r.text
            def p(l):
                m = re.search(rf'>{l}</td>[^<]*<td[^>]*><b>\s*([\d\.-]+%?)\s*</b>', txt, re.DOTALL)
                v = clean_float(m.group(1)) if m else None
                if v and '%' in m.group(1): v /= 100
                return v
            
            d['peg'] = p('PEG')
            d['fwd_pe'] = p('Forward P/E')
            d['pb'] = p('P/B')
            d['roe'] = p('ROE')
            d['de'] = p('Debt/Eq')
            d['cr'] = p('Current Ratio')
            d['profit_margin'] = p('Profit Margin')
            d['div_yield'] = p('Dividend %')
            d['target'] = p('Target Price')
    except: pass
    return d

@st.cache_data(ttl=300)
def scrape_marketwatch(ticker):
    d = {}
    session = get_session()
    try:
        clean = ticker.split('.')[0]
        r = session.get(f"https://www.marketwatch.com/investing/stock/{clean}", timeout=4)
        txt = r.text
        def mw(l):
            m = re.search(rf'{l}</small>.*?primary\s*">\s*([\d\.,]+)%?\s*<', txt, re.IGNORECASE | re.DOTALL)
            v = clean_float(m.group(1)) if m else None
            if v and '%' in str(m.group(1)): v/=100
            return v
        d['pe'] = mw("P/E Ratio")
        d['pb'] = mw("Price to Book Ratio")
        d['div_yield'] = mw("Yield")
        tgt = re.search(r'Average Target Price.*?primary\s*">\s*([\d\.,]+)', txt, re.IGNORECASE | re.DOTALL)
        if tgt: d['target'] = clean_float(tgt.group(1))
    except: pass
    return d

def get_hybrid_data(ticker, us_ticker, yf_info, fmp_key):
    # 1. FMP API (Hoogste prio als key er is)
    fmp = get_fmp_data(ticker, fmp_key) if fmp_key else {}
    
    # 2. Yahoo API (Zeer stabiel)
    api = {
        'peg': safe_get(yf_info, 'pegRatio'),
        'fwd_pe': safe_get(yf_info, 'forwardPE'),
        'pb': safe_get(yf_info, 'priceToBook'),
        'roe': safe_get(yf_info, 'returnOnEquity'),
        'de': safe_get(yf_info, 'debtToEquity'),
        'cr': safe_get(yf_info, 'currentRatio'),
        'profit_margin': safe_get(yf_info, 'profitMargins'),
        'div_yield': safe_get(yf_info, 'dividendYield'),
        'target': safe_get(yf_info, 'targetMeanPrice'),
        'eps': safe_get(yf_info, 'trailingEps')
    }
    if api['de'] and api['de'] > 10: api['de'] /= 100

    # 3. Scrapers
    fv = scrape_finviz(us_ticker) if us_ticker else {}
    mw = scrape_marketwatch(us_ticker if us_ticker else ticker)

    # 4. Aggregatie
    keys = ['peg', 'fwd_pe', 'pb', 'roe', 'de', 'cr', 'profit_margin', 'div_yield', 'target']
    final = {}
    sources = {}
    debug = {k: {} for k in keys}

    for k in keys:
        vals = []
        srcs = []
        
        # Helper om toe te voegen
        def add(name, val):
            if val is not None:
                vals.append(val)
                srcs.append(name)
            debug[k][name] = val

        # Volgorde van betrouwbaarheid
        add("FMP (API)", fmp.get(k) if k != 'fwd_pe' else fmp.get('pe')) # FMP heeft trailing PE vaak beter
        add("Yahoo (API)", api.get(k))
        add("Finviz", fv.get(k))
        add("MarketWatch", mw.get(k))
        
        if vals:
            final[k] = sum(vals) / len(vals)
            sources[k] = f"{len(vals)} bronnen"
        else:
            final[k] = None
            sources[k] = "Geen data"
            
    final['eps'] = fmp.get('eps') or api.get('eps')
    return final, sources, debug

# --- AI ANALYSE ---
def ai_check(metrics, eps):
    score, count = 0, 0
    pos, neg = [], []
    
    # Checklists
    checks = [
        ('peg', 0, 1.5, 2.5, "PEG uitstekend", "PEG te hoog"),
        ('fwd_pe', 0, 20, 35, "K/W gunstig", "K/W hoog"),
        ('roe', 0.15, 999, 0.05, "Management top (ROE)", "Zwak rendement (ROE)"),
        ('de', 0, 1.0, 2.0, "Gezonde balans", "Hoge schulden"),
        ('profit_margin', 0.15, 999, 0.0, "Sterke winstmarge", "Dunne marges")
    ]
    
    for k, good_min, good_max, bad_thresh, txt_good, txt_bad in checks:
        val = metrics.get(k)
        if val is not None:
            count += 1
            # ROE Logic (hoger is beter)
            if k in ['roe', 'profit_margin']:
                if val >= good_min: score+=1; pos.append(f"{txt_good} ({val:.1%})")
                elif val <= bad_thresh: neg.append(f"{txt_bad} ({val:.1%})")
                else: score+=0.5
            # Ratio Logic (lager is beter, maar > 0)
            else:
                if good_min < val <= good_max: score+=1; pos.append(f"{txt_good} ({val:.2f})")
                elif val > bad_thresh: neg.append(f"{txt_bad} ({val:.2f})")
                else: score+=0.5

    final = (score / count * 10) if count > 0 else 5.0
    if (eps and eps < 0) or (metrics.get('profit_margin') and metrics.get('profit_margin') < 0):
        final -= 3.0; neg.insert(0, "⚠️ Verlieslatend bedrijf")
    
    return max(1.0, min(10.0, round(final, 1))), pos, neg

# --- UI ---
st.sidebar.title("Instellingen")
fmp_key = st.sidebar.text_input("🔑 FMP API Key (Optioneel)", type="password", help="Haal gratis op bij financialmodelingprep.com")
st.sidebar.divider()

tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist", "🇳🇱 Belasting"])

# 1. ANALYSE
with tabs[0]:
    c1, c2 = st.columns([3, 1])
    dq = st.session_state.get('search_query', "")
    search = c1.text_input("Zoek aandeel:", value=dq, key="s")
    if search:
        try:
            # Ticker Search & Info
            tick = yf.Ticker(search if "." in search else search + "") # Simple check
            info = tick.info
            # Als yfinance niks vindt, probeer search API
            if 'regularMarketPrice' not in info and 'symbol' not in info:
                # Search logic hier (ingekort voor overzicht)
                pass 
            
            # Prijzen
            hist = tick.history(period="1y")
            curr = safe_get(info, 'currency', 'EUR')
            rate = get_currency_rate(curr, "EUR")
            price = hist['Close'].iloc[-1] * rate
            
            # US Ticker (Simpel)
            us_tick = info.get('symbol') # Default
            
            with st.spinner("Analyseren..."):
                data, srcs, debug = get_hybrid_data(info.get('symbol'), us_tick, info, fmp_key)
            
            score, plus, minn = ai_check(data, data.get('eps'))

            # Weergave
            st.title(f"{info.get('shortName')} ({info.get('symbol')})")
            c1, c2, c3 = st.columns(3)
            c1.metric("Prijs", f"€ {price:.2f}")
            
            tgt = data.get('target')
            if tgt:
                tgt_e = tgt * get_currency_rate("USD", "EUR") # Aanname targets vaak USD bij scrapers
                ups = ((tgt_e - price)/price)*100
                c2.metric("Consensus Doel", f"€ {tgt_e:.2f}", f"{ups:+.1f}%")
            
            # AI Blok
            col = "#4CAF50" if score > 7 else "#F44336"
            st.markdown(f"""
            <div style="padding:15px; border:1px solid #444; border-radius:10px; margin:20px 0;">
                <h2 style="color:{col}; margin:0">AI Score: {score}/10</h2>
                <div style="display:flex; gap:20px; margin-top:10px;">
                    <div style="color:#888">✅ {', '.join(plus) if plus else '-'}</div>
                    <div style="color:#888">⚠️ {', '.join(minn) if minn else '-'}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # Metrics
            cols = st.columns(4)
            ks = ['peg', 'fwd_pe', 'roe', 'div_yield', 'cr', 'de', 'pb', 'profit_margin']
            ls = ['PEG', 'P/E', 'ROE', 'Yield', 'Current', 'Debt/Eq', 'P/B', 'Marge']
            
            for i, (k, l) in enumerate(zip(ks, ls)):
                v = data.get(k)
                val_s = "-"
                clr = ""
                if v is not None:
                    val_s = f"{v*100:.2f}%" if k in ['roe', 'div_yield', 'profit_margin'] else f"{v:.2f}"
                    # Kleur logica (Simpel)
                    if k=='peg' and v<1.5: clr="🟢"
                    elif k=='peg' and v>2.5: clr="🟠"
                
                with cols[i%4]:
                    st.metric(l, f"{clr} {val_s}", help=f"Bronnen: {srcs.get(k)}")
            
            with st.expander("🔍 Bron details"):
                st.json(debug)

            st.plotly_chart(px.line(hist, y="Close", title="Koersverloop"))

            # Knoppen
            c1, c2 = st.columns(2)
            if c1.button("➕ Portfolio"):
                st.session_state['portfolio'].append({'ticker': info['symbol'], 'amount': 10, 'avg_price_eur': price})
                st.rerun()
            if c2.button("❤️ Wishlist"):
                st.session_state['wishlist'].append({'ticker': info['symbol'], 'name': info.get('shortName')})
                st.rerun()

        except Exception as e: st.error(f"Fout: {e}")

# ... (Portfolio, Wishlist, Belasting tabs blijven ongewijzigd zoals in vorig script) ...
# Om de lengte beheersbaar te houden heb ik de standaard tabs hier ingekort in de weergave,
# maar je kunt de code van het vorige antwoord voor die tabs 1-op-1 overnemen.
