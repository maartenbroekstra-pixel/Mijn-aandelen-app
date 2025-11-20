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
st.set_page_config(page_title="Aandelen Dashboard", layout="wide", page_icon="📈")

# --- STATE INITIALISATIE (DE MASTER DATA) ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []

# --- MAPPING (EU -> US voor dataverrijking) ---
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY",   # Heineken
    "AD.AS": "ADRNY",                        # Ahold
    "UNA.AS": "UL",                          # Unilever
    "VOW3.DE": "VWAGY",                      # Volkswagen
    "ADS.DE": "ADDYY",                       # Adidas
    "BMW.DE": "BMWYY",                       # BMW
    "DTE.DE": "DTEGY",                       # Deutsche Telekom
    "SIE.DE": "SIEGY",                       # Siemens
    "ALV.DE": "ALIZY",                       # Allianz
    "AIR.PA": "EADSY",                       # Airbus
    "MC.PA": "LVMUY",                        # LVMH
    "OR.PA": "LRLCY",                        # L'Oreal
    "ASML.AS": "ASML",                       # ASML
    "PHIA.AS": "PHG",                        # Philips
    "INGA.AS": "ING",                        # ING
    "MT.AS": "MT",                           # ArcelorMittal
    "DSM.AS": "DSM",                         # DSM
    "2PP.DE": "PYPL",                        # Paypal
    "NOVO-B.CO": "NVO",                      # Novo Nordisk
    "NOVO-B.DE": "NVO"
}

# --- TOOLTIPS ---
TOOLTIPS = {
    "peg": "Prijs/Winst gedeeld door Groei.\n🟢 < 1.5: Goedkoop.\n🔴 Negatief: Krimp.",
    "fwd_pe": "Verwachte K/W.\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Duur.",
    "roe": "Rendement op Eigen Vermogen.\n🟢 > 15%: Sterk management.",
    "de": "Schuldratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Liquiditeit.\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3.0: Redelijk.\n⚪ Tech mag hoger zijn.",
    "div": "Dividend.\n🟢 > 3%: Hoog inkomen.",
    "beta": "Volatiliteit.\n🟢 < 0.8: Stabiel.\n🔴 > 1.5: Wild.",
    "rsi": "Momentum.\n🟢 < 30: Koopkans?.\n🔴 > 70: Te duur?."
}

# --- HULPFUNCTIES ---
@st.cache_data(ttl=3600)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr or from_curr == 'nan': return 1.0
    try:
        # Probeer standaard ticker
        ticker = f"{from_curr}{to_curr}=X"
        if from_curr == "USD" and to_curr == "EUR": ticker = "EUR=X"
        
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            # Sanity check: USD->EUR is ong 0.95. Als we 1.05 krijgen is het andersom.
            if from_curr == "USD" and to_curr == "EUR" and rate > 1.02:
                return 1 / rate
            return rate
        return 0.95 if from_curr == "USD" else 1.0
    except: return 1.0

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

@st.cache_data(ttl=3600)
def get_finviz_data(ticker):
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://finviz.com/quote.ashx?t={ticker}", headers=h, timeout=5)
        d = {}
        if r.status_code == 200:
            txt = r.text
            def parse(pat):
                m = re.search(pat, txt, re.DOTALL)
                return float(m.group(1)) if m else None
            d['peg'] = parse(r'>PEG<.*?<b>\s*([\d\.]+)\s*</b>')
            d['pb'] = parse(r'>P/B<.*?<b>\s*([\d\.]+)\s*</b>')
            d['fwd_pe'] = parse(r'>Forward P/E<.*?<b>\s*([\d\.]+)\s*</b>')
            d['target'] = parse(r'>Target Price<.*?<b>\s*([\d\.]+)\s*</b>')
        return d
    except: return {}

def search_yahoo(query):
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10", headers=h, timeout=5)
        d = r.json()
        res = []
        if 'quotes' in d:
            qs = d['quotes']
            # Sorteer EU tickers bovenaan
            qs.sort(key=lambda x: 0 if x.get('symbol','').endswith(('.AS','.DE','.F','.CO')) else 1)
            for q in qs:
                res.append(f"{q['symbol']} | {q.get('shortname','')} ({q.get('exchange','')})")
        return res
    except: return []

@st.cache_data(ttl=3600)
def find_primary_ticker(company_name):
    try:
        # Strip juridische termen
        base = re.split(r' (Inc|Corp|Corporation|Hldgs|Holdings|Group|AG|SE|PLC|Ltd|GmbH|S\.A\.|NV|N\.V\.|B\.V\.)', company_name, flags=re.IGNORECASE)[0].strip()
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={base}&lang=en-US&region=US&quotesCount=5", headers=h, timeout=5)
        d = r.json()
        if 'quotes' in d:
            for q in d['quotes']:
                # Zoek ticker zonder punt en max 5 letters (US ticker)
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
            try: 
                dt_str = datetime.strptime(i.find('pubDate').text[:16], '%a, %d %b %Y').strftime('%d %b')
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
    
    # PEG
    if m['peg']:
        total+=1
        if 0<m['peg']<1.5: score+=1; opps.append(f"PEG ({m['peg']:.2f}) is goedkoop.")
        elif m['peg']<0: risks.append("Negatieve PEG.")
        elif m['peg']>2.5: risks.append(f"Hoge PEG ({m['peg']:.2f}).")
        else: score+=0.5; neut.append("PEG normaal.")
    
    # Helper
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

# --- DATA MANAGEMENT (ROBUUST) ---
def handle_upload():
    """Verwerkt upload en update state DIRECT"""
    uploaded_file = st.session_state.get('uploader')
    if uploaded_file:
        try:
            uploaded_file.seek(0)
            data = json.load(uploaded_file)
            if isinstance(data, dict):
                st.session_state['portfolio'] = data.get('portfolio', [])
                st.session_state['wishlist'] = data.get('wishlist', [])
            elif isinstance(data, list):
                st.session_state['portfolio'] = data
                st.session_state['wishlist'] = []
            st.toast("✅ Data geladen!", icon="💾")
        except:
            st.error("Fout bestand.")

def get_download_data():
    """Genereert JSON van HUIDIGE state"""
    return json.dumps({
        'portfolio': st.session_state['portfolio'],
        'wishlist': st.session_state['wishlist']
    })

# --- SIDEBAR ---
st.sidebar.header("📂 Data")
# Download altijd van huidige state
st.sidebar.download_button("⬇️ Download Data", get_download_data(), "aandelen.json", "application/json")
# Upload triggert callback
st.sidebar.file_uploader("⬆️ Upload Data", type=['json'], key='uploader', on_change=handle_upload)

st.sidebar.divider()
if st.sidebar.button("🗑️ Reset Cache"):
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
                ch = st.selectbox("Kies:", res)
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
                st.error("Geen data gevonden.")
            else:
                # Koers & Valuta
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price_eur = hist['Close'].iloc[-1] * rate
                
                # SMART DATA LOGICA
                metrics_info = info
                metrics_ticker = sel_ticker
                
                # Mapping check
                if sel_ticker in EU_TO_US_MAP:
                    metrics_ticker = EU_TO_US_MAP[sel_ticker]
                    metrics_info = yf.Ticker(metrics_ticker).info
                # Of dynamisch zoeken als data mist
                elif any(safe_get(info, k) is None for k in ['forwardPE', 'pegRatio', 'targetMeanPrice']):
                    sn = safe_get(info, 'shortName', '')
                    us_t = find_primary_ticker(sn)
                    if us_t and us_t != sel_ticker:
                        metrics_ticker = us_t
                        metrics_info = yf.Ticker(us_t).info

                fin = get_finviz_data(metrics_ticker)

                # METRICS OPHALEN
                def get_m(y_k, f_k):
                    if safe_get(metrics_info, y_k) is not None: return metrics_info[y_k], "Yahoo"
                    if fin.get(f_k): return fin[f_k], "Finviz"
                    return None, "N/A"

                pe, _ = get_m('trailingPE', None)
                fwd_pe, fwd_src = get_m('forwardPE', 'fwd_pe')
                
                # PEG Complex
                peg, peg_src = get_m('pegRatio', 'peg')
                if not peg:
                    # Calc
                    base_pe = pe if pe else fwd_pe
                    g = safe_get(metrics_info, 'earningsGrowth')
                    if base_pe and g: peg = base_pe/(g*100); peg_src="Berekend"

                pb, pb_src = get_m('priceToBook', 'pb')
                
                # Target
                tgt, tgt_src = get_m('targetMeanPrice', 'target')
                tgt_eur, upside = None, ""
                if tgt:
                    t_rate = get_currency_rate(safe_get(metrics_info, 'currency', 'USD'), "EUR")
                    tgt_eur = tgt * t_rate
                    upside = f"{((tgt_eur-price_eur)/price_eur)*100:+.1f}%"

                de = safe_get(metrics_info, 'debtToEquity'); de = de/100 if de else None
                cr = safe_get(metrics_info, 'currentRatio')
                roe = safe_get(metrics_info, 'returnOnEquity')
                beta = safe_get(metrics_info, 'beta')
                marg = safe_get(metrics_info, 'profitMargins')

                # Dividend
                dy, dy_src = get_m('dividendYield', None)
                if not dy:
                    dr = safe_get(metrics_info, 'dividendRate')
                    if dr: 
                        # Probeer prijs van metrics ticker te pakken voor yield
                        try: 
                            mp = yf.Ticker(metrics_ticker).history(period="1d")['Close'].iloc[-1]
                            dy = dr/mp; dy_src = "Berekend"
                        except: pass
                if dy and dy > 0.5: dy = dy/100

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
                                    st.rerun()
                        with tw:
                            if st.button("Zet op Wishlist"):
                                st.session_state['wishlist'].append({"ticker": sel_ticker, "name": safe_get(info, 'shortName')})
                                st.toast("Opgeslagen!", icon="❤️")

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
                with m3: d_m("Current", cr, "Yahoo", "cr", cl=lambda x: "g" if x>1.5 else "o")
                with m4: d_m("D/E", de, "Yahoo", "de", cl=lambda x: "g" if x<1 else "o")
                with m5: d_m("P/B", pb, pb_src, "pb", cl=lambda x: "g" if 0<x<3 else "y")
                with m6: d_m("ROE", roe, "Yahoo", "roe", "{:.1%}", cl=lambda x: "g" if x>0.15 else "y")
                
                st.write("")
                x1, x2, x3 = st.columns(3)
                with x1: d_m("Dividend", dy, dy_src, "div", "{:.2%}", cl=lambda x: "g" if x>0.03 else "b")
                with x2: d_m("Beta", beta, "Yahoo", "beta", "{:.2f}", cl=lambda x: "g" if x<0.8 else "o")
                with x3: d_m("Marge", marg, "Yahoo", "margin", "{:.1%}", cl=lambda x: "g" if x>0.2 else "b")

                st.markdown("### 📈 Koers")
                fig = go.Figure()
                lc = '#00C805' if hist['Close'].iloc[-1] >= hist['Close'].iloc[0] else '#FF5000'
                fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], mode='lines', line=dict(color=lc)))
                fig.update_layout(template="plotly_dark", margin=dict(t=10,b=0,l=0,r=0), height=300)
                st.plotly_chart(fig, use_container_width=True)

                cn, cv = st.columns(2)
                with cn:
                    st.markdown("### 📰 Nieuws")
                    for n in get_google_news(safe_get(info, 'shortName', sel_ticker)):
                        st.markdown(f"**[{n['title']}]({n['link']})**")
                        st.divider()
                with cv:
                    st.markdown("### 📺 Video")
                    vid = get_youtube_video_id(safe_get(info, 'shortName', sel_ticker))
                    if vid: st.video(f"https://www.youtube.com/watch?v={vid}")
        except Exception as e: st.error(f"Fout: {e}")

# ==================================================
# 2. PORTFOLIO
# ==================================================
with tabs[1]:
    st.title("Portfolio")
    
    if st.session_state['portfolio']:
        rows = []
        sec_dist = {}
        div_cal = []
        
        total_val = 0
        total_inv = 0
        
        bar = st.progress(0)
        for i, item in enumerate(st.session_state['portfolio']):
            t = item['ticker']
            try:
                s = yf.Ticker(t)
                h = s.history(period="1d")
                
                if not h.empty:
                    raw = h['Close'].iloc[-1]
                    curr = safe_get(s.info, 'currency', 'EUR')
                    rate = get_currency_rate(curr, "EUR")
                    p_eur = raw * rate
                else:
                    p_eur = item['avg_price_eur']
                
                val = p_eur * item['amount']
                cost = item['avg_price_eur'] * item['amount']
                
                total_val += val
                total_inv += cost
                
                sec = safe_get(s.info, 'sector', 'Overig')
                sec_dist[sec] = sec_dist.get(sec, 0) + val
                
                # Dividend
                dr = safe_get(s.info, 'dividendRate')
                if not dr:
                    yd = safe_get(s.info, 'dividendYield')
                    if yd: dr = yd * (p_eur/rate)
                if dr:
                    dy_eur = dr * rate * item['amount']
                    ex = safe_get(s.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m') if ex else "-"
                    div_cal.append({"Ticker": t, "Ex": exs, "Jaar (€)": dy_eur})
                    
                rows.append({
                    "Ticker": t,
                    "Aantal": item['amount'],
                    "GAK": item['avg_price_eur'],
                    "Prijs": p_eur,
                    "Waarde": val,
                    "Winst": val - cost,
                    "Rend %": ((val-cost)/cost)*100 if cost else 0
                })
            except: pass
            bar.progress((i+1)/len(st.session_state['portfolio']))
        bar.empty()
        
        # KPI
        prof = total_val - total_inv
        k1, k2, k3 = st.columns(3)
        k1.metric("Waarde", f"€ {total_val:,.2f}")
        k2.metric("Winst", f"€ {prof:,.2f}", delta=f"{(prof/total_inv)*100:.1f}%" if total_inv else "0%")
        k3.metric("Inleg", f"€ {total_inv:,.2f}")
        st.divider()
        
        # CHARTS
        c_pie, c_div = st.columns(2)
        with c_pie:
            st.subheader("Verdeling")
            # FIX: Check of total_val > 0 om crash te voorkomen
            if total_val > 0 and sec_dist:
                fig = px.pie(values=list(sec_dist.values()), names=list(sec_dist.keys()), hole=0.4)
                fig.update_layout(margin=dict(t=0,b=0,l=0,r=0), height=200)
                st.plotly_chart(fig, use_container_width=True)
            else: st.info("Geen waardevolle data.")
        
        with c_div:
            st.subheader("Dividend")
            if div_cal:
                df_d = pd.DataFrame(div_cal)
                st.metric("Mnd Inkomen", f"€ {df_d['Jaar (€)'].sum()/12:.2f}")
                st.dataframe(df_d, hide_index=True, use_container_width=True)
            else: st.info("Geen dividend.")
            
        st.subheader("Details")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        
        if st.button("Alles Wissen"):
            st.session_state['portfolio'] = []
            st.rerun()
            
    else: st.info("Geen portfolio data.")

# ==================================================
# 3. WISHLIST
# ==================================================
with tabs[2]:
    st.title("Wishlist")
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            t = item['ticker']
            # Snelkoers ophalen
            try:
                s = yf.Ticker(t)
                h = s.history(period="2d")
                if not h.empty:
                    cp = h['Close'].iloc[-1]
                    pp = h['Close'].iloc[0]
                    pct = ((cp-pp)/pp)*100
                    
                    wi = s.info
                    pe = safe_get(wi, 'trailingPE')
                    g = safe_get(wi, 'earningsGrowth')
                    p_sc = pe/(g*100) if pe and g else None
                    
                    sd = {'peg': p_sc, 'roe': safe_get(wi, 'returnOnEquity'), 'de': safe_get(wi, 'debtToEquity'), 'cr': safe_get(wi, 'currentRatio')}
                    if sd['de']: sd['de'] /= 100
                    sc, _, _, _ = calculate_score(sd)
                    sc_col = "green" if sc>=7 else ("orange" if sc>=5.5 else "red")
                else: cp=0; pct=0; sc=0; sc_col="gray"
            except: cp=0; pct=0; sc=0; sc_col="gray"

            # COMPACTE REGEL
            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            with c1: st.markdown(f"**{item['name']}**")
            with c2: st.markdown(f"€ {cp:.2f} :{'green' if pct>=0 else 'red'}[{pct:+.1f}%]")
            with c3: st.markdown(f"AI: :{sc_col}[{sc:.1f}]")
            with c4:
                if st.button("🔎", key=f"wl_{t}"):
                    st.session_state['search_query'] = t
                    st.toast("Check Analyse!", icon="🚀")
            st.divider()
            
        if st.button("Legen"):
            st.session_state['wishlist'] = []
            st.rerun()
    else: st.info("Leeg.")
