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

# ==========================================
# 🔑 VUL HIER JE FMP KEY IN
# ==========================================
JOUW_FMP_KEY = ""  # Zet hier je key, bijv: "12345abcdef"
# ==========================================

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- STATE ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []
if 'search_query' not in st.session_state: st.session_state['search_query'] = ""

# --- CONSTANTEN ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD", "RDSA.AS": "SHEL",
    "SHELL.AS": "SHEL", "WKL.AS": "WTKWY", "ASM.AS": "ASMIY"
}

TAX_DEFAULTS = {
    2023: {"invest": 6.17, "tax": 32},
    2024: {"invest": 6.04, "tax": 36},
    2025: {"invest": 5.88, "tax": 36}
}

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

# --- KEY MANAGEMENT ---
def get_api_key():
    # 1. Check Hardcoded
    if JOUW_FMP_KEY and JOUW_FMP_KEY != "":
        return JOUW_FMP_KEY
    # 2. Check Secrets
    try:
        if st.secrets.get("fmp_key"): return st.secrets["fmp_key"]
    except: pass
    return None

# --- HULPFUNCTIES ---
def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=25):
    if text and len(text) > max_len: return text[:max_len] + ".."
    return text

def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            return data['Close'].iloc[-1]
        return 1.0
    except: return 1.0

# --- DATA BRON 1: FMP API ---
@st.cache_data(ttl=3600)
def get_fmp_data(ticker, api_key):
    """Haalt data op van FMP (Ratios & Quote)"""
    if not api_key: return {}
    data = {}
    try:
        # Endpoint 1: Ratios TTM (Trailing Twelve Months) - Voor PEG, ROE, etc.
        url_ratios = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{ticker}?apikey={api_key}"
        r = requests.get(url_ratios, timeout=4)
        if r.status_code == 200:
            js = r.json()
            if js and isinstance(js, list):
                d = js[0]
                data['peg'] = d.get('pegRatioTTM')
                data['pe'] = d.get('priceEarningsRatioTTM')
                data['pb'] = d.get('priceToBookRatioTTM')
                data['roe'] = d.get('returnOnEquityTTM')
                data['de'] = d.get('debtEquityRatioTTM')
                data['cr'] = d.get('currentRatioTTM')
                data['profit_margin'] = d.get('netProfitMarginTTM')
                data['div_yield'] = d.get('dividendYielTTM')

        # Endpoint 2: Quote - Voor actuele PE, EPS en soms betere prijsinfo
        url_quote = f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={api_key}"
        r2 = requests.get(url_quote, timeout=4)
        if r2.status_code == 200:
            js2 = r2.json()
            if js2 and isinstance(js2, list):
                d2 = js2[0]
                data['eps'] = d2.get('eps')
                # Soms heeft ratios endpoint geen PE, quote wel
                if not data.get('pe'): data['pe'] = d2.get('pe')
                
    except Exception as e:
        print(f"FMP Error: {e}")
    return data

# --- DATA COMBINATIE LOGICA ---
def get_combined_data(ticker, us_ticker, yf_info, fmp_key):
    """
    Combineert data van Yahoo (via yf_info) en FMP API.
    Berekent het gemiddelde als beide beschikbaar zijn.
    """
    
    # 1. Haal FMP Data op
    # Gebruik US ticker voor FMP indien beschikbaar (FMP werkt beter met US tickers)
    fmp_ticker = us_ticker if us_ticker else ticker
    fmp_data = get_fmp_data(fmp_ticker, fmp_key)
    
    # 2. Haal Yahoo Data op (uit info object)
    # We mappen Yahoo keys naar onze interne keys
    yahoo_data = {
        'peg': safe_get(yf_info, 'pegRatio'),       # Cruciaal voor PEG
        'fwd_pe': safe_get(yf_info, 'forwardPE'),
        'pe': safe_get(yf_info, 'trailingPE'),
        'pb': safe_get(yf_info, 'priceToBook'),
        'roe': safe_get(yf_info, 'returnOnEquity'),
        'de': safe_get(yf_info, 'debtToEquity'),
        'cr': safe_get(yf_info, 'currentRatio'),
        'profit_margin': safe_get(yf_info, 'profitMargins'),
        'div_yield': safe_get(yf_info, 'dividendYield'),
        'target': safe_get(yf_info, 'targetMeanPrice'),
        'eps': safe_get(yf_info, 'trailingEps')
    }
    
    # Yahoo geeft Debt/Equity vaak als percentage (bv 150), wij willen decimal (1.5)
    if yahoo_data['de'] and yahoo_data['de'] > 10: 
        yahoo_data['de'] /= 100

    # 3. Aggregatie
    keys = ['peg', 'fwd_pe', 'pb', 'roe', 'de', 'cr', 'profit_margin', 'div_yield', 'target']
    
    final_data = {}
    sources_info = {}
    debug_info = {k: {} for k in keys}

    for k in keys:
        values = []
        source_names = []

        # Check Yahoo
        y_val = yahoo_data.get(k)
        # Soms geeft Yahoo 'fwd_pe' niet, maar wel 'pe', gebruik die als fallback voor PE
        if k == 'fwd_pe' and y_val is None:
            y_val = yahoo_data.get('pe')
        
        if y_val is not None:
            values.append(y_val)
            source_names.append("Yahoo")
            debug_info[k]["Yahoo"] = y_val

        # Check FMP
        f_val = fmp_data.get(k)
        # FMP heeft 'pe' (trailing) meestal ipv fwd_pe, gebruiken we als proxy als we fwd_pe zoeken
        if k == 'fwd_pe' and f_val is None:
            f_val = fmp_data.get('pe')
            
        if f_val is not None:
            values.append(f_val)
            source_names.append("FMP")
            debug_info[k]["FMP"] = f_val

        # Bereken Gemiddelde
        if values:
            final_data[k] = sum(values) / len(values)
            sources_info[k] = " + ".join(source_names)
        else:
            final_data[k] = None
            sources_info[k] = "Geen data"

    # EPS handling (Yahoo is vaak leidend, FMP als backup)
    final_data['eps'] = yahoo_data.get('eps') or fmp_data.get('eps')

    return final_data, sources_info, debug_info

# --- AI ANALYSE ---
def ai_check(metrics, eps):
    score, count = 0, 0
    pos, neg = [], []
    
    checks = [
        ('peg', 0, 1.5, 2.5, "PEG uitstekend", "PEG te hoog"),
        ('fwd_pe', 0, 20, 35, "K/W gunstig", "K/W hoog"),
        ('roe', 0.15, 999, 0.05, "Management top (ROE)", "Zwak rendement (ROE)"),
        ('de', 0, 1.0, 2.0, "Gezonde balans", "Hoge schulden"),
        ('profit_margin', 0.15, 999, 0.0, "Sterke winstmarge", "Dunne marges"),
        ('cr', 1.5, 999, 1.0, "Goede liquiditeit", "Krap bij kas")
    ]
    
    for k, g_min, g_max, b_thresh, t_good, t_bad in checks:
        val = metrics.get(k)
        if val is not None:
            count += 1
            if k in ['roe', 'profit_margin']: # Hoger = Beter
                if val >= g_min: score+=1; pos.append(f"{t_good} ({val:.1%})")
                elif val <= b_thresh: neg.append(f"{t_bad} ({val:.1%})")
                else: score+=0.5
            else: # Ratio (Lager = Beter, maar niet te laag)
                if g_min < val <= g_max: score+=1; pos.append(f"{t_good} ({val:.2f})")
                elif val > b_thresh: neg.append(f"{t_bad} ({val:.2f})")
                else: score+=0.5

    final = (score / count * 10) if count > 0 else 5.0
    
    # Strafpunten
    if (eps and eps < 0) or (metrics.get('profit_margin') and metrics.get('profit_margin') < 0):
        final -= 3.0; final = max(1.0, final)
        neg.insert(0, "⚠️ Verlieslatend bedrijf")
    
    return round(final, 1), pos, neg

# --- SEARCH ---
def search_yahoo_strict(query):
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10"
        r = requests.get(url, headers=HEADERS, timeout=3)
        return [f"{q['symbol']} | {q.get('shortname','')}" for q in r.json().get('quotes', [])]
    except: return []

def get_google_news(query):
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={query}+stock&hl=en-US&gl=US&ceid=US:en", timeout=4)
        root = ET.fromstring(r.content)
        return [{"title": i.find('title').text, "link": i.find('link').text} for i in root.findall('./channel/item')[:4]]
    except: return []

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    try:
        url = f"https://www.youtube.com/results?search_query={query.replace(' ','+')}+stock+analysis+{datetime.now().year}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except: return None

# --- FILE MANAGER ---
def handle_upload():
    f = st.session_state.get('uploader')
    if f:
        try:
            d = json.load(f)
            st.session_state['portfolio'] = d.get('portfolio', [])
            st.session_state['wishlist'] = d.get('wishlist', [])
            st.toast("Geladen!", icon="💾")
        except: st.error("Fout bestand.")

def get_json():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- UI START ---
st.sidebar.header("📂 Menu")
active_key = get_api_key()

if not active_key:
    st.sidebar.warning("⚠️ Geen FMP Key gevonden! Vul deze bovenaan het script in bij 'JOUW_FMP_KEY' of hieronder.")
    user_key = st.sidebar.text_input("FMP API Key:", type="password")
    if user_key: active_key = user_key
else:
    st.sidebar.success("✅ FMP API Actief")

st.sidebar.download_button("⬇️ Backup Opslaan", get_json(), "data.json", "application/json")
st.sidebar.file_uploader("⬆️ Restore Backup", type=['json'], key='uploader', on_change=handle_upload)
st.sidebar.divider()
if st.sidebar.button("🧹 Cache Legen"): st.cache_data.clear(); st.rerun()

tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist", "🇳🇱 Belasting"])

# 1. ANALYSE
with tabs[0]:
    c1, c2 = st.columns([3, 1])
    dq = st.session_state.get('search_query', "")
    txt = c1.text_input("Zoek aandeel:", value=dq, key="s", placeholder="Bijv: ASML, AAPL, SHELL")
    if txt:
        try:
            sel = txt
            if "." not in txt:
                res = search_yahoo_strict(txt)
                sel = st.selectbox("Resultaat:", res).split(" | ")[0] if res else txt.upper()
            
            st.markdown("---")
            tick = yf.Ticker(sel)
            info = tick.info
            hist = tick.history(period="1y")
            
            if hist.empty: st.error("Geen koersdata gevonden.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price = hist['Close'].iloc[-1] * rate
                
                # US Ticker Logic
                us_mt = sel
                if sel in EU_TO_US_MAP: us_mt = EU_TO_US_MAP[sel]
                elif "." in sel:
                    sn = safe_get(info, 'shortName', '').split(' ')[0]
                    found = search_yahoo_strict(sn)
                    if found and "." not in found[0].split(" | ")[0]: us_mt = found[0].split(" | ")[0]

                # DATA FETCH (YAHOO + FMP)
                with st.spinner("Data ophalen en combineren (Yahoo + FMP)..."):
                    data, srcs, debug = get_combined_data(sel, us_mt if us_mt != sel else None, info, active_key)
                
                score, plus, minn = ai_check(data, data.get('eps'))

                # Header
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                c1.title(truncate_text(safe_get(info, 'shortName', sel)))
                c1.caption(f"{sel} | US Data: {us_mt}")
                c2.metric("Prijs", f"€ {price:.2f}")
                
                tgt = data.get('target')
                if tgt:
                    tgt_e = tgt * get_currency_rate("USD", "EUR")
                    ups = ((tgt_e - price)/price)*100
                    c3.metric("Doel (Avg)", f"€ {tgt_e:.2f}", f"{ups:+.1f}%", help=srcs.get('target'))
                else: c3.metric("Doel", "-")

                with c4.popover("➕ Actie"):
                    with st.form("add"):
                        n = st.number_input("Aantal", 1.0)
                        p = st.number_input("GAK", value=float(round(price, 2)))
                        if st.form_submit_button("Portfolio"):
                            st.session_state['portfolio'].append({'ticker': sel, 'amount': n, 'avg_price_eur': p})
                            st.rerun()
                    if st.button("Wishlist"):
                        st.session_state['wishlist'].append({'ticker': sel, 'name': safe_get(info, 'shortName')})
                        st.rerun()

                # AI
                col_s = "#4CAF50" if score > 7 else "#FF9800" if score >= 5.5 else "#F44336"
                st.markdown(f"""
                <div style="border:1px solid #444; padding:15px; border-radius:10px; margin:20px 0;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <h2 style="color:{col_s}; margin:0">Score: {score}/10</h2>
                        <div style="font-size:30px">{"🚀" if score>7 else "⚖️" if score>5.5 else "🐻"}</div>
                    </div>
                    <div style="margin-top:10px; font-size:0.9em;">
                        <b>✅ Plus:</b> {', '.join(plus) if plus else '-'}<br>
                        <b>⚠️ Min:</b> {', '.join(minn) if minn else '-'}
                    </div>
                </div>""", unsafe_allow_html=True)

                # Metrics
                def get_c(k, v):
                    if v is None: return ""
                    if k=='peg': return "🟢" if v<1.5 else "🟠"
                    if k=='fwd_pe': return "🟢" if v<20 else "🔴" if v>35 else "🟠"
                    if k in ['roe', 'profit_margin', 'div_yield']: return "🟢" if v>0.15 else "⚪"
                    if k=='cr': return "🟢" if v>1.5 else "🔴"
                    return "⚪"

                cols = st.columns(4)
                ks = ['peg', 'fwd_pe', 'roe', 'div_yield', 'cr', 'de', 'pb', 'profit_margin']
                ls = ['PEG', 'P/E', 'ROE', 'Yield', 'Current', 'Debt/Eq', 'P/B', 'Marge']
                
                for i, (k, l) in enumerate(zip(ks, ls)):
                    v = data.get(k)
                    clr = get_c(k, v)
                    disp = "-"
                    if v is not None:
                        disp = f"{v*100:.2f}%" if k in ['roe', 'div_yield', 'profit_margin'] else f"{v:.2f}"
                    
                    with cols[i%4]:
                        st.metric(l, f"{clr} {disp}", help=f"{TOOLTIPS.get(k)}\nBron: {srcs.get(k)}")

                with st.expander("🛠️ Bron Details (Alleen Yahoo + FMP)"):
                    st.json(debug)

                st.plotly_chart(px.line(hist, y='Close'))
                
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("##### 📰 Nieuws")
                    for n in get_google_news(safe_get(info, 'shortName', sel)):
                        st.markdown(f"[{n['title']}]({n['link']})")
                with c2:
                    st.markdown("##### 📺 Video")
                    v = get_youtube_video_id(safe_get(info, 'shortName', sel))
                    if v: st.video(f"https://www.youtube.com/watch?v={v}")

        except Exception as e: st.error(f"Fout: {e}")

# 2. PORTFOLIO
with tabs[1]:
    if st.session_state['portfolio']:
        rows, tv, ti, sd, dc = [], 0, 0, {}, []
        bar = st.progress(0)
        for i, item in enumerate(st.session_state['portfolio']):
            try:
                t = item['ticker']
                tk = yf.Ticker(t)
                h = tk.history(period="1d")
                curr = h['Close'].iloc[-1] * get_currency_rate(tk.info.get('currency','EUR'), "EUR") if not h.empty else item['avg_price_eur']
                
                v = curr * item['amount']
                c = item['avg_price_eur'] * item['amount']
                tv += v; ti += c
                sd[tk.info.get('sector','Overig')] = sd.get(tk.info.get('sector','Overig'), 0) + v
                
                dr = tk.info.get('dividendRate')
                if dr: dc.append({"Aandeel": t, "Bedrag": dr * get_currency_rate(tk.info.get('currency','EUR'),"EUR") * item['amount']})
                rows.append({"Naam": t, "Aantal": item['amount'], "Prijs": f"€{curr:.2f}", "Waarde": f"€{v:.2f}", "Winst": f"€{v-c:.2f}"})
            except: pass
            bar.progress((i+1)/len(st.session_state['portfolio']))
        bar.empty()
        
        c1, c2 = st.columns(2)
        c1.metric("Waarde", f"€ {tv:,.2f}"); c2.metric("Winst", f"€ {tv-ti:,.2f}", f"{((tv-ti)/ti)*100:.1f}%" if ti else "0%")
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        if sd: st.plotly_chart(px.pie(values=list(sd.values()), names=list(sd.keys()), hole=0.4))
        if st.button("Leegmaken"): st.session_state['portfolio'] = []; st.rerun()
    else: st.info("Leeg")

# 3. WISHLIST
with tabs[2]:
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            t = item['ticker']
            tk = yf.Ticker(t)
            h = tk.history(period="2d")
            if not h.empty:
                rate = get_currency_rate(tk.info.get('currency','EUR'), "EUR")
                cp = h['Close'].iloc[-1] * rate
                ch = ((cp - h['Close'].iloc[0]*rate)/(h['Close'].iloc[0]*rate))*100
                c1, c2 = st.columns([3,1])
                c1.markdown(f"**{item['name']}** ({t})")
                c2.markdown(f"€{cp:.2f} <span style='color:{'green' if ch>=0 else 'red'}'>({ch:+.1f}%)</span>", unsafe_allow_html=True)
                st.divider()
        if st.button("Leegmaken", key="wl"): st.session_state['wishlist'] = []; st.rerun()
    else: st.info("Leeg")

# 4. BELASTING
with tabs[3]:
    st.header("Box 3")
    c1, c2 = st.columns(2)
    yr = c1.selectbox("Jaar", [2023, 2024, 2025])
    sv = c2.number_input("Start (1 jan)", 0.0)
    ev = c2.number_input("Eind (31 dec)", 0.0)
    inl = c2.number_input("Netto Inleg", 0.0)
    
    d = TAX_DEFAULTS.get(yr, {"invest": 6.04, "tax": 36})
    rend = (sv * d['invest'] / 100)
    st.metric("Belasting", f"€ {rend * d['tax'] / 100:.2f}")
