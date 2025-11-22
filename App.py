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

# --- STATE MANAGEMENT ---
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
    "fwd_pe": "Verwachte K/W.\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Premie.",
    "roe": "Rendement op Eigen Vermogen.\n🟢 > 15%: Sterk management.",
    "de": "Schuldratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Liquiditeit.\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3.0: Redelijk.",
    "div": "Dividend Rendement.\n🟢 > 3%: Hoog inkomen.",
    "beta": "Volatiliteit.\n🟢 < 0.8: Stabiel.\n🔴 > 1.5: Wild.",
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
            if from_curr == "USD" and to_curr == "EUR" and rate > 1.02: return 1/rate
            return rate
        return 0.95 if from_curr == "USD" else 1.0
    except: return 1.0

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=25):
    if text and len(text) > max_len: return text[:max_len] + "..."
    return text

# --- PRECISE SCRAPERS ---
@st.cache_data(ttl=3600)
def scrape_yahoo_advanced(ticker):
    """Scrapet Yahoo Key Statistics pagina"""
    data = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        r = requests.get(f"https://finance.yahoo.com/quote/{ticker}/key-statistics", headers=headers, timeout=5)
        if r.status_code == 200:
            # Zoek specifiek naar Forward P/E waarde
            m_fwd = re.search(r'>Forward P/E.*?<td.*?>(.*?)</td>', r.text, re.DOTALL)
            if m_fwd: 
                try: data['fwd_pe'] = float(re.sub(r'[^\d\.]', '', m_fwd.group(1)))
                except: pass
            
            # Zoek specifiek naar PEG (5 yr expected)
            m_peg = re.search(r'>PEG Ratio \(5 yr expected\).*?<td.*?>(.*?)</td>', r.text, re.DOTALL)
            if m_peg:
                try: data['peg'] = float(re.sub(r'[^\d\.]', '', m_peg.group(1)))
                except: pass
    except: pass
    return data

@st.cache_data(ttl=3600)
def get_finviz_data(ticker):
    """Scrapet Finviz met stricte regex"""
    try:
        h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(f"https://finviz.com/quote.ashx?t={ticker}", headers=h, timeout=5)
        d = {}
        if r.status_code == 200:
            txt = r.text
            # Strictere regex: Zoek woord PEG, sluit tag, zoek volgende tag met bold
            # Voorbeeld: <td>PEG</td><td...><b>0.93</b></td>
            def p(label):
                pat = rf'>{label}</td>.*?<b>\s*([\d\.]+)\s*</b>'
                m = re.search(pat, txt, re.DOTALL)
                return float(m.group(1)) if m else None
                
            d['peg'] = p('PEG')
            d['pb'] = p('P/B')
            d['fwd_pe'] = p('Forward P/E')
            d['target'] = p('Target Price')
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
    
    if m.get('peg'):
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

# --- DATA MANAGER (CALLBACK) ---
def load_file_callback():
    uploaded_file = st.session_state.get('file_loader')
    if uploaded_file is not None:
        try:
            uploaded_file.seek(0)
            data = json.load(uploaded_file)
            if isinstance(data, list): st.session_state['portfolio'] = data
            elif isinstance(data, dict):
                st.session_state['portfolio'] = data.get('portfolio', [])
                st.session_state['wishlist'] = data.get('wishlist', [])
            st.toast("✅ Data succesvol ingeladen!", icon="💾")
        except Exception as e: st.error(f"Fout: {e}")

def get_json_data():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- SIDEBAR ---
st.sidebar.header("📂 Data")
st.sidebar.download_button(f"⬇️ Download ({len(st.session_state['portfolio'])} port / {len(st.session_state['wishlist'])} wish)", get_json_data(), "aandelen.json", "application/json")
st.sidebar.file_uploader("⬆️ Upload", type=['json'], key='file_loader', on_change=load_file_callback)
st.sidebar.divider()
if st.sidebar.button("🔄 Cache Legen (Reset Data)"): st.cache_data.clear(); st.rerun()

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
                st.error("Geen data.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price_eur = hist['Close'].iloc[-1] * rate
                
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
                        mi = yf.Ticker(us_t).info

                fin = get_finviz_data(mt)
                scrape = scrape_yahoo_advanced(mt)

                def get_m(y_k, f_k, scrape_k=None, calc=None):
                    if scrape_k and scrape.get(scrape_k): return scrape[scrape_k], "Yahoo Web"
                    if f_k and fin.get(f_k): return fin[f_k], "Finviz"
                    if safe_get(mi, y_k) is not None: return mi[y_k], "Yahoo API"
                    if calc:
                        c = calc()
                        if c is not None: return c, "Berekend"
                    return None, "N/A"

                def c_peg():
                    p = safe_get(mi, 'trailingPE') or safe_get(mi, 'forwardPE')
                    g = safe_get(mi, 'earningsGrowth')
                    if not g: g = safe_get(mi, 'revenueGrowth')
                    if p and g and g>0: return p/(g*100)
                    return None
                
                peg, peg_src = get_m('pegRatio', 'peg', 'peg', c_peg)
                fwd_pe, fwd_src = get_m('forwardPE', 'fwd_pe', 'fwd_pe')
                pb, pb_src = get_m('priceToBook', 'pb')
                tgt, tgt_src = get_m('targetMeanPrice', 'target')
                tgt_eur, upside = None, ""
                if tgt:
                    t_rate = get_currency_rate(safe_get(mi, 'currency', 'USD'), "EUR")
                    tgt_eur = tgt * t_rate
                    upside = f"{((tgt_eur-price_eur)/price_eur)*100:+.1f}%"

                de, de_src = get_m('debtToEquity', None)
                if de and de > 10: de/=100
                cr, cr_src = get_m('currentRatio', None)
                roe, roe_src = get_m('returnOnEquity', None)
                
                dy, dy_src = get_m('dividendYield', None)
                if not dy:
                    dr = safe_get(mi, 'dividendRate')
                    if dr:
                        try:
                            orig_p = yf.Ticker(mt).history(period="1d")['Close'].iloc[-1]
                            dy = dr/orig_p; dy_src = "Berekend"
                        except: pass
                if dy and dy > 0.5: dy/=100

                beta = safe_get(mi, 'beta')
                marg = safe_get(mi, 'profitMargins')
                
                d = hist['Close'].diff()
                g = (d.where(d>0, 0)).rolling(14).mean()
                l = (-d.where(d<0, 0)).rolling(14).mean()
                rs = g/l
                rsi = 100 - (100/(1+rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50

                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1:
                    st.title(truncate_text(safe_get(info, 'shortName', sel_ticker)))
                    st.caption(f"{sel_ticker} | Data: {mt}")
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
                                st.rerun()

                md = {'peg': peg, 'fwd_pe': fwd_pe, 'roe': roe, 'de': de, 'cr': cr, 'pb': pb}
                score, opps, risks, neut = calculate_score(md)

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
                def dm(l, v, s, k, fmt="{:.2f}", cl=None):
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
                with m1: dm("PEG", peg, peg_src, "peg", cl=lambda x: "g" if 0<x<1.5 else ("o" if x<0 else "y"))
                with m2: dm("Fwd P/E", fwd_pe, fwd_src, "fwd_pe", cl=lambda x: "g" if 0<x<20 else "y")
                with m3: dm("Current", cr, cr_src, "cr", cl=lambda x: "g" if x>1.5 else "o")
                with m4: dm("D/E", de, de_src, "de", cl=lambda x: "g" if x<1 else "o")
                with m5: dm("P/B", pb, pb_src, "pb", cl=lambda x: "g" if 0<x<3 else "y")
                with m6: dm("ROE", roe, roe_src, "roe", "{:.1%}", cl=lambda x: "g" if x>0.15 else "y")
                
                st.write("")
                x1, x2, x3 = st.columns(3)
                with x1: dm("Dividend", dy, dy_src, "div", "{:.2%}", cl=lambda x: "g" if x>0.03 else "b")
                with x2: dm("Beta", beta, "Yahoo", "beta", "{:.2f}", cl=lambda x: "g" if x<0.8 else "o")
                with x3: dm("Marge", marg, "Yahoo", "margin", "{:.1%}", cl=lambda x: "g" if x>0.2 else "b")

                st.markdown("### 📈 Koers")
                fig = go.Figure()
                lc = '#00C805' if hist['Close'].iloc[-1] >= hist['Close'].iloc[0] else '#FF5000'
                fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], mode='lines', line=dict(color=lc)))
                fig.update_layout(template="plotly_dark", margin=dict(t=10,b=0,l=0,r=0), height=350)
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
    
    total_val = 0
    total_inv = 0
    
    if st.session_state['portfolio']:
        rows = []
        sec_dist = {}
        div_cal = []
        
        bar = st.progress(0)
        items = st.session_state['portfolio']
        for i, item in enumerate(items):
            try:
                t = item['ticker']
                s = yf.Ticker(t)
                h = s.history(period="1d")
                
                if not h.empty:
                    raw = h['Close'].iloc[-1]
                    curr = safe_get(s.info, 'currency', 'EUR')
                    rate = get_currency_rate(curr, "EUR")
                    p_eur = raw * rate
                else: p_eur = item['avg_price_eur']
                
                val = p_eur * item['amount']
                cost = item['avg_price_eur'] * item['amount']
                
                total_val += val
                total_inv += cost
                
                sec = safe_get(s.info, 'sector', 'Overig')
                sec_dist[sec] = sec_dist.get(sec, 0) + val
                
                dr = safe_get(s.info, 'dividendRate')
                if not dr:
                    yd = safe_get(s.info, 'dividendYield')
                    if yd: dr = yd * (p_eur/rate)
                if dr:
                    dye = dr * rate * item['amount']
                    ex = safe_get(s.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m') if ex else "-"
                    div_cal.append({"Ticker": t, "Ex": exs, "Jaar (€)": dye})
                    
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
            bar.progress((i+1)/len(items))
        bar.empty()
        
        prof = total_val - total_inv
        k1, k2, k3 = st.columns(3)
        k1.metric("Waarde", f"€ {total_val:,.2f}")
        k2.metric("Winst", f"€ {prof:,.2f}", delta=f"{(prof/total_inv)*100:.1f}%" if total_inv else "0%")
        k3.metric("Inleg", f"€ {total_inv:,.2f}")
        st.divider()
        
        c_pie, c_div = st.columns(2)
        with c_pie:
            st.subheader("Verdeling")
            if total_val > 0 and sec_dist:
                fig = px.pie(values=list(sec_dist.values()), names=list(sec_dist.keys()), hole=0.4)
                fig.update_layout(margin=dict(t=0,b=0,l=0,r=0), height=200)
                st.plotly_chart(fig, use_container_width=True)
        
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
            
    else: st.info("Geen data.")

    # --- CALCULATOR (ALTIJD ZICHTBAAR) ---
    st.divider()
    with st.expander("🧮 Rente-op-Rente Planner"):
        cc1, cc2, cc3, cc4 = st.columns(4)
        sv = cc1.number_input("Start (€)", value=float(round(total_val, 0)), step=100.0)
        mi = cc2.number_input("Mnd Inleg (€)", min_value=0, value=500, step=50)
        jr = cc3.number_input("Jaren", min_value=1, value=20, step=1)
        rr = cc4.number_input("Rendement (%)", min_value=0.0, value=7.0, step=0.1)
        
        mths = np.arange(jr*12+1)
        mr = rr/100/12
        fut, inv = [sv], [sv]
        c, ci = sv, sv
        for _ in range(len(mths)-1):
            c = (c+mi)*(1+mr)
            ci += mi
            fut.append(c)
            inv.append(ci)
        st.metric(f"Na {jr} jaar", f"€ {fut[-1]:,.0f}", delta=f"Winst: € {fut[-1]-inv[-1]:,.0f}")
        df_c = pd.DataFrame({"Maand": mths, "Waarde": fut, "Inleg": inv})
        st.plotly_chart(px.line(df_c, x="Maand", y=["Waarde", "Inleg"]), use_container_width=True)

# ==================================================
# 3. WISHLIST
# ==================================================
with tabs[2]:
    st.title("Wishlist")
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            t = item['ticker']
            try:
                s = yf.Ticker(t)
                h = s.history(period="2d")
                if not h.empty:
                    rate = get_currency_rate(safe_get(s.info, 'currency', 'EUR'), "EUR")
                    cp = h['Close'].iloc[-1] * rate
                    pp = h['Close'].iloc[0] * rate
                    pct = ((cp-pp)/pp)*100
                    
                    wi = s.info
                    # Score lite (zonder advanced scrape voor snelheid)
                    pe = safe_get(wi, 'trailingPE')
                    g = safe_get(wi, 'earningsGrowth')
                    pg = pe/(g*100) if pe and g else None
                    sd = {'peg': pg, 'roe': safe_get(wi, 'returnOnEquity'), 'de': safe_get(wi, 'debtToEquity'), 'cr': safe_get(wi, 'currentRatio')}
                    if sd['de']: sd['de']/=100
                    sc, _, _, _ = calculate_score(sd)
                    scol = "green" if sc>=7 else ("orange" if sc>=5.5 else "red")
                else: cp=0; pct=0; sc=0; scol="gray"
            except: cp=0; pct=0; sc=0; scol="gray"

            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            with c1: st.markdown(f"**{item['name']}**")
            with c2: st.markdown(f"€ {cp:.2f} :{'green' if pct>=0 else 'red'}[{pct:+.1f}%]")
            with c3: st.markdown(f"AI: :{scol}[{sc:.1f}]")
            with c4:
                if st.button("🔎", key=f"wl_{t}"):
                    st.session_state['search_query'] = t
                    st.toast("Check Analyse!", icon="🚀")
                    st.rerun()
            st.divider()
            
        if st.button("Legen"):
            st.session_state['wishlist'] = []
            st.rerun()
    else: st.info("Leeg.")
