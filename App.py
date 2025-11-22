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
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "IMCD.AS": "IMCDY"
}

# --- CONSTANTEN ---
TAX_DEFAULTS = {
    2023: {"invest": 6.17, "tax": 32},
    2024: {"invest": 6.04, "tax": 36},
    2025: {"invest": 5.88, "tax": 36}
}

TOOLTIPS = {
    "peg": "Prijs/Winst gedeeld door Groei (5jr).\n🟢 < 1.5: Goedkoop.\n🔴 Negatief: Krimp.",
    "fwd_pe": "Verwachte K/W komend jaar.\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Premie.",
    "roe": "Rendement op Eigen Vermogen.\n🟢 > 15%: Sterk management.",
    "de": "Schuldratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3.0: Redelijk.",
    "div": "Dividend Rendement.\n🟢 > 3%: Hoog inkomen.",
    "beta": "Volatiliteit.\n🟢 < 0.8: Stabiel.\n🔴 > 1.5: Wild.",
    "rsi": "Momentum.\n🟢 < 30: Koopkans?.",
    "margin": "Winstmarge.\n🟢 > 20%: Zeer winstgevend."
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
    if text and len(text) > max_len: return text[:max_len] + ".."
    return text

# --- SCRAPERS ---
@st.cache_data(ttl=3600)
def scrape_yahoo_stats(ticker):
    d = {}
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://finance.yahoo.com/quote/{ticker}/key-statistics", headers=h, timeout=4)
        if r.status_code == 200:
            txt = r.text
            m = re.search(r'Forward P/E.*?<td.*?>(.*?)</td>', txt, re.DOTALL)
            if m: d['fwd_pe'] = float(re.sub(r'[^\d\.]', '', m.group(1)))
            m = re.search(r'PEG Ratio \(5 yr expected\).*?<td.*?>(.*?)</td>', txt, re.DOTALL)
            if m: d['peg'] = float(re.sub(r'[^\d\.]', '', m.group(1)))
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
    except: pass
    return d

def get_best_metric(metric_key, yahoo_info, finviz_data, yahoo_scrape, calc_func=None):
    fv_map = {'pegRatio': 'peg', 'forwardPE': 'fwd_pe', 'priceToBook': 'pb', 'targetMeanPrice': 'target'}
    ys_map = {'pegRatio': 'peg', 'forwardPE': 'fwd_pe'}
    
    # 1. Finviz
    if fv_map.get(metric_key) and finviz_data.get(fv_map[metric_key]): 
        return finviz_data[fv_map[metric_key]], "Finviz"
    # 2. Yahoo Web
    if ys_map.get(metric_key) and yahoo_scrape.get(ys_map[metric_key]): 
        return yahoo_scrape[ys_map[metric_key]], "Yahoo Web"
    # 3. Yahoo API
    y_val = safe_get(yahoo_info, metric_key)
    if y_val: return y_val, "Yahoo API"
    # 4. Calc
    if calc_func:
        try:
            res = calc_func()
            if res: return res, "Berekend"
        except: pass
    return None, "N/A"

def search_yahoo(query):
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10", headers=h, timeout=3)
        d = r.json()
        res = []
        if 'quotes' in d:
            qs = d['quotes']
            qs.sort(key=lambda x: 0 if x.get('symbol','').endswith(('.AS','.DE','.F')) else 1)
            for q in qs: res.append(f"{q['symbol']} | {q.get('shortname','')}")
        return res
    except: return []

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

def calculate_ai_score(metrics):
    score, count = 0, 0
    opps, risks = [], []
    rules = [
        (metrics.get('peg'), "PEG", lambda x: 0<x<1.5, lambda x: x<0 or x>2.5),
        (metrics.get('fwd_pe'), "Fwd P/E", lambda x: 0<x<20, lambda x: x>40),
        (metrics.get('roe'), "ROE", lambda x: x>0.15, lambda x: x<0.05),
        (metrics.get('de'), "D/E", lambda x: x<1.0, lambda x: x>2.0),
        (metrics.get('cr'), "Current", lambda x: x>1.5, lambda x: x<1.0),
        (metrics.get('pb'), "P/B", lambda x: 0<x<3.0, lambda x: x>10)
    ]
    for val, name, good, bad in rules:
        if val is not None:
            count += 1
            if good(val): score += 1; opps.append(f"{name} ({val:.2f})")
            elif bad(val): risks.append(f"{name} ({val:.2f})")
            else: score += 0.5
    final = (score/count)*10 if count > 0 else 5.0
    return final, opps, risks

# --- DATA BEHEER ---
def handle_upload():
    uploaded_file = st.session_state.get('uploader')
    if uploaded_file:
        try:
            uploaded_file.seek(0)
            d = json.load(uploaded_file)
            if isinstance(d, dict):
                st.session_state['portfolio'] = d.get('portfolio', [])
                st.session_state['wishlist'] = d.get('wishlist', [])
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
                mscr = scrape_yahoo_stats(mt)

                def c_peg():
                    p = safe_get(minfo, 'trailingPE')
                    g = safe_get(minfo, 'earningsGrowth')
                    return p/(g*100) if p and g else None
                
                peg, peg_s = get_best_metric('pegRatio', minfo, mfin, mscr, c_peg)
                fwd, fwd_s = get_best_metric('forwardPE', minfo, mfin, mscr)
                pb, pb_s   = get_best_metric('priceToBook', minfo, mfin, mscr)
                tgt, tgt_s = get_best_metric('targetMeanPrice', minfo, mfin, mscr)
                if tgt: tgt *= get_currency_rate(safe_get(minfo, 'currency', 'USD'), "EUR")

                de, de_s = get_best_metric('debtToEquity', minfo, {}, {})
                if de and de > 10: de/=100
                roe, roe_s = get_best_metric('returnOnEquity', minfo, {}, {})
                cr, cr_s = get_best_metric('currentRatio', minfo, {}, {})
                
                dy, dy_s = get_best_metric('dividendYield', minfo, {}, {})
                if not dy:
                    dr = safe_get(minfo, 'dividendRate')
                    if dr:
                        try:
                            op = yf.Ticker(mt).history(period="1d")['Close'].iloc[-1]
                            dy = dr/op; dy_s = "Berekend"
                        except: pass
                if dy and dy > 0.5: dy /= 100

                beta = safe_get(minfo, 'beta')
                rsi_val = 50 # Default
                if len(hist) > 14:
                    d = hist['Close'].diff()
                    g = d.where(d>0, 0).rolling(14).mean()
                    l = -d.where(d<0, 0).rolling(14).mean()
                    rs = g/l
                    rsi_val = (100 - (100/(1+rs))).iloc[-1]

                sd = {'peg': peg, 'fwd_pe': fwd, 'roe': roe, 'de': de, 'cr': cr, 'pb': pb}
                score, opps, risks = calculate_ai_score(sd)

                # HEADER
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1: 
                    st.title(truncate_text(safe_get(info, 'shortName', sel)))
                    st.caption(f"{sel} (Data via {mt})")
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

                # SCORE
                col = "green" if score>=7 else "orange" if score>=5.5 else "red"
                st.markdown(f"""
                <div style="display:flex; align-items:center; gap: 20px; margin: 20px 0;">
                    <div style="min-width:80px; height:80px; border-radius:50%; border:5px solid {col}; display:flex; align-items:center; justify-content:center;">
                        <span style="font-size:24px; font-weight:bold; color:{col}">{score:.1f}</span>
                    </div>
                    <div>
                        <span style="color:green">✅ {', '.join(opps) if opps else 'Geen uitschieters'}</span><br>
                        <span style="color:red">⚠️ {', '.join(risks) if risks else 'Geen grote risico\'s'}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # METRICS MET KLEUREN
                cols = st.columns(5)
                
                def dm(i, l, v, s, k, f="{:.2f}", cl=None):
                    if v is None: 
                        with cols[i]: st.metric(l, "N/A", help=f"Bron: {s}")
                        return
                    
                    icon = "⚪"
                    if cl:
                        r = cl(v)
                        if r == "g": icon = "🟢"
                        elif r == "o": icon = "🟠"
                        elif r == "r": icon = "🔴"
                        elif r == "b": icon = "🔵"
                    
                    with cols[i]:
                        st.metric(l, f"{icon} {f.format(v)}", help=f"Bron: {s}\n\n{TOOLTIPS.get(k, '')}")

                dm(0, "PEG", peg, peg_s, 'peg', cl=lambda x: "g" if 0<x<1.5 else ("o" if x<0 else "y"))
                dm(1, "Fwd P/E", fwd, fwd_s, 'fwd_pe', cl=lambda x: "g" if 0<x<20 else "y")
                dm(2, "ROE", roe, roe_s, 'roe', "{:.1%}", cl=lambda x: "g" if x>0.15 else "y")
                dm(3, "D/E", de, de_s, 'de', cl=lambda x: "g" if x<1 else "o")
                dm(4, "Current", cr, cr_s, 'cr', cl=lambda x: "g" if x>1.5 else "o")
                
                # 2e Rij
                cols = st.columns(5)
                dm(0, "P/B", pb, pb_s, 'pb', cl=lambda x: "g" if 0<x<3 else "y")
                dm(1, "Dividend", dy, dy_s, 'div', "{:.2%}", cl=lambda x: "g" if x>0.03 else "b")
                dm(2, "RSI", rsi_val, "Yahoo", 'rsi', "{:.0f}", cl=lambda x: "g" if x<30 else "y")
                dm(3, "Beta", beta, "Yahoo", 'beta', "{:.2f}", cl=lambda x: "g" if x<0.8 else "o")
                
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
        sec_dist = {}
        div_cal = []
        tv, ti = 0, 0
        
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
                
                # Sector
                s = safe_get(tk.info, 'sector', 'Overig')
                sec_dist[s] = sec_dist.get(s, 0) + v
                
                # Dividend
                dr = safe_get(tk.info, 'dividendRate')
                if dr:
                    # Rate is in local currency
                    dy_e = dr * r * item['amount']
                    ex = safe_get(tk.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m') if ex else "-"
                    div_cal.append({"Aandeel": t, "Datum": exs, "Bedrag": dy_e})

                rows.append({"Naam": t, "Aantal": item['amount'], "Prijs": f"€{cur:.2f}", "Waarde": f"€{v:.2f}", "Winst": f"€{v-c:.2f}"})
            except: pass
            bar.progress((i+1)/len(st.session_state['portfolio']))
        bar.empty()
        
        k1, k2, k3 = st.columns(3)
        k1.metric("Waarde", f"€ {tv:,.2f}")
        k2.metric("Winst", f"€ {tv-ti:,.2f}", delta=f"{((tv-ti)/ti)*100:.1f}%" if ti else "0%")
        k3.metric("Inleg", f"€ {ti:,.2f}")
        
        st.divider()
        
        cp, cd = st.columns(2)
        with cp:
            st.subheader("Verdeling")
            if sec_dist and tv > 0:
                fig = px.pie(values=list(sec_dist.values()), names=list(sec_dist.keys()), hole=0.4)
                fig.update_layout(margin=dict(t=0,b=0,l=0,r=0), height=200)
                st.plotly_chart(fig, use_container_width=True)
        with cd:
            st.subheader("Dividend")
            if div_cal:
                dfd = pd.DataFrame(div_cal)
                st.metric("Maandelijks", f"€ {dfd['Bedrag'].sum()/12:.2f}")
                st.dataframe(dfd, hide_index=True, use_container_width=True)
            else: st.info("Geen dividend.")

        st.subheader("Details")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        
        if st.button("Alles Wissen"): st.session_state['portfolio'] = []; st.rerun()
        
        st.divider()
        with st.expander("🧮 Calculator"):
            c1, c2, c3 = st.columns(3)
            m = c1.number_input("Mnd Inleg", 500)
            j = c2.number_input("Jaren", 20)
            r = c3.number_input("Rendement %", 7.0)
            end = tv * ((1+r/100)**j) + (m * 12 * (((1+r/100)**j - 1) / (r/100)))
            st.metric("Eindbedrag", f"€ {end:,.0f}")
    else: st.info("Leeg.")

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
                    
                    # Score Lite
                    pe = safe_get(tk.info, 'trailingPE')
                    g = safe_get(tk.info, 'earningsGrowth')
                    pg = pe/(g*100) if pe and g else None
                    sd = {'peg': pg, 'roe': safe_get(tk.info, 'returnOnEquity')}
                    sc, _, _ = calculate_ai_score(sd)
                    sc_col = "green" if sc>=7 else "orange" if sc>=5.5 else "red"
                    
                    st.markdown(f"""
                    <div style="border:1px solid #333; border-radius:10px; padding:10px; margin-bottom:10px; background-color:#262730;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div style="flex:2;"><b>{item['name']}</b><br><span style='color:gray;font-size:0.8em'>{t}</span></div>
                            <div style="flex:1; text-align:right;"><b>€{cp:.2f}</b> <span style='color:{col}'>{ch:+.1f}%</span></div>
                            <div style="flex:1; text-align:right;"><b style='color:{sc_col}'>{sc:.1f}</b> AI</div>
                        </div>
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
    st.header("🇳🇱 Box 3")
    c1, c2 = st.columns(2)
    with c1:
        yr = st.selectbox("Jaar", [2023, 2024, 2025])
        defs = TAX_DEFAULTS.get(yr, {"invest": 6.04, "tax": 36})
        fr = st.number_input("Fictief %", value=defs["invest"], step=0.01)
        tr = st.number_input("Tax %", value=float(defs["tax"]), step=0.1)
    with c2:
        sv = st.number_input("Startwaarde (1 jan)", value=0.0, step=100.0)
        ev = st.number_input("Eindwaarde (31 dec)", value=0.0, step=100.0)
        dep = st.number_input("Netto Inleg", value=0.0, step=100.0)
    
    real = (ev - sv) - dep
    avg = sv + (dep / 2)
    rp = (real / avg * 100) if avg > 0 else 0
    
    taxable = (sv * fr) / 100
    due = (taxable * tr) / 100
    
    st.divider()
    c3, c4 = st.columns(2)
    with c3:
        col = "green" if real > taxable else "red"
        st.metric("Werkelijk", f"€ {real:,.2f}", delta=f"{rp:.2f}%")
    with c4:
        st.metric("Belasting", f"€ {due:,.2f}", help=f"Fictief rendement: € {taxable:,.2f}")
    
    if real > taxable: st.success("Gewonnen van de fiscus!")
    else: st.warning("Fictief > Werkelijk")
