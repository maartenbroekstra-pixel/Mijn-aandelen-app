import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
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

# --- MAPPING (EU -> US) ---
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "IMCD.AS": "IMCDY", "0DD.F": "PDD",
    "SAP.DE": "SAP", "DHER.DE": "DHER.DE" 
}

# --- BELASTING DEFAULT ---
TAX_DEFAULTS = {
    2023: {"invest": 6.17, "tax": 32},
    2024: {"invest": 6.04, "tax": 36},
    2025: {"invest": 5.88, "tax": 36}
}

# --- TOOLTIPS ---
TOOLTIPS = {
    "peg": "Prijs/Winst gedeeld door Groei (5jr).\n🟢 < 1.5: Goedkoop.\n🔴 Negatief: Krimp/Verlies.",
    "fwd_pe": "Verwachte K/W komend jaar.\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Premie.",
    "roe": "Rendement op Eigen Vermogen.\n🟢 > 15%: Sterk management.",
    "de": "Schuldratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3.0: Redelijk.",
    "div": "Dividend Rendement.\n🟢 > 3%: Hoog inkomen.",
    "beta": "Volatiliteit.\n🟢 < 0.8: Stabiel.\n🔴 > 1.5: Wild.",
    "rsi": "Momentum.\n🟢 < 30: Koopkans?.",
    "margin": "Winstmarge.\n🟢 > 20%: Zeer winstgevend.\n🔴 < 0%: Verlieslatend."
}

# --- HULPFUNCTIES ---
@st.cache_data(ttl=3600)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD" and rate > 1.05: return 1/rate
            return rate
        return 0.95 if from_curr == "USD" else 1.0
    except: return 1.0

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=25):
    if text and len(text) > max_len: return text[:max_len] + "..."
    return text

# --- SCRAPERS ---

@st.cache_data(ttl=3600)
def scrape_yahoo_analysis(ticker):
    d = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        url = f"https://finance.yahoo.com/quote/{ticker}/analysis"
        r = requests.get(url, headers=headers, timeout=4)
        if r.status_code == 200:
            txt = r.text
            # Target Price
            m_val = re.search(r'Average Target.*?<td.*?>(.*?)</td>', txt, re.DOTALL)
            if m_val:
                val_str = re.sub(r'[^\d\.]', '', m_val.group(1))
                if val_str: d['target'] = float(val_str)
            
            # Forward PE soms hier
            m_fwd = re.search(r'Forward P/E.*?<td.*?>(.*?)</td>', txt, re.DOTALL)
            if m_fwd:
                 val_str = re.sub(r'[^\d\.]', '', m_fwd.group(1))
                 if val_str: d['fwd_pe'] = float(val_str)
    except: pass
    return d

@st.cache_data(ttl=3600)
def get_finviz_data(ticker):
    d = {}
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://finviz.com/quote.ashx?t={ticker}", headers=h, timeout=4)
        if r.status_code == 200:
            txt = r.text
            def p(lbl):
                m = re.search(rf'>{lbl}</td>.*?<b>\s*([\d\.]+)\s*</b>', txt, re.DOTALL)
                return float(m.group(1)) if m else None
            d['peg'] = p('PEG')
            d['pb'] = p('P/B')
            d['fwd_pe'] = p('Forward P/E')
            d['target'] = p('Target Price')
            d['eps'] = p('EPS \(ttm\)')
    except: pass
    return d

@st.cache_data(ttl=3600)
def scrape_google_finance(ticker):
    d = {}
    try:
        url = f"https://www.google.com/finance/quote/{ticker}:NASDAQ"
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=h, timeout=3)
        if r.status_code == 200:
            if "P/E ratio" in r.text:
                m = re.search(r'P/E ratio</div>.*?<div class=".*?">([\d\.]+)</div>', r.text, re.DOTALL)
                if m: d['pe'] = float(m.group(1))
    except: pass
    return d

def search_yahoo(query):
    """Zoekt met regio Duitsland (DE) om Xetra tickers te prioriteren"""
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        # REGION = DE zorgt dat Duitse tickers eerst komen
        r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=DE&quotesCount=10", headers=h, timeout=3)
        d = r.json()
        res = []
        if 'quotes' in d:
            qs = d['quotes']
            # Extra sortering voor zekerheid: .DE / .F bovenaan
            qs.sort(key=lambda x: 0 if x.get('symbol','').endswith(('.DE','.F', '.AS')) else 1)
            for q in qs: res.append(f"{q['symbol']} | {q.get('shortname','')}")
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
            try: d = datetime.strptime(i.find('pubDate').text[:16], '%a, %d %b %Y').strftime('%d %b')
            except: d = ""
            items.append({"title": i.find('title').text, "link": i.find('link').text, "date": d})
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

# --- SCORE BEREKENING ---
def calculate_ai_score(metrics, eps_val=None):
    score, count = 0, 0
    opps, risks = [], []
    
    margin = metrics.get('margin', 0) or 0
    
    rules = [
        (metrics.get('peg'), "PEG", lambda x: 0<x<1.5, lambda x: x<0 or x>3.0),
        (metrics.get('fwd_pe'), "Fwd P/E", lambda x: 0<x<20, lambda x: x>40),
        (metrics.get('roe'), "ROE", lambda x: x>0.15, lambda x: x<0.05),
        (metrics.get('de'), "D/E", lambda x: x<1.0, lambda x: x>2.5),
        (metrics.get('cr'), "Current", lambda x: x>1.5, lambda x: x<0.9),
        (metrics.get('pb'), "P/B", lambda x: 0<x<3.0, lambda x: x>10)
    ]
    
    for val, name, good, bad in rules:
        if val is not None:
            count += 1
            if good(val): 
                score += 1
                opps.append(f"{name} ({val:.2f})")
            elif bad(val):
                risks.append(f"{name} ({val:.2f})")
            else:
                score += 0.5
    
    final = (score/count)*10 if count > 0 else 5.0
    
    if margin < 0:
        final = min(final, 5.5)
        risks.insert(0, f"Verlieslatend ({margin:.1%})")
    
    if metrics.get('de') and metrics.get('de') > 4.0:
        final = min(final, 4.0)
        risks.insert(0, "Hoge schulden")

    return final, opps, risks

# --- DATA BEHEER ---
def handle_upload():
    uploaded_file = st.session_state.get('uploader')
    if uploaded_file:
        try:
            uploaded_file.seek(0)
            data = json.load(uploaded_file)
            if isinstance(data, dict):
                st.session_state['portfolio'] = data.get('portfolio', [])
                st.session_state['wishlist'] = data.get('wishlist', [])
            st.toast("✅ Data geladen!", icon="💾")
        except: st.error("Fout bestand.")

def get_json_data():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- SIDEBAR ---
st.sidebar.header("📂 Data")
st.sidebar.download_button(f"⬇️ Download", get_json_data(), "data.json", "application/json")
st.sidebar.file_uploader("⬆️ Upload", type=['json'], key='uploader', on_change=handle_upload)
st.sidebar.divider()
if st.sidebar.button("🧹 Cache Legen"): st.cache_data.clear(); st.rerun()

# --- TABS ---
tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist", "🇳🇱 Belasting"])

# ==================================================
# 1. ANALYSE
# ==================================================
with tabs[0]:
    c1, c2 = st.columns([3, 1])
    with c1:
        dq = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek:", value=dq, placeholder="Naam...", key="search_main")
        sel = None
        if txt:
            if "." in txt and " " not in txt: sel = txt
            else:
                res = search_yahoo(txt)
                if res: sel = st.selectbox("Resultaat:", res).split(" | ")[0]
                else: sel = txt.upper()
    with c2:
        per = st.selectbox("Periode", ["1mo", "6mo", "1y", "5y"], index=2)

    if sel:
        try:
            st.markdown("---")
            tick = yf.Ticker(sel)
            info = tick.info
            hist = tick.history(period=per)
            
            if hist.empty: st.error("Geen data.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price = hist['Close'].iloc[-1] * rate
                
                # SMART DATA
                mt = sel
                if sel in EU_TO_US_MAP: mt = EU_TO_US_MAP[sel]
                minfo = yf.Ticker(mt).info
                mfin = get_finviz_data(mt)
                mscr = scrape_yahoo_analysis(mt)
                mgoogle = scrape_google_finance(mt)

                # METRIC SELECTOR
                def get_m(y_k, f_k, s_k=None, g_k=None, calc=None):
                    # Prio 1: Scrapers (Yahoo Web / Finviz)
                    if s_k and mscr.get(s_k): return mscr[s_k], "Yahoo Web"
                    if f_k and mfin.get(f_k): return mfin[f_k], "Finviz"
                    if g_k and mgoogle.get(g_k): return mgoogle[g_k], "Google"
                    
                    # Prio 2: API
                    yv = safe_get(minfo, y_k)
                    if yv: return yv, "Yahoo API"
                    
                    # Prio 3: Calc
                    if calc:
                        c = calc()
                        if c is not None: return c, "Berekend"
                    return None, "N/A"

                # PEG
                def c_peg():
                    p = safe_get(minfo, 'trailingPE')
                    g = safe_get(minfo, 'earningsGrowth')
                    if p and g and p > 0 and g > 0: return p/(g*100)
                    return None
                peg, peg_s = get_m('pegRatio', 'peg', None, None, c_peg)

                # Forward P/E
                fwd, fwd_s = get_m('forwardPE', 'fwd_pe', 'fwd_pe', None)

                # Target
                tgt, tgt_s = get_m('targetMeanPrice', 'target', 'target') 
                if tgt: tgt *= get_currency_rate(safe_get(minfo, 'currency', 'USD'), "EUR")

                pb, pb_s = get_m('priceToBook', 'pb')
                de, de_s = get_m('debtToEquity', None)
                if de and de > 10: de/=100
                cr, cr_s = get_m('currentRatio', None)
                roe, roe_s = get_m('returnOnEquity', None)

                # Dividend
                dy, dy_s = get_m('dividendYield', None)
                if not dy:
                    dr = safe_get(minfo, 'dividendRate')
                    if dr:
                        try:
                            op = yf.Ticker(mt).history(period="1d")['Close'].iloc[-1]
                            dy = dr/op; dy_s = "Berekend"
                        except: pass
                if dy and dy > 0.5: dy /= 100

                marg = safe_get(minfo, 'profitMargins')
                eps = mfin.get('eps')

                sd = {'peg': peg, 'fwd_pe': fwd, 'roe': roe, 'de': de, 'cr': cr, 'pb': pb, 'margin': marg}
                score, opps, risks = calculate_ai_score(sd, eps)

                # UI
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1: 
                    st.title(truncate_text(safe_get(info, 'shortName', sel)))
                    st.caption(f"{sel} | Data: {mt}")
                with c2: st.metric("Prijs", f"€ {price:.2f}")
                with c3: st.metric("Doel", f"€ {tgt:.2f}" if tgt else "-", help=f"Bron: {tgt_s}")
                with c4:
                    with st.popover("➕ Toevoegen"):
                        with st.form("add"):
                            n = st.number_input("Aantal", 1.0)
                            p = st.number_input("GAK (€)", value=float(round(price, 2)))
                            if st.form_submit_button("Portfolio"):
                                st.session_state['portfolio'].append({'ticker': sel, 'amount': n, 'avg_price_eur': p})
                                st.rerun()
                        if st.button("Wishlist"):
                            st.session_state['wishlist'].append({'ticker': sel, 'name': safe_get(info, 'shortName')})
                            st.rerun()

                col = "green" if score>=7 else "orange" if score>=5.5 else "red"
                st.markdown(f"""<div style="display:flex; align-items:center; gap: 15px; margin: 10px 0;">
                    <div style="min-width:80px; height:80px; border-radius:50%; border:5px solid {col}; display:flex; align-items:center; justify-content:center;">
                        <span style="font-size:24px; font-weight:bold; color:{col}">{score:.1f}</span>
                    </div>
                    <div><small>✅ {', '.join(opps) if opps else '-'}<br>⚠️ {', '.join(risks) if risks else '-'}</small></div>
                </div>""", unsafe_allow_html=True)

                # METRICS DISPLAY (MET KLEURTJES)
                cols = st.columns(4)
                def dm(i, l, v, s, k, f="{:.2f}", cl=None):
                    ic = "⚪"
                    if v is not None and cl:
                        r = cl(v)
                        if r=="g": ic="🟢"
                        elif r=="o": ic="🟠"
                        elif r=="r": ic="🔴"
                        elif r=="b": ic="🔵"
                    with cols[i]: st.metric(l, f"{ic} {f.format(v)}" if v is not None else "N/A", help=f"Bron: {s}\n\n{TOOLTIPS.get(k,'')}")
                
                dm(0, "PEG", peg, peg_s, 'peg', cl=lambda x: "g" if 0<x<1.5 else ("o" if x<0 else "y"))
                dm(1, "Fwd P/E", fwd, fwd_s, 'fwd_pe', cl=lambda x: "g" if 0<x<20 else "y")
                dm(2, "ROE", roe, roe_s, 'roe', "{:.1%}", cl=lambda x: "g" if x>0.15 else "y")
                dm(3, "Dividend", dy, dy_s, 'div', "{:.2%}", cl=lambda x: "g" if x>0.03 else "b")
                
                cols2 = st.columns(4)
                def dm2(i, l, v, s, k, f="{:.2f}", cl=None):
                     ic = "⚪"
                     if v is not None and cl:
                        r = cl(v)
                        if r=="g": ic="🟢"
                        elif r=="o": ic="🟠"
                        elif r=="r": ic="🔴"
                        elif r=="b": ic="🔵"
                     with cols2[i]: st.metric(l, f"{ic} {f.format(v)}" if v is not None else "N/A", help=f"Bron: {s}\n\n{TOOLTIPS.get(k,'')}")
                
                dm2(0, "Current", cr, cr_s, 'cr', cl=lambda x: "g" if x>1.5 else "o")
                dm2(1, "D/E", de, de_s, 'de', cl=lambda x: "g" if x<1 else "o")
                dm2(2, "P/B", pb, pb_s, 'pb', cl=lambda x: "g" if 0<x<3 else "y")
                dm2(3, "Marge", marg, "Yahoo", 'margin', "{:.1%}", cl=lambda x: "g" if x>0.2 else "o")

                st.plotly_chart(px.line(x=hist.index, y=hist['Close']), use_container_width=True)
                
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
        divcal = []
        sec_dist = {}
        
        bar = st.progress(0)
        items = st.session_state['portfolio']
        for i, item in enumerate(items):
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
                sec_dist[s] = sec_dist.get(s, 0) + v

                dr = safe_get(tk.info, 'dividendRate')
                if dr: 
                    dye = dr * r * item['amount']
                    ex = safe_get(tk.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m') if ex else "-"
                    divcal.append({"Aandeel": t, "Ex": exs, "Jaar (€)": dye})

                rows.append({"Naam": t, "Aantal": item['amount'], "Prijs": f"€{cur:.2f}", "Waarde": f"€{v:.2f}", "Winst": f"€{v-c:.2f}"})
            except: pass
            bar.progress((i+1)/len(items))
        bar.empty()
        
        k1, k2 = st.columns(2)
        k1.metric("Waarde", f"€ {tv:,.2f}")
        k2.metric("Winst", f"€ {tv-ti:,.2f}", delta=f"{((tv-ti)/ti)*100:.1f}%" if ti else "0%")
        
        c1, c2 = st.columns(2)
        with c1:
            if sec_dist and tv > 0: st.plotly_chart(px.pie(values=list(sec_dist.values()), names=list(sec_dist.keys()), hole=0.4), use_container_width=True)
        with c2:
            if divcal: st.dataframe(pd.DataFrame(divcal), hide_index=True)
        
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        
        if st.button("Alles Wissen"): st.session_state['portfolio'] = []; st.rerun()
        
        with st.expander("🧮 Calculator"):
            c1, c2 = st.columns(2)
            m = c1.number_input("Mnd Inleg", 500)
            j = c2.number_input("Jaren", 20)
            r = 7.0
            end = tv * ((1+r/100)**j) + (m * 12 * (((1+r/100)**j - 1) / (r/100)))
            st.metric("Eindbedrag (7%)", f"€ {end:,.0f}")
    else: st.info("Leeg.")

# ==================================================
# 3. WISHLIST (1 REGEL FIX)
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
                    
                    # Score Lite
                    wi = tk.info
                    pe = safe_get(wi, 'trailingPE')
                    g = safe_get(wi, 'earningsGrowth')
                    pg = pe/(g*100) if pe and g else None
                    sd = {'peg': pg, 'roe': safe_get(wi, 'returnOnEquity')}
                    sc, _, _ = calculate_ai_score(sd)
                    scol = "green" if sc>=7 else "orange" if sc>=5.5 else "red"
                    
                    st.markdown(f"""
                    <div style="border-bottom:1px solid #444; padding:8px; display:flex; align-items:center; justify-content:space-between;">
                        <div style="flex:2;"><b>{item['name']}</b><br><span style='color:#888;font-size:0.8em'>{t}</span></div>
                        <div style="flex:1;text-align:right;">€{cp:.2f}<br><span style='color:{col};font-size:0.9em'>{ch:+.1f}%</span></div>
                        <div style="flex:1;text-align:right;padding-left:8px;"><b style='color:{scol}'>{sc:.1f}</b> AI</div>
                    </div>
                    """, unsafe_allow_html=True)
                    
            except: pass
        if st.button("Legen", key="wl_clr"): st.session_state['wishlist'] = []; st.rerun()
    else: st.info("Leeg.")

# ==================================================
# 4. BELASTING (NIEUW)
# ==================================================
with tabs[3]:
    st.header("🇳🇱 Box 3 Check")
    c1, c2 = st.columns(2)
    with c1:
        yr = st.selectbox("Jaar", [2023, 2024, 2025])
        defs = TAX_DEFAULTS.get(yr, {"invest": 6.04, "tax": 36})
        fr = st.number_input("Fictief %", value=defs["invest"], step=0.01)
        tr = st.number_input("Tax %", value=float(defs["tax"]), step=0.1)
    with c2:
        sv = st.number_input("Waarde 1 jan", 0.0, step=100.0)
        ev = st.number_input("Waarde 31 dec", 0.0, step=100.0)
        dep = st.number_input("Netto Inleg", 0.0, step=100.0)
    
    real = (ev - sv) - dep
    taxable = (sv * fr) / 100
    due = (taxable * tr) / 100
    
    st.divider()
    c3, c4 = st.columns(2)
    with c3:
        col = "green" if real > taxable else "red"
        st.metric("Werkelijk", f"€ {real:,.2f}")
    with c4: st.metric("Belasting", f"€ {due:,.2f}")
    
    if real > taxable: st.success("Gewonnen van de fiscus!")
    else: st.warning("Fictief > Werkelijk")
