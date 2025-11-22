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

# --- CONSTANTEN ---
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

TAX_DEFAULTS = {
    2023: {"invest": 6.17, "tax": 32},
    2024: {"invest": 6.04, "tax": 36},
    2025: {"invest": 5.88, "tax": 36}
}

TOOLTIPS = {
    "peg": "Prijs/Winst / Groei.\n🟢 < 1.5: Goedkoop.\n🔴 < 0: Krimp.\n🟠 > 2.5: Duur.",
    "fwd_pe": "Verwachte K/W (1jr).\n🟢 < 20: Goedkoop.\n🟠 > 30: Premie.",
    "roe": "Rendement Eigen Vermogen.\n🟢 > 15%: Sterk.",
    "de": "Schuldratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schuld.",
    "cr": "Current Ratio.\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3: Redelijk.",
    "div": "Dividend %.\n🟢 > 3%: Hoog.",
    "beta": "Volatiliteit.\n🟢 < 0.8: Stabiel.\n🔴 > 1.5: Wild.",
    "rsi": "Momentum.\n🟢 < 30: Koopkans?.\n🔴 > 70: Te duur?.",
    "margin": "Winstmarge.\n🟢 > 20%: Top.\n🔴 < 0%: Verlies."
}

# --- MAPPING (EU -> US) ---
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD"
}

# --- HULPFUNCTIES ---

@st.cache_data(ttl=300)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            # Sanity Check: USD is ong 0.95 EUR. Als > 1.05 is het wss andersom
            if from_curr == "USD" and rate > 1.05: return 1/rate
            return rate
        return 0.95 if from_curr == "USD" else 1.0
    except: return 1.0

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=22):
    if text and len(text) > max_len: return text[:max_len] + ".."
    return text

# --- SCRAPERS (REAL-TIME) ---

@st.cache_data(ttl=600)
def scrape_yahoo_analysis(ticker):
    """Haalt actuele koersdoelen en groei van Yahoo Analysis"""
    d = {}
    try:
        r = requests.get(f"https://finance.yahoo.com/quote/{ticker}/analysis", headers=HEADERS, timeout=4)
        if r.status_code == 200:
            # Target
            m = re.search(r'Average Target.*?<td.*?>(.*?)</td>', r.text, re.DOTALL)
            if m: d['target'] = float(re.sub(r'[^\d\.]', '', m.group(1)))
            # Growth
            m2 = re.search(r'Next 5 Years \(per annum\).*?<td.*?>(.*?)%</td>', r.text, re.DOTALL)
            if m2: d['growth_5y'] = float(m2.group(1))
    except: pass
    return d

@st.cache_data(ttl=600)
def scrape_yahoo_stats(ticker):
    """Haalt metrics van Yahoo Key Statistics"""
    d = {}
    try:
        r = requests.get(f"https://finance.yahoo.com/quote/{ticker}/key-statistics", headers=HEADERS, timeout=4)
        if r.status_code == 200:
            def p(pat):
                m = re.search(pat, r.text, re.DOTALL)
                return float(re.sub(r'[^\d\.-]', '', m.group(1))) if m else None
            
            d['peg'] = p(r'PEG Ratio \(5 yr expected\).*?<td.*?>(.*?)</td>')
            d['fwd_pe'] = p(r'Forward P/E.*?<td.*?>(.*?)</td>')
            d['pb'] = p(r'Price/Book.*?<td.*?>(.*?)</td>')
    except: pass
    return d

@st.cache_data(ttl=600)
def get_finviz_data(ticker):
    """Finviz scraper (US Stocks)"""
    d = {}
    try:
        r = requests.get(f"https://finviz.com/quote.ashx?t={ticker}", headers=HEADERS, timeout=4)
        if r.status_code == 200:
            def p(l):
                m = re.search(rf'>{l}</td>.*?<b>\s*([\d\.-]+)\s*</b>', r.text, re.DOTALL)
                return float(m.group(1)) if m and m.group(1)!='-' else None
            d['peg'] = p('PEG')
            d['fwd_pe'] = p('Forward P/E')
            d['pb'] = p('P/B')
            d['target'] = p('Target Price')
            d['eps'] = p('EPS \(ttm\)')
    except: pass
    return d

def search_yahoo(query):
    """Zoekt met prioriteit voor EU tickers"""
    try:
        r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10", headers=HEADERS, timeout=3)
        d = r.json()
        res = []
        if 'quotes' in d:
            qs = d['quotes']
            # Sorteer: .DE/.AS/.F bovenaan (score 0), rest score 1
            qs.sort(key=lambda x: 0 if x.get('symbol','').endswith(('.DE','.AS','.F','.CO')) else 1)
            for q in qs: res.append(f"{q['symbol']} | {q.get('shortname','')}")
        return res
    except: return []

@st.cache_data(ttl=3600)
def find_primary_ticker(name):
    """Zoekt US ticker op basis van naam"""
    try:
        base = re.split(r' (Inc|Corp|Group|AG|SE|PLC|NV|B\.V\.)', name, flags=re.IGNORECASE)[0].strip()
        r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={base}&lang=en-US&region=US&quotesCount=5", headers=HEADERS, timeout=4)
        d = r.json()
        for q in d.get('quotes', []):
            s = q['symbol']
            if "." not in s and len(s) <= 5: return s
    except: pass
    return None

def get_google_news(query):
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={query}+stock&hl=en-US&gl=US&ceid=US:en", timeout=5)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:4]:
            dt = i.find('pubDate').text[:16]
            items.append({"title": i.find('title').text, "link": i.find('link').text, "date": dt})
        return items
    except: return []

@st.cache_data(ttl=3600)
def get_youtube_id(query):
    try:
        url = f"https://www.youtube.com/results?search_query={query.replace(' ','+')}+stock+analysis+{datetime.now().year}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except: return None

# --- AI SCORE ---
def calculate_ai_score(metrics, eps=None):
    score, count = 0, 0
    opps, risks = [], []
    
    # Kill Switch: Verlieslatend
    margin = metrics.get('margin', 0) or 0
    if (eps and eps < 0) or margin < 0:
        return 4.0, [], ["Verlieslatend bedrijf (Negatieve Winst/Marge)"]

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
            if good(val): score += 1; opps.append(f"{name} ({val:.2f})")
            elif bad(val): risks.append(f"{name} ({val:.2f})")
            else: score += 0.5
            
    final = (score/count)*10 if count > 0 else 5.0
    
    # Schuld Straf
    if metrics.get('de') and metrics.get('de') > 4: 
        final = min(final, 3.5); risks.insert(0, "Kritiek schuldniveau")

    return round(final, 1), opps, risks

# --- DATA BEHEER ---
def handle_upload():
    f = st.session_state.get('uploader')
    if f:
        try:
            f.seek(0)
            d = json.load(f)
            if isinstance(d, dict):
                st.session_state['portfolio'] = d.get('portfolio', [])
                st.session_state['wishlist'] = d.get('wishlist', [])
            st.toast("✅ Data geladen!", icon="💾")
        except: st.error("Fout bestand.")

def get_json():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- SIDEBAR ---
st.sidebar.header("📂 Data")
st.sidebar.download_button(f"⬇️ Download ({len(st.session_state['portfolio'])})", get_json(), "data.json", "application/json")
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
                
                # SMART DATA FETCH
                mt = sel
                if sel in EU_TO_US_MAP: mt = EU_TO_US_MAP[sel]
                elif any(safe_get(info, k) is None for k in ['forwardPE', 'pegRatio']):
                    sn = safe_get(info, 'shortName', '')
                    us_t = find_primary_ticker(sn)
                    if us_t and us_t != sel: mt = us_t

                # Haal data op
                minfo = yf.Ticker(mt).info
                mfin = get_finviz_data(mt)
                mscr = scrape_yahoo_stats(mt)
                manal = scrape_yahoo_analysis(mt)

                # Metric Kiezer (Prio: Scraper -> Finviz -> API -> Calc)
                def get_m(y_k, f_k, s_k, calc=None):
                    if s_k and mscr.get(s_k): return mscr[s_k], "Yahoo Web"
                    if f_k and mfin.get(f_k): return mfin[f_k], "Finviz"
                    if safe_get(minfo, y_k) is not None: return minfo[y_k], "Yahoo API"
                    if calc:
                        c = calc()
                        if c: return c, "Berekend"
                    return None, "N/A"

                # 1. PEG
                def c_peg():
                    p = safe_get(minfo, 'trailingPE')
                    g = safe_get(minfo, 'earningsGrowth')
                    return p/(g*100) if p and g and g>0 else None
                peg, peg_s = get_m('pegRatio', 'peg', 'peg', c_peg)

                # 2. Fwd PE
                fwd, fwd_s = get_m('forwardPE', 'fwd_pe', 'fwd_pe', None)

                # 3. Target
                tgt = manal.get('target')
                tgt_s = "Yahoo Analysis"
                if not tgt: tgt, tgt_s = get_m('targetMeanPrice', 'target', None)
                
                if tgt:
                    tgt_c = safe_get(minfo, 'currency', 'USD')
                    tgt *= get_currency_rate(tgt_c, "EUR")
                    upside = f"{((tgt-price)/price)*100:+.1f}%"
                else: upside = ""

                pb, pb_s = get_m('priceToBook', 'pb', 'pb')
                de, de_s = get_m('debtToEquity', None, None)
                if de and de > 10: de/=100
                cr, cr_s = get_m('currentRatio', None, None)
                roe, roe_s = get_m('returnOnEquity', None, None)
                
                dy, dy_s = get_m('dividendYield', None, None)
                if not dy:
                    dr = safe_get(minfo, 'dividendRate')
                    if dr:
                        try:
                            op = yf.Ticker(mt).history(period="1d")['Close'].iloc[-1]
                            dy = dr/op; dy_s = "Berekend"
                        except: pass
                if dy and dy > 0.5: dy/=100

                beta = safe_get(minfo, 'beta')
                marg = safe_get(minfo, 'profitMargins')
                
                # RSI
                d = hist['Close'].diff()
                g = d.where(d>0, 0).rolling(14).mean()
                l = -d.where(d<0, 0).rolling(14).mean()
                rs = g/l
                rsi = 100 - (100/(1+rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50

                eps = mfin.get('eps')
                sd = {'peg': peg, 'fwd_pe': fwd, 'roe': roe, 'de': de, 'cr': cr, 'pb': pb, 'margin': marg}
                score, opps, risks = calculate_ai_score(sd, eps)

                # UI
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1: 
                    st.title(truncate_text(safe_get(info, 'shortName', sel)))
                    st.caption(f"{sel} | Data: {mt}")
                with c2: st.metric("Prijs", f"€ {price:.2f}")
                with c3: st.metric("Doel", f"€ {tgt:.2f}" if tgt else "-", delta=upside, help=f"Bron: {tgt_s}")
                with c4:
                    with st.popover("➕ Toevoegen"):
                        with st.form("add"):
                            n = st.number_input("Aantal", 1.0)
                            p = st.number_input("GAK", value=float(round(price, 2)))
                            if st.form_submit_button("Portfolio"):
                                st.session_state['portfolio'].append({'ticker': sel, 'amount': n, 'avg_price_eur': p})
                                st.rerun()
                        if st.button("Wishlist"):
                            st.session_state['wishlist'].append({'ticker': sel, 'name': safe_get(info, 'shortName')})
                            st.rerun()

                col = "green" if score>=7 else "orange" if score>=5.5 else "red"
                st.markdown(f"""
                <div style="display:flex; align-items:center; gap: 15px; margin: 10px 0;">
                    <div style="min-width:80px; height:80px; border-radius:50%; border:5px solid {col}; display:flex; align-items:center; justify-content:center;">
                        <span style="font-size:24px; font-weight:bold; color:{col}">{score:.1f}</span>
                    </div>
                    <div><small>✅ {', '.join(opps) if opps else '-'}<br>⚠️ {', '.join(risks) if risks else '-'}</small></div>
                </div>""", unsafe_allow_html=True)

                cols = st.columns(4)
                def dm(i, l, v, s, k, f="{:.2f}"):
                    c = "⚪"
                    if v:
                        if l=="PEG": c="🟢" if 0<v<1.5 else ("🟠" if v<0 or v>2.5 else "🟡")
                        if l=="Fwd P/E": c="🟢" if 0<v<20 else "🟠"
                        if l=="ROE": c="🟢" if v>0.15 else "🟠"
                        if l=="Current": c="🟢" if v>1.5 else "🟠"
                        if l=="D/E": c="🟢" if v<1 else "🟠"
                        if l=="Dividend": c="🟢" if v>0.03 else "🔵"
                        if l=="RSI": c="🟢" if v<30 else "⚪"
                        if l=="Marge": c="🟢" if v>0.2 else "🟠"
                    with cols[i]: st.metric(l, f"{c} {f.format(v)}" if v else "-", help=f"Bron: {s}\n{TOOLTIPS.get(k,'')}")
                
                dm(0, "PEG", peg, peg_s, 'peg')
                dm(1, "Fwd P/E", fwd, fwd_s, 'fwd_pe')
                dm(2, "ROE", roe, roe_s, 'roe', "{:.1%}")
                dm(3, "Dividend", dy, dy_s, 'div', "{:.2%}")
                
                cols2 = st.columns(4)
                def dm2(i, l, v, s, k, f="{:.2f}"):
                    c = "⚪" # Herhaal logic of maak generiek (hier versimpeld)
                    with cols2[i]: st.metric(l, f.format(v) if v else "-", help=f"Bron: {s}")

                with cols2[0]: st.metric("Current", f"{cr:.2f}" if cr else "-", help=f"Bron: {cr_s}")
                with cols2[1]: st.metric("D/E", f"{de:.2f}" if de else "-", help=f"Bron: {de_s}")
                with cols2[2]: st.metric("P/B", f"{pb:.2f}" if pb else "-", help=f"Bron: {pb_s}")
                with cols2[3]: st.metric("Marge", f"{marg:.1%}" if marg else "-")

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
        sd = {}
        dc = []
        
        for item in st.session_state['portfolio']:
            try:
                t = item['ticker']
                tick = yf.Ticker(t)
                h = tick.history(period="1d")
                if not h.empty:
                    r = get_currency_rate(safe_get(tick.info, 'currency', 'EUR'), "EUR")
                    cur = h['Close'].iloc[-1] * r
                else: cur = item['avg_price_eur']
                
                v = cur * item['amount']
                c = item['avg_price_eur'] * item['amount']
                tv += v
                ti += c
                
                s = safe_get(tick.info, 'sector', 'Overig')
                sd[s] = sd.get(s, 0) + v

                dr = safe_get(tick.info, 'dividendRate')
                if dr:
                    dye = dr * r * item['amount']
                    ex = safe_get(tick.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m') if ex else "-"
                    dc.append({"Aandeel": t, "Datum": exs, "Bedrag": dye})

                rows.append({"Naam": t, "Aantal": item['amount'], "Prijs": f"€{cur:.2f}", "Waarde": f"€{v:.2f}", "Winst": f"€{v-c:.2f}"})
            except: pass
        
        k1, k2 = st.columns(2)
        k1.metric("Waarde", f"€ {tv:,.2f}")
        k2.metric("Winst", f"€ {tv-ti:,.2f}", delta=f"{((tv-ti)/ti)*100:.1f}%" if ti else "0%")
        
        c1, c2 = st.columns(2)
        with c1:
            if sd and tv > 0: st.plotly_chart(px.pie(values=list(sd.values()), names=list(sd.keys()), hole=0.4), use_container_width=True)
        with c2:
            if dc:
                dfd = pd.DataFrame(dc)
                st.metric("Maandelijks", f"€ {dfd['Bedrag'].sum()/12:.2f}")
                st.dataframe(dfd, hide_index=True)
        
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        
        if st.button("Wissen"): st.session_state['portfolio'] = []; st.rerun()
        
        with st.expander("🧮 Calculator"):
            c1, c2 = st.columns(2)
            m = c1.number_input("Mnd Inleg", 500)
            j = c2.number_input("Jaren", 20)
            r = 7.0
            end = tv * ((1+r/100)**j) + (m * 12 * (((1+r/100)**j - 1) / (r/100)))
            st.metric("Eindbedrag", f"€ {end:,.0f}")
    else: st.info("Leeg.")

# ==================================================
# 3. WISHLIST (COMPACT)
# ==================================================
with tabs[2]:
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            t = item['ticker']
            try:
                tick = yf.Ticker(t)
                h = tick.history(period="2d")
                if not h.empty:
                    r = get_currency_rate(safe_get(tick.info, 'currency', 'EUR'), "EUR")
                    cp = h['Close'].iloc[-1] * r
                    ch = ((cp - h['Close'].iloc[0] * r) / (h['Close'].iloc[0] * r)) * 100
                    col = "green" if ch >= 0 else "red"
                    
                    st.markdown(f"""
                    <div style="border-bottom:1px solid #444; padding:8px; display:flex; justify-content:space-between; align-items:center;">
                        <div><b>{item['name']}</b><br><span style='color:gray;font-size:0.8em'>{t}</span></div>
                        <div style="text-align:right;"><b>€{cp:.2f}</b><br><span style='color:{col};font-size:0.9em'>{ch:+.1f}%</span></div>
                    </div>""", unsafe_allow_html=True)
                    
                    if st.button("🔎", key=f"wl_{t}"):
                        st.session_state['search_query'] = t
                        st.rerun()
            except: pass
        if st.button("Legen", key="wl_clr"): st.session_state['wishlist'] = []; st.rerun()
    else: st.info("Leeg.")

# ==================================================
# 4. BELASTING
# ==================================================
with tabs[3]:
    st.header("Box 3")
    yr = st.selectbox("Jaar", [2023, 2024, 2025])
    d = TAX_DEFAULTS.get(yr, {"invest": 6.04, "tax": 36})
    
    c1, c2 = st.columns(2)
    with c1:
        fr = st.number_input("Fictief %", value=d["invest"], step=0.01)
        tr = st.number_input("Tax %", value=float(d["tax"]), step=0.1)
    with c2:
        sv = st.number_input("Start (1 jan)", 0.0)
        ev = st.number_input("Eind (31 dec)", 0.0)
        inl = st.number_input("Netto Inleg", 0.0)
        div = st.number_input("Dividend", 0.0)
    
    real = (ev - sv) - inl + div
    tax = (sv * fr / 100) * (tr / 100)
    
    c3, c4 = st.columns(2)
    with c3: st.metric("Werkelijk", f"€ {real:.2f}")
    with c4: st.metric("Belasting", f"€ {tax:.2f}")
