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
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- STATE ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []
if 'search_query' not in st.session_state: st.session_state['search_query'] = ""

# --- CONSTANTEN & HEADERS ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
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
    "peg": "Price/Earnings to Growth.\n🟢 < 1.5: Goedkoop t.o.v. groei.\n🟠 > 2.5: Duur.",
    "fwd_pe": "Forward P/E (Verwachte K/W).\n🟢 < 20: Aantrekkelijk.\n🔴 > 35: Premie waardering.",
    "roe": "Return on Equity.\n🟢 > 15%: Sterk management.\n🟠 < 10%: Matig.",
    "de": "Debt/Equity (Schuld).\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Price/Book.\n🟢 < 3.0: Redelijk.\n🟠 > 5.0: Duur (afh. van sector).",
    "div_yield": "Dividend Rendement.\n🟢 > 2.5%: Goede basis.\n⚪ 0%: Geen dividend.",
    "profit_margin": "Winstmarge.\n🟢 > 15%: Sterke 'Moat'.\n🔴 < 0%: Verlieslatend.",
    "target": "Gemiddeld koersdoel van analisten."
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

def normalize_percent(val):
    """Zorgt dat percentages altijd decimalen zijn (bijv 15% -> 0.15)"""
    if val is None: return None
    # Als waarde groter is dan 5 (bijv 15.5), ga ervan uit dat het een percentage is -> delen door 100
    # Tenzij het PE of PEG is, die mogen groter zijn.
    if val > 5.0: return val / 100.0
    return val

def clean_float(val_str):
    if not val_str: return None
    try:
        clean = re.sub(r'[^\d\.-]', '', val_str)
        if not clean or clean == '.' or clean == '-': return None
        val = float(clean)
        # Check of er een % teken stond in de string, zo ja, direct delen
        if '%' in val_str: return val / 100.0
        return val
    except: return None

@st.cache_data(ttl=300)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD" and rate > 1.05: return 1/rate
            return rate
        return 1.0
    except: return 1.0

# --- SCRAPERS (Backup & Consensus) ---

@st.cache_data(ttl=300)
def scrape_finviz(ticker):
    """Finviz (US Only)"""
    d = {}
    session = get_session()
    try:
        r = session.get(f"https://finviz.com/quote.ashx?t={ticker}", timeout=4)
        if r.status_code == 200:
            txt = r.text
            def p(label):
                m = re.search(rf'>{label}</td>[^<]*<td[^>]*><b>\s*([\d\.-]+%?)\s*</b>', txt, re.DOTALL)
                return clean_float(m.group(1)) if m and m.group(1)!='-' else None
            
            d['peg'] = p('PEG')
            d['fwd_pe'] = p('Forward P/E')
            d['pb'] = p('P/B')
            d['roe'] = normalize_percent(p('ROE'))
            d['de'] = p('Debt/Eq')
            d['cr'] = p('Current Ratio')
            d['profit_margin'] = normalize_percent(p('Profit Margin'))
            d['div_yield'] = normalize_percent(p('Dividend %'))
            d['target'] = p('Target Price')
            d['eps'] = p('EPS \(ttm\)')
    except: pass
    return d

@st.cache_data(ttl=300)
def scrape_marketwatch(ticker):
    """MarketWatch"""
    data = {}
    session = get_session()
    try:
        clean_ticker = ticker.split('.')[0] 
        url = f"https://www.marketwatch.com/investing/stock/{clean_ticker}"
        r = session.get(url, timeout=4)
        txt = r.text

        def get_mw(label):
            m = re.search(rf'{label}</small>.*?primary\s*">\s*([\d\.,]+)%?\s*<', txt, re.IGNORECASE | re.DOTALL)
            return clean_float(m.group(1)) if m else None

        data['pe'] = get_mw("P/E Ratio")
        data['pb'] = get_mw("Price to Book Ratio")
        data['div_yield'] = normalize_percent(get_mw("Yield"))
        
        m_tgt = re.search(r'Average Target Price.*?primary\s*">\s*([\d\.,]+)', txt, re.IGNORECASE | re.DOTALL)
        if m_tgt: data['target'] = clean_float(m_tgt.group(1))

    except: pass
    return data

def get_hybrid_data(ticker, us_ticker, yf_info):
    """
    Strategie:
    1. Haal ALTIJD data uit yf_info (API) -> Dit is de basis (zeer betrouwbaar).
    2. Probeer te scrapen (Finviz/Marketwatch).
    3. Als scrape lukt: Bereken gemiddelde.
    4. Als scrape faalt: Gebruik API data.
    """
    
    # 1. API DATA (Base Layer)
    api_data = {
        'peg': safe_get(yf_info, 'pegRatio'),
        'fwd_pe': safe_get(yf_info, 'forwardPE'),
        'pb': safe_get(yf_info, 'priceToBook'),
        'roe': safe_get(yf_info, 'returnOnEquity'), # is al decimal (0.15)
        'de': safe_get(yf_info, 'debtToEquity'),
        'cr': safe_get(yf_info, 'currentRatio'),
        'profit_margin': safe_get(yf_info, 'profitMargins'), # is al decimal
        'div_yield': safe_get(yf_info, 'dividendYield'), # is al decimal
        'target': safe_get(yf_info, 'targetMeanPrice'),
        'eps': safe_get(yf_info, 'trailingEps')
    }
    
    # Correcties op API data
    if api_data['de'] and api_data['de'] > 10: api_data['de'] /= 100 # API geeft soms 450 ipv 4.5
    
    # 2. SCRAPING (Verification Layer)
    df = scrape_finviz(us_ticker) if us_ticker else {}
    dm = scrape_marketwatch(us_ticker if us_ticker else ticker)
    
    # 3. COMBINEREN
    keys = ['peg', 'fwd_pe', 'pb', 'roe', 'de', 'cr', 'profit_margin', 'div_yield', 'target']
    final = {}
    sources = {}
    debug = {k: {} for k in keys}

    for k in keys:
        vals = []
        src_list = []
        
        # Voeg API toe (als beschikbaar)
        if api_data.get(k) is not None:
            vals.append(api_data[k])
            src_list.append("API")
            debug[k]["API"] = api_data[k]
            
        # Voeg Finviz toe
        if df.get(k) is not None:
            vals.append(df[k])
            src_list.append("Finviz")
            debug[k]["Finviz"] = df[k]

        # Voeg MarketWatch toe
        if dm.get(k) is not None:
            vals.append(dm[k])
            src_list.append("MarketWatch")
            debug[k]["MW"] = dm[k]
            
        # Bereken Gemiddelde
        if vals:
            final[k] = sum(vals) / len(vals)
            sources[k] = f"{len(vals)} bronnen ({', '.join(src_list)})"
        else:
            final[k] = None
            sources[k] = "Geen data"
            
    final['eps'] = api_data.get('eps') # EPS uit API is leidend
    
    return final, sources, debug

# --- AI ANALYSE ---
def generate_ai_analysis(metrics, eps=None):
    score = 0
    count = 0
    reasons, risks = [], []
    
    # PEG
    peg = metrics.get('peg')
    if peg:
        count += 1
        if 0 < peg < 1.5: score += 1; reasons.append(f"Zeer aantrekkelijke PEG ratio ({peg:.2f}).")
        elif peg > 2.5: risks.append(f"Hoge waardering t.o.v. groei (PEG: {peg:.2f}).")
        else: score += 0.5
    
    # PE
    pe = metrics.get('fwd_pe')
    if pe:
        count += 1
        if 0 < pe < 20: score += 1; reasons.append(f"Gunstige koers/winst verhouding ({pe:.1f}).")
        elif pe > 35: risks.append(f"Hoge koers/winst verhouding ({pe:.1f}).")
        else: score += 0.5

    # ROE
    roe = metrics.get('roe')
    if roe:
        count += 1
        if roe > 0.15: score += 1; reasons.append(f"Sterk rendement op eigen vermogen ({roe:.1%}).")
        elif roe < 0.05: risks.append(f"Laag rendement op eigen vermogen ({roe:.1%}).")
        else: score += 0.5

    # Profit Margin
    pm = metrics.get('profit_margin')
    if pm:
        count += 1
        if pm > 0.15: score += 1; reasons.append(f"Hoge winstmarges ({pm:.1%}).")
        elif pm < 0.02: risks.append(f"Flinterdunne marges ({pm:.1%}).")
        else: score += 0.5

    # Final Score Calculation
    final_score = (score / count * 10) if count > 0 else 5.0
    
    # Penalties
    if (eps and eps < 0) or (pm and pm < 0):
        final_score -= 3.0
        risks.insert(0, "⚠️ Bedrijf maakt verlies!")
    
    if count < 4:
        final_score *= 0.8
        risks.append("Beperkte data beschikbaar voor analyse.")

    return max(1.0, min(10.0, round(final_score, 1))), reasons, risks

# --- SEARCH ---
def search_yahoo_strict(query):
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10"
        r = requests.get(url, headers=HEADERS, timeout=3)
        qs = r.json().get('quotes', [])
        return [f"{q['symbol']} | {q.get('shortname','')}" for q in qs]
    except: return []

def get_google_news(query):
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={query}+stock&hl=en-US&gl=US&ceid=US:en", timeout=4)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:4]:
            d = i.find('pubDate').text[:16]
            items.append({"title": i.find('title').text, "link": i.find('link').text, "date": d})
        return items
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
            f.seek(0)
            d = json.load(f)
            st.session_state['portfolio'] = d.get('portfolio', [])
            st.session_state['wishlist'] = d.get('wishlist', [])
            st.toast("Geladen!", icon="💾")
        except: st.error("Fout bestand.")

def get_json():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- UI START ---
st.sidebar.header("📂 Menu")
st.sidebar.download_button("⬇️ Backup Opslaan", get_json(), "data.json", "application/json")
st.sidebar.file_uploader("⬆️ Restore Backup", type=['json'], key='uploader', on_change=handle_upload)
st.sidebar.divider()
if st.sidebar.button("🧹 Cache Legen"): st.cache_data.clear(); st.rerun()

tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist", "🇳🇱 Belasting"])

# ==================================================
# 1. ANALYSE
# ==================================================
with tabs[0]:
    c1, c2 = st.columns([3, 1])
    with c1:
        dq = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek aandeel:", value=dq, placeholder="Bijv: PYPL, ASML...", key="search_main")
        sel = None
        if txt:
            if "." in txt: sel = txt
            else:
                res = search_yahoo_strict(txt)
                sel = st.selectbox("Resultaat:", res).split(" | ")[0] if res else txt.upper()
    with c2:
        per = st.selectbox("Periode", ["1mo", "6mo", "1y", "5y"], index=2)

    if sel:
        try:
            st.markdown("---")
            tick = yf.Ticker(sel)
            info = tick.info # DIT IS NU DE HOOFDBRON
            hist = tick.history(period=per)
            
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
                    if found and "." not in found[0].split(" | ")[0]:
                        us_mt = found[0].split(" | ")[0]

                # HYBRIDE DATA FETCH
                with st.spinner(f"Data ophalen (API + Scraping Consensus)..."):
                    data, sources, debug_data = get_hybrid_data(sel, us_mt if us_mt != sel else None, info)
                
                score, reasons, risks = generate_ai_analysis(data, data.get('eps'))

                # HEADER
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1: 
                    st.title(truncate_text(safe_get(info, 'shortName', sel)))
                    st.caption(f"Ticker: {sel} | US Data: {us_mt}")
                with c2: st.metric("Prijs", f"€ {price:.2f}")
                with c3:
                    tgt = data.get('target')
                    if tgt:
                        tgt_curr = "USD" if us_mt != sel else curr
                        tgt_eur = tgt * get_currency_rate(tgt_curr, "EUR")
                        upside = ((tgt_eur - price) / price) * 100
                        st.metric("Doel (Consensus)", f"€ {tgt_eur:.2f}", delta=f"{upside:+.1f}%", help=f"Bronnen: {sources.get('target')}")
                    else: st.metric("Doel", "-")
                with c4:
                    with st.popover("➕ Actie"):
                        with st.form("add"):
                            n = st.number_input("Aantal", 1.0)
                            p = st.number_input("GAK", value=float(round(price, 2)))
                            if st.form_submit_button("Portfolio"):
                                st.session_state['portfolio'].append({'ticker': sel, 'amount': n, 'avg_price_eur': p})
                                st.rerun()
                        if st.button("Wishlist"):
                            st.session_state['wishlist'].append({'ticker': sel, 'name': safe_get(info, 'shortName')})
                            st.rerun()

                # AI CARD
                st.markdown("### 🤖 AI Oordeel")
                col_s = "#4CAF50" if score >= 7 else "#FF9800" if score >= 5.5 else "#F44336"
                icon_s = "🚀" if score >= 7 else "⚖️" if score >= 5.5 else "🐻"
                
                with st.container(border=True):
                    ic, tx = st.columns([1, 5])
                    with ic:
                        st.markdown(f"<div style='text-align:center; color:{col_s};'><h1 style='margin:0'>{score}</h1><div style='font-size:40px'>{icon_s}</div></div>", unsafe_allow_html=True)
                    with tx:
                        if reasons:
                            st.markdown("**Positief:**")
                            for r in reasons: st.markdown(f"✅ {r}")
                        if risks:
                            st.markdown("**Risico / Aandacht:**")
                            for r in risks: st.markdown(f"⚠️ {r}")

                # METRICS GRID MET KLEUREN
                st.markdown("### 📊 Fundamentele Cijfers")
                
                # Functie voor kleur logica
                def get_color(key, val):
                    if val is None: return ""
                    if key == 'peg': return "🟢" if 0<val<1.5 else "🟠" if val>2.5 else "⚪"
                    if key == 'fwd_pe': return "🟢" if 0<val<20 else "🔴" if val>35 else "🟠"
                    if key == 'roe': return "🟢" if val>0.15 else "🟠" if val<0.05 else "⚪"
                    if key == 'div_yield': return "🟢" if val>0.025 else "⚪"
                    if key == 'cr': return "🟢" if val>1.5 else "🔴" if val<1.0 else "🟠"
                    if key == 'de': return "🟢" if val<1.0 else "🔴" if val>2.0 else "🟠"
                    if key == 'pb': return "🟢" if 0<val<3.0 else "🟠" if val>5.0 else "⚪"
                    if key == 'profit_margin': return "🟢" if val>0.15 else "🔴" if val<0 else "🟠"
                    return "⚪"

                m_col1 = st.columns(4)
                keys1 = ['peg', 'fwd_pe', 'roe', 'div_yield']
                lbls1 = ['PEG Ratio', 'Fwd P/E', 'ROE', 'Dividend']
                
                for i, (k, l) in enumerate(zip(keys1, lbls1)):
                    v = data.get(k)
                    src = sources.get(k, "?")
                    c = get_color(k, v)
                    
                    val_disp = "-"
                    if v is not None:
                        val_disp = f"{v*100:.2f}%" if k in ['roe', 'div_yield'] else f"{v:.2f}"
                    
                    with m_col1[i]:
                        st.metric(l, f"{c} {val_disp}", help=TOOLTIPS.get(k))
                        st.caption(f"Bron: {src}")

                m_col2 = st.columns(4)
                keys2 = ['cr', 'de', 'pb', 'profit_margin']
                lbls2 = ['Current Ratio', 'Debt/Equity', 'P/B', 'Winstmarge']
                
                for i, (k, l) in enumerate(zip(keys2, lbls2)):
                    v = data.get(k)
                    src = sources.get(k, "?")
                    c = get_color(k, v)
                    
                    val_disp = "-"
                    if v is not None:
                        val_disp = f"{v*100:.2f}%" if k == 'profit_margin' else f"{v:.2f}"
                    
                    with m_col2[i]:
                        st.metric(l, f"{c} {val_disp}", help=TOOLTIPS.get(k))
                        st.caption(f"Bron: {src}")

                with st.expander("🛠️ Technische Debug (Ruwe Data per Bron)"):
                    st.json(debug_data)

                st.plotly_chart(px.line(x=hist.index, y=hist['Close']), use_container_width=True)
                
                # Nieuws en Video
                cn, cv = st.columns(2)
                with cn:
                    st.markdown("##### 📰 Nieuws")
                    for n in get_google_news(safe_get(info, 'shortName', sel)):
                        st.markdown(f"[{n['title']}]({n['link']})")
                with cv:
                    st.markdown("##### 📺 Video")
                    vid = get_youtube_video_id(safe_get(info, 'shortName', sel))
                    if vid: st.video(f"https://www.youtube.com/watch?v={vid}")

        except Exception as e: st.error(f"Fout: {e}")

# ==================================================
# 2. PORTFOLIO
# ==================================================
with tabs[1]:
    if st.session_state['portfolio']:
        rows = []
        tv = 0
        ti = 0
        sd = {}
        dc = []
        
        bar = st.progress(0)
        for i, item in enumerate(st.session_state['portfolio']):
            try:
                t = item['ticker']
                tk = yf.Ticker(t)
                h = tk.history(period="1d")
                if not h.empty:
                    r = get_currency_rate(safe_get(tk.info, 'currency', 'EUR'), "EUR")
                    cur = h['Close'].iloc[-1] * r
                else: cur = item['avg_price_eur']
                
                v = cur * item['amount']
                c = item['avg_price_eur'] * item['amount']
                tv += v
                ti += c
                
                s = safe_get(tk.info, 'sector', 'Overig')
                sd[s] = sd.get(s, 0) + v

                dr = safe_get(tk.info, 'dividendRate')
                if dr:
                    dye = dr * r * item['amount']
                    ex = safe_get(tk.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m') if ex else "-"
                    dc.append({"Aandeel": t, "Datum": exs, "Bedrag": dye})

                rows.append({"Naam": t, "Aantal": item['amount'], "Prijs": f"€{cur:.2f}", "Waarde": f"€{v:.2f}", "Winst": f"€{v-c:.2f}"})
            except: pass
            bar.progress((i+1)/len(st.session_state['portfolio']))
        bar.empty()
        
        k1, k2 = st.columns(2)
        k1.metric("Totale Waarde", f"€ {tv:,.2f}")
        k2.metric("Totaal Winst", f"€ {tv-ti:,.2f}", delta=f"{((tv-ti)/ti)*100:.1f}%" if ti else "0%")
        
        c1, c2 = st.columns(2)
        with c1:
            if sd and tv > 0: st.plotly_chart(px.pie(values=list(sd.values()), names=list(sd.keys()), hole=0.4), use_container_width=True)
        with c2:
            if dc:
                dfd = pd.DataFrame(dc)
                st.write("Verwacht Dividend (Jaar)")
                st.dataframe(dfd, hide_index=True)
        
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if st.button("Portfolio Legen"): st.session_state['portfolio'] = []; st.rerun()
    else: st.info("Je portfolio is leeg. Voeg aandelen toe via tabblad 'Analyse'.")

# ==================================================
# 3. WISHLIST
# ==================================================
with tabs[2]:
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            t = item['ticker']
            try:
                tk = yf.Ticker(t)
                h = tk.history(period="2d")
                if not h.empty:
                    r = get_currency_rate(safe_get(tk.info, 'currency', 'EUR'), "EUR")
                    cp = h['Close'].iloc[-1] * r
                    ch = ((cp - h['Close'].iloc[0] * r) / (h['Close'].iloc[0] * r)) * 100
                    col = "green" if ch >= 0 else "red"
                    
                    c1, c2 = st.columns([3, 1])
                    with c1: st.markdown(f"**{item['name']}** ({t})")
                    with c2: st.markdown(f"€{cp:.2f} <span style='color:{col}'>({ch:+.1f}%)</span>", unsafe_allow_html=True)
                    st.divider()
            except: pass
        if st.button("Wishlist Legen"): st.session_state['wishlist'] = []; st.rerun()
    else: st.info("Leeg.")

# ==================================================
# 4. BELASTING
# ==================================================
with tabs[3]:
    st.header("Box 3 Calculator")
    c1, c2 = st.columns(2)
    with c1:
        yr = st.selectbox("Jaar", [2023, 2024, 2025])
        d = TAX_DEFAULTS.get(yr, {"invest": 6.04, "tax": 36})
        fr = st.number_input("Fictief %", value=d["invest"])
        tr = st.number_input("Tax %", value=float(d["tax"]))
    with c2:
        sv = st.number_input("Start (1 jan)", 0.0)
        ev = st.number_input("Eind (31 dec)", 0.0)
        inl = st.number_input("Netto Inleg", 0.0)
    
    real = (ev - sv) - inl
    tax = (sv * fr / 100) * (tr / 100)
    
    c3, c4 = st.columns(2)
    with c3: st.metric("Werkelijk Rendement", f"€ {real:.2f}")
    with c4: st.metric("Belasting", f"€ {tax:.2f}")
