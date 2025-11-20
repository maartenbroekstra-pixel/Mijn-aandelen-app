import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
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

# --- MAPPING (EU -> US) ---
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "IMCD.AS": "IMCDY"
}

# --- TOOLTIPS ---
TOOLTIPS = {
    "peg": "Prijs/Winst gedeeld door Groei.\n🟢 < 1.5: Goedkoop.\n🔴 Negatief: Krimp.",
    "fwd_pe": "Verwachte K/W.\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Duur.",
    "roe": "Rendement Eigen Vermogen.\n🟢 > 15%: Sterk management.",
    "de": "Schuldratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Liquiditeit.\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3.0: Redelijk.",
    "div": "Dividend.\n🟢 > 3%: Hoog inkomen.",
    "beta": "Volatiliteit.\n🟢 < 0.8: Stabiel.",
    "rsi": "Momentum.\n🟢 < 30: Koopkans?.",
    "margin": "Winstmarge.\n🟢 > 20%: Zeer winstgevend."
}

# --- HULPFUNCTIES ---
@st.cache_data(ttl=3600)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr or from_curr == 'nan': return 1.0
    try:
        ticker = f"{from_curr}{to_curr}=X"
        if from_curr == "USD" and to_curr == "EUR": ticker = "EUR=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD" and to_curr == "EUR" and rate > 1.05: return 1/rate
            return rate
        return 0.95 if from_curr == "USD" else 1.0
    except: return 1.0

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=25):
    if text and len(text) > max_len: return text[:max_len] + "..."
    return text

@st.cache_data(ttl=3600)
def get_finviz_data(ticker):
    try:
        h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(f"https://finviz.com/quote.ashx?t={ticker}", headers=h, timeout=5)
        d = {}
        if r.status_code == 200:
            txt = r.text
            def p(pat):
                m = re.search(pat, txt, re.DOTALL)
                return float(m.group(1)) if m else None
            d['peg'] = p(r'>PEG<.*?<b>\s*([\d\.]+)\s*</b>')
            d['pb'] = p(r'>P/B<.*?<b>\s*([\d\.]+)\s*</b>')
            d['fwd_pe'] = p(r'>Forward P/E<.*?<b>\s*([\d\.]+)\s*</b>')
            d['target'] = p(r'>Target Price<.*?<b>\s*([\d\.]+)\s*</b>')
        return d
    except: return {}

@st.cache_data(ttl=3600)
def scrape_yahoo_peg(ticker):
    """Specifieke scraper voor Yahoo Key Statistics"""
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://finance.yahoo.com/quote/{ticker}/key-statistics", headers=h, timeout=5)
        # Zoek naar PEG in de platte tekst
        match = re.search(r'PEG Ratio \(5 yr expected\).*?([\d\.]+)', r.text)
        if match: return float(match.group(1))
    except: pass
    return None

def search_yahoo(query):
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10", headers=h, timeout=5)
        d = r.json()
        res = []
        if 'quotes' in d:
            qs = d['quotes']
            qs.sort(key=lambda x: 0 if x.get('symbol','').endswith(('.AS','.DE','.F','.CO')) else 1)
            for q in qs:
                res.append(f"{q['symbol']} | {q.get('shortname','')} ({q.get('exchange','')})")
        return res
    except: return []

@st.cache_data(ttl=3600)
def find_primary_ticker(company_name):
    try:
        base = re.split(r' (Inc|Corp|Corporation|Hldgs|Holdings|Group|AG|SE|PLC|Ltd|GmbH|S\.A\.|NV|N\.V\.|B\.V\.)', company_name, flags=re.IGNORECASE)[0].strip()
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={base}&lang=en-US&region=US&quotesCount=5", headers=h, timeout=5)
        d = r.json()
        if 'quotes' in d:
            for q in d['quotes']:
                s = q['symbol']
                if "." not in s and len(s) <= 5: return s
    except: pass
    return None

def get_google_news(query):
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={query}+stock+finance&hl=en-US&gl=US&ceid=US:en", timeout=5)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:5]:
            try: dt_str = datetime.strptime(i.find('pubDate').text[:16], '%a, %d %b %Y').strftime('%d %b')
            except: dt_str = ""
            items.append({"title": i.find('title').text, "link": i.find('link').text, "date": dt_str})
        return items
    except: return []

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    try:
        url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}+stock+analysis+{datetime.now().year}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except: return None

def calculate_score(m):
    score, total = 0, 0
    opps, risks, neut = [], [], []
    
    if m['peg']:
        total+=1
        if 0<m['peg']<1.5: score+=1; opps.append(f"PEG ({m['peg']:.2f}) goedkoop.")
        elif m['peg']<0: risks.append("Negatieve PEG.")
        elif m['peg']>2.5: risks.append(f"Hoge PEG ({m['peg']:.2f}).")
        else: score+=0.5; neut.append("PEG normaal.")
    
    def chk(k, n, good, bad):
        nonlocal score, total
        if m.get(k):
            total+=1
            v = m[k]
            if good(v): score+=1; opps.append(f"{n} ({v:.2f}) sterk.")
            elif bad(v): risks.append(f"{n} ({v:.2f}) zwak.")
            else: score+=0.5; neut.append(f"{n} neutraal.")

    chk('fwd_pe', 'Fwd P/E', lambda x: 0<x<20, lambda x: x>40)
    chk('roe', 'ROE', lambda x: x>0.15, lambda x: x<0.05)
    chk('de', 'D/E', lambda x: x<1.0, lambda x: x>2.0)
    chk('cr', 'Current Ratio', lambda x: x>1.5, lambda x: x<1.0)
    chk('pb', 'P/B', lambda x: 0<x<3.0, lambda x: x>10)

    return (score/total)*10 if total>0 else 5.0, opps, risks, neut

# --- DATA BEHEER ---
def save_data():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- SIDEBAR ---
st.sidebar.header("📂 Data")

# Download (Altijd laatste state)
st.sidebar.download_button(
    f"⬇️ Download ({len(st.session_state['portfolio'])} port / {len(st.session_state['wishlist'])} wish)",
    save_data(),
    "aandelen.json",
    "application/json"
)

# Upload
up = st.sidebar.file_uploader("⬆️ Upload", type=['json'])
if up:
    try:
        up.seek(0)
        d = json.load(up)
        if isinstance(d, list): st.session_state['portfolio'] = d
        elif isinstance(d, dict):
            st.session_state['portfolio'] = d.get('portfolio', [])
            st.session_state['wishlist'] = d.get('wishlist', [])
        st.sidebar.success("✅ Geladen!")
    except: st.sidebar.error("Fout bestand.")

if st.sidebar.button("🔄 Cache Legen"):
    st.cache_data.clear()
    st.rerun()

# --- TABS ---
tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist"])

# ==================================================
# 1. ANALYSE
# ==================================================
with tabs[0]:
    c_src, c_per = st.columns([3, 1])
    with c_src:
        def_q = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek:", value=def_q, placeholder="Heineken, PDD...", key="search")
        sel_ticker = None
        if txt and "." in txt and " " not in txt: sel_ticker = txt
        elif txt:
            res = search_yahoo(txt)
            if res:
                ch = st.selectbox("Resultaten:", res)
                sel_ticker = ch.split(" | ")[0]
            else: sel_ticker = txt.upper()
            
    with c_per:
        per = st.selectbox("Periode", ["1mo", "6mo", "1y", "3y", "5y"], index=2)

    if sel_ticker:
        try:
            st.markdown("---")
            stock = yf.Ticker(sel_ticker)
            info = stock.info
            hist = stock.history(period=per)
            
            if hist.empty:
                st.error("Geen data.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price_eur = hist['Close'].iloc[-1] * rate
                
                # SMART DATA
                mt = sel_ticker
                mi = info
                
                if sel_ticker in EU_TO_US_MAP:
                    mt = EU_TO_US_MAP[sel_ticker]
                    mi = yf.Ticker(mt).info
                elif any(safe_get(info, k) is None for k in ['forwardPE', 'pegRatio', 'targetMeanPrice']):
                    sn = safe_get(info, 'shortName', '')
                    us_t = find_primary_ticker(sn)
                    if us_t and us_t != sel_ticker:
                        mt = us_t
                        mi = yf.Ticker(mt).info

                fin = get_finviz_data(mt)

                # --- MATH ENGINE: VAL & SOURCE ---
                def get_m(key_y, key_f, manual_calc=None):
                    # 1. Yahoo API
                    val = safe_get(mi, key_y)
                    if val is not None: return val, "Yahoo"
                    # 2. Finviz
                    if key_f and fin.get(key_f): return fin[key_f], "Finviz"
                    # 3. Yahoo Scraper (Specifiek voor PEG)
                    if key_y == 'pegRatio':
                        scrape = scrape_yahoo_peg(mt)
                        if scrape: return scrape, "Yahoo Web"
                    # 4. Manual Calc (De Redder)
                    if manual_calc:
                        try:
                            res = manual_calc()
                            if res is not None: return res, "Berekend"
                        except: pass
                    return None, "N/A"

                # 1. PEG
                def calc_peg():
                    pe = safe_get(mi, 'trailingPE') or safe_get(mi, 'forwardPE')
                    g = safe_get(mi, 'earningsGrowth')
                    if not g: g = safe_get(mi, 'revenueGrowth')
                    if pe and g and g > 0: return pe / (g*100)
                    return None
                peg, peg_src = get_m('pegRatio', 'peg', calc_peg)

                # 2. P/E & Fwd P/E
                def calc_pe():
                    eps = safe_get(mi, 'trailingEps')
                    if eps: return price_eur / (eps * rate) # EPS ook converten
                    return None
                pe, pe_src = get_m('trailingPE', None, calc_pe)
                fwd_pe, fwd_src = get_m('forwardPE', 'fwd_pe')
                if not fwd_pe: fwd_pe = pe; fwd_src = "Huidige P/E (Fallback)"

                # 3. P/B
                def calc_pb():
                    bv = safe_get(mi, 'bookValue')
                    if bv: return price_eur / (bv * rate)
                    return None
                pb, pb_src = get_m('priceToBook', 'pb', calc_pb)

                # 4. Debt/Equity
                def calc_de():
                    td = safe_get(mi, 'totalDebt')
                    te = safe_get(mi, 'totalStockholderEquity')
                    if td and te: return td/te
                    return None
                de, de_src = get_m('debtToEquity', None, calc_de)
                # Yahoo geeft D/E vaak als percentage (150), wij willen ratio (1.5)
                if de and de > 10: de = de/100 

                # 5. Current Ratio
                def calc_cr():
                    ca = safe_get(mi, 'totalCurrentAssets')
                    cl = safe_get(mi, 'totalCurrentLiabilities')
                    if ca and cl: return ca/cl
                    return None
                cr, cr_src = get_m('currentRatio', None, calc_cr)

                # 6. ROE
                roe, roe_src = get_m('returnOnEquity', None)

                # 7. Dividend
                dy, dy_src = get_m('dividendYield', None)
                if not dy:
                    dr = safe_get(mi, 'dividendRate')
                    if dr: 
                        # Rate is in originele valuta, dus prijs ook origineel pakken
                        try: 
                            orig_p = yf.Ticker(mt).history(period="1d")['Close'].iloc[-1]
                            dy = dr/orig_p; dy_src = "Berekend"
                        except: pass
                if dy and dy > 0.5: dy = dy/100

                # 8. Target
                tgt, tgt_src = get_m('targetMeanPrice', 'target')
                tgt_eur, upside = None, ""
                if tgt:
                    t_curr = safe_get(mi, 'currency', 'USD')
                    t_rate = get_currency_rate(t_curr, "EUR")
                    tgt_eur = tgt * t_rate
                    upside = f"{((tgt_eur-price_eur)/price_eur)*100:+.1f}%"

                beta = safe_get(mi, 'beta')
                marg = safe_get(mi, 'profitMargins')
                
                delta = hist['Close'].diff()
                gain = (delta.where(delta>0, 0)).rolling(14).mean()
                loss = (-delta.where(delta<0, 0)).rolling(14).mean()
                rs = gain/loss
                rsi = 100 - (100/(1+rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50

                # HEADER
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1:
                    st.title(truncate_text(safe_get(info, 'shortName', sel_ticker)))
                    st.caption(f"{sel_ticker} | Data: {metrics_ticker}")
                with c2: st.metric("Koers", f"€ {price_eur:.2f}")
                with c3: st.metric("Koersdoel", f"€ {tgt_eur:.2f}" if tgt_eur else "N/A", delta=upside, help=f"Bron: {tgt_src}")
                with c4:
                    with st.popover("➕ Toevoegen"):
                        tp, tw = st.tabs(["Port", "Wish"])
                        with tp:
                            with st.form("addp"):
                                a = st.number_input("Aantal", 1.0)
                                p = st.number_input("GAK (€)", value=float(round(price_eur, 2)))
                                if st.form_submit_button("Opslaan"):
                                    st.session_state['portfolio'].append({"ticker": sel_ticker, "amount": a, "avg_price_eur": p})
                                    st.toast("Opgeslagen!", icon="✅")
                                    st.rerun() # FORCEER UPDATE VOOR DOWNLOAD
                        with tw:
                            if st.button("Zet op Wishlist"):
                                st.session_state['wishlist'].append({"ticker": sel_ticker, "name": safe_get(info, 'shortName')})
                                st.toast("Opgeslagen!", icon="❤️")
                                st.rerun() # FORCEER UPDATE VOOR DOWNLOAD

                # SCORE
                m_dict = {'peg': peg, 'fwd_pe': fwd_pe, 'roe': roe, 'de': de, 'cr': cr, 'pb': pb}
                score, opps, risks, neut = calculate_score(m_dict)

                st.markdown("### 🧬 Analyse")
                with st.container(border=True):
                    sc, txt = st.columns([1, 3])
                    with sc:
                        col = "green" if score>=7 else ("orange" if score>=5.5 else "red")
                        st.markdown(f"""<div style="display:flex;justify-content:center;align-items:center;height:100px;"><h1 style="color:{col};font-size:3rem;margin:0;">{score:.1f}</h1></div>""", unsafe_allow_html=True)
                    with txt:
                        c_p, c_r = st.columns(2)
                        with c_p:
                            if opps:
                                st.success("**Kansen**")
                                for o in opps: st.write(f"- {o}")
                        with c_r:
                            if risks:
                                st.error("**Risico's**")
                                for r in risks: st.write(f"- {r}")

                st.divider()
                st.subheader("📊 Ratio's")
                
                def d_m(l, v, s, k, fmt="{:.2f}", cl=None):
                    tt = f"**Bron:** {s}\n\n{TOOLTIPS.get(k,'')}"
                    if v is None: st.metric(l, "N/A", help=tt); return
                    ic = "⚪"
                    if cl:
                        r = cl(v)
                        if r=="g": ic="🟢"
                        elif r=="o": ic="🟠"
                        elif r=="r": ic="🔴"
                        elif r=="b": ic="🔵"
                    st.metric(l, f"{ic} {fmt.format(v)}", help=tt)

                m1, m2, m3, m4, m5, m6 = st.columns(6)
                with m1: d_m("PEG", peg, peg_src, "peg", cl=lambda x: "g" if 0<x<1.5 else ("o" if x<0 else "y"))
                with m2: d_m("Fwd P/E", fwd_pe, fwd_src, "fwd_pe", cl=lambda x: "g" if 0<x<20 else "y")
                with m3: d_m("Current", cr, cr_src, "cr", cl=lambda x: "g" if x>1.5 else "o")
                with m4: d_m("D/E", de, de_src, "de", cl=lambda x: "g" if x<1 else "o")
                with m5: d_m("P/B", pb, pb_src, "pb", cl=lambda x: "g" if 0<x<3 else "y")
                with m6: d_m("ROE", roe, roe_src, "roe", "{:.1%}", cl=lambda x: "g" if x>0.15 else "y")
                
                st.write("")
                x1, x2, x3 = st.columns(3)
                with x1: d_m("Dividend", dy, dy_src, "div", "{:.2%}", cl=lambda x: "g" if x>0.03 else "b")
                with x2: d_m("Beta", beta
