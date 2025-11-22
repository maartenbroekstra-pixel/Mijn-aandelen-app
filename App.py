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

# --- CONSTANTEN & MAPPING ---
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "IMCD.AS": "IMCDY"
}

# Standaardwaarden Belastingdienst (kunnen nu overschreven worden)
TAX_DEFAULTS = {
    2023: {"invest": 6.17, "tax": 32},
    2024: {"invest": 6.04, "tax": 36},
    2025: {"invest": 5.88, "tax": 36}
}

TOOLTIPS = {
    "peg": "Prijs/Winst gedeeld door Groei.\n🟢 < 1.5: Goedkoop.\n🔴 Negatief: Krimp.",
    "fwd_pe": "Verwachte K/W.\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Duur.",
    "roe": "Rendement Eigen Vermogen.\n🟢 > 15%: Sterk.",
    "de": "Schuldratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Liquiditeit.\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs/Boekwaarde.\n🟢 < 3: Redelijk.",
    "div": "Dividend.\n🟢 > 3%: Hoog.",
    "beta": "Volatiliteit.\n🟢 < 0.8: Stabiel.",
    "rsi": "Momentum.\n🟢 < 30: Koopkans?."
}

# --- HULPFUNCTIES ---
@st.cache_data(ttl=3600)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = f"{from_curr}{to_curr}=X"
        if from_curr == "USD" and to_curr == "EUR": ticker = "EUR=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD" and rate > 1.05: return 1/rate
            return rate
        return 0.95 if from_curr == "USD" else 1.0
    except: return 1.0

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=20):
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
            data = json.load(uploaded_file)
            if isinstance(data, dict):
                st.session_state['portfolio'] = data.get('portfolio', [])
                st.session_state['wishlist'] = data.get('wishlist', [])
            st.toast("✅ Data geladen!")
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

                sd = {'peg': peg, 'fwd_pe': fwd, 'roe': roe, 'de': de, 'cr': cr, 'pb': pb}
                score, opps, risks = calculate_ai_score(sd)

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
                st.markdown(f"""
                <div style="display:flex; align-items:center; gap: 15px; margin: 10px 0;">
                    <div style="min-width:70px; height:70px; border-radius:50%; border:5px solid {col}; display:flex; align-items:center; justify-content:center;">
                        <span style="font-size:24px; font-weight:bold; color:{col}">{score:.1f}</span>
                    </div>
                    <div><small>✅ {', '.join(opps) if opps else '-'}<br>⚠️ {', '.join(risks) if risks else '-'}</small></div>
                </div>""", unsafe_allow_html=True)

                cols = st.columns(4)
                def dm(i, l, v, s, k, f="{:.2f}"):
                    with cols[i]: st.metric(l, f.format(v) if v else "-", help=f"Bron: {s}\n\n{TOOLTIPS.get(k,'')}")
                
                dm(0, "PEG", peg, peg_s, 'peg')
                dm(1, "Fwd P/E", fwd, fwd_s, 'fwd_pe')
                dm(2, "ROE", roe, roe_s, 'roe', "{:.1%}")
                dm(3, "Dividend", dy, dy_s, 'div', "{:.2%}")
                
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
                rows.append({"Naam": t, "Aantal": item['amount'], "Prijs": f"€{cur:.2f}", "Waarde": f"€{v:.2f}", "Winst": f"€{v-c:.2f}"})
            except: pass
        
        k1, k2 = st.columns(2)
        k1.metric("Waarde", f"€ {tv:,.2f}")
        k2.metric("Winst", f"€ {tv-ti:,.2f}", delta=f"{((tv-ti)/ti)*100:.1f}%" if ti else "0%")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        
        if st.button("Alles Wissen"): st.session_state['portfolio'] = []; st.rerun()
        
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
                tick = yf.Ticker(t)
                h = tick.history(period="2d")
                if not h.empty:
                    r = get_currency_rate(safe_get(tick.info, 'currency', 'EUR'), "EUR")
                    cp = h['Close'].iloc[-1] * r
                    ch = ((cp - h['Close'].iloc[0] * r) / (h['Close'].iloc[0] * r)) * 100
                    col = "green" if ch >= 0 else "red"
                    
                    c1, c2 = st.columns([3, 1])
                    with c1: st.markdown(f"**{item['name']}**\n€{cp:.2f} (:{col}[{ch:+.1f}%])")
                    with c2:
                        if st.button("🔎", key=f"w_{t}"):
                            st.session_state['search_query'] = t
                            st.rerun()
                    st.divider()
            except: pass
        if st.button("Legen", key="wl_clr"): st.session_state['wishlist'] = []; st.rerun()
    else: st.info("Leeg.")

# ==================================================
# 4. BELASTING (NIEUW)
# ==================================================
with tabs[3]:
    st.header("🇳🇱 Box 3 (Beleggingen)")
    
    col1, col2 = st.columns(2)
    with col1:
        year = st.selectbox("Jaar", [2023, 2024, 2025])
        defs = TAX_DEFAULTS.get(year, {"invest": 6.04, "tax": 36})
        
        fict_rate = st.number_input("Fictief Rendement (%)", value=defs["invest"], step=0.01)
        tax_rate = st.number_input("Belastingtarief (%)", value=float(defs["tax"]), step=0.1)
        
    with col2:
        start_val = st.number_input("Waarde 1 jan (€)", value=0.0, step=100.0)
        end_val = st.number_input("Waarde 31 dec (€)", value=0.0, step=100.0)
        deposits = st.number_input("Netto Inleg (€)", value=0.0, step=100.0)
        
    st.divider()
    
    # Berekening Werkelijk
    real_gain = (end_val - start_val) - deposits
    avg_cap = start_val + (deposits / 2) # Simpele benadering
    real_pct = (real_gain / avg_cap * 100) if avg_cap > 0 else 0
    
    # Berekening Fiscus
    # De fiscus kijkt naar de waarde op 1 januari (peildatum)
    taxable_gain = (start_val * fict_rate) / 100
    tax_due = (taxable_gain * tax_rate) / 100
    
    c_res1, c_res2 = st.columns(2)
    with c_res1:
        color = "green" if real_gain > taxable_gain else "red"
        st.metric("Werkelijk Rendement", f"€ {real_gain:,.2f}", delta=f"{real_pct:.2f}%")
        st.caption("Jouw echte winst (Koerswinst + Dividend)")
        
    with c_res2:
        st.metric("Belasting (Box 3)", f"€ {tax_due:,.2f}", help=f"Over fictief rendement van € {taxable_gain:,.2f}")
        st.caption(f"Te betalen aan fiscus (o.b.v. {fict_rate}%)")

    if real_gain > taxable_gain:
        st.success(f"🎉 Je hebt de fiscus verslagen! Je echte rendement is hoger dan het fictieve rendement.")
    elif real_gain < taxable_gain:
        st.warning("Let op: Je betaalt belasting over winst die je niet hebt gemaakt (Fictief > Werkelijk).")
