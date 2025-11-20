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
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY",   # Heineken
    "AD.AS": "ADRNY",                        # Ahold Delhaize
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
    "peg": "Prijs/Winst gedeeld door de Groei (5-jaars verwachting).\n\n🟢 < 1.5: Goedkoop t.o.v. groei.\n🔴 Negatief: Winst krimp.\n🟠 > 2.5: Prijs loopt voor op groei.",
    "fwd_pe": "Verwachte K/W voor komend jaar.\n\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Groeiaandeel premie.",
    "roe": "Rendement op Eigen Vermogen.\n\n🟢 > 15%: Sterk management.\n🔴 < 5%: Zwak.",
    "de": "Schuldratio.\n\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap bij kas.",
    "pb": "Prijs / Boekwaarde.\n\n🟢 < 3.0: Redelijk.\n⚪ Tech mag hoger zijn.",
    "div": "Dividend Rendement.\n\n🟢 > 3%: Hoog inkomen.\n⚪ Laag is normaal bij groei.",
    "beta": "Volatiliteit (Markt = 1.0).\n\n🟢 < 0.8: Stabiel.\n🔴 > 1.5: Wild.",
    "rsi": "Momentum (14 dgn).\n\n🟢 < 30: Oversold (Koopkans?).\n🔴 > 70: Overbought.",
    "margin": "Netto Winstmarge.\n\n🟢 > 20%: Zeer winstgevend."
}

# --- HULPFUNCTIES ---

@st.cache_data(ttl=3600)
def get_currency_rate(from_currency, to_currency="EUR"):
    if from_currency == to_currency: return 1.0
    if not from_currency or from_currency == 'nan': return 1.0
    try:
        ticker = f"{from_currency}{to_currency}=X"
        if from_currency == "USD" and to_currency == "EUR": ticker = "EUR=X"
        
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_currency == "USD" and to_currency == "EUR" and rate > 1.05:
                return 1 / rate
            return rate
        if from_currency == "USD": return 0.95
        return 1.0
    except: return 1.0

def safe_get(info_dict, key, default=None):
    if not info_dict: return default
    val = info_dict.get(key)
    return val if val is not None else default

def truncate_text(text, max_len=25):
    if text and len(text) > max_len: return text[:max_len] + "..."
    return text

# --- DATASCRAPERS ---

@st.cache_data(ttl=3600)
def get_yahoo_peg_direct(ticker):
    """Scrapet de 'Key Statistics' pagina van Yahoo voor de echte 5-yr PEG"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://finance.yahoo.com/quote/{ticker}/key-statistics"
        r = requests.get(url, headers=headers, timeout=5)
        
        # Zoek naar "PEG Ratio (5 yr expected)" in de HTML
        # De waarde staat vaak in de buurt van deze tekst in een tabel
        if "PEG Ratio (5 yr expected)" in r.text:
            # Simpele regex om het eerste getal na deze tekst te vinden
            # Dit is een beetje 'hacky' maar werkt vaak waar de API faalt
            match = re.search(r'PEG Ratio \(5 yr expected\).*?</td><td class="Ta\(c\) Pstart\(10px\) Miw\(60px\) Miw\(80px\)--pnclg Bgc\(\$lv1BgColor\) fi-row:h_Bgc\(\$hoverBgColor\)">([\d\.]+)</td>', r.text, re.DOTALL)
            if not match:
                # Probeer een generiekere regex voor andere Yahoo layouts
                match = re.search(r'PEG Ratio \(5 yr expected\).*?([\d\.]+)', r.text)
            
            if match:
                return float(match.group(1))
    except: pass
    return None

@st.cache_data(ttl=3600)
def get_finviz_data(ticker):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        r = requests.get(url, headers=headers, timeout=5)
        data = {}
        if r.status_code == 200:
            text = r.text
            def parse(pat):
                m = re.search(pat, text, re.DOTALL)
                return float(m.group(1)) if m else None
            data['peg'] = parse(r'>PEG<.*?<b>\s*([\d\.]+)\s*</b>')
            data['pb'] = parse(r'>P/B<.*?<b>\s*([\d\.]+)\s*</b>')
            data['fwd_pe'] = parse(r'>Forward P/E<.*?<b>\s*([\d\.]+)\s*</b>')
            data['target'] = parse(r'>Target Price<.*?<b>\s*([\d\.]+)\s*</b>')
        return data
    except: return {}

def search_yahoo(query):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10"
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        results = []
        if 'quotes' in data:
            quotes = data['quotes']
            def sort_key(q):
                sym = q.get('symbol', '')
                if sym.endswith('.AS') or sym.endswith('.DE') or sym.endswith('.F'): return 0
                return 1
            quotes.sort(key=sort_key)
            for q in quotes:
                symbol = q.get('symbol')
                name = q.get('shortname', q.get('longname', symbol))
                exch = q.get('exchange', 'N/A')
                results.append(f"{symbol} | {name} ({exch})")
        return results
    except: return []

@st.cache_data(ttl=3600)
def find_primary_ticker(company_name):
    try:
        base_name = re.split(r' (Inc|Corp|Corporation|Hldgs|Holdings|Group|Grp|AG|SE|PLC|Ltd|GmbH|S\.A\.|NV|N\.V\.|B\.V\.|Co\.|DL-|DRc)', company_name, flags=re.IGNORECASE)[0]
        base_name = base_name.strip()
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={base_name}&lang=en-US&region=US&quotesCount=5"
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        if 'quotes' in data:
            for q in data['quotes']:
                sym = q['symbol']
                if "." not in sym and len(sym) <= 5: return sym
    except: pass
    return None

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    try:
        q = f"{query} stock analysis {datetime.now().year}"
        url = f"https://www.youtube.com/results?search_query={q.replace(' ', '+')}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=5)
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except: return None

def get_google_news(query):
    try:
        q = f"{query} stock finance news"
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, timeout=5)
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall('./channel/item')[:5]:
            title = item.find('title').text
            link = item.find('link').text
            try:
                dt = datetime.strptime(item.find('pubDate').text[:16], '%a, %d %b %Y')
                d_str = dt.strftime('%d %b %Y')
            except: d_str = ""
            items.append({"title": title, "link": link, "date": d_str})
        return items
    except: return []

def calculate_score(metrics):
    score = 0
    total = 0
    opps, risks, neut = [], [], []
    
    def check(val, name, good_cond, bad_cond):
        nonlocal score, total
        if val is not None:
            total += 1
            if good_cond(val): 
                score += 1; opps.append(f"{name} ({val:.2f}) is sterk.")
            elif bad_cond(val): 
                risks.append(f"{name} ({val:.2f}) is zwak.")
            else: 
                score += 0.5; neut.append(f"{name} ({val:.2f}) is neutraal.")

    peg = metrics.get('peg')
    if peg is not None:
        total += 1
        if 0 < peg < 1.5: score += 1; opps.append(f"PEG ({peg:.2f}) is aantrekkelijk.")
        elif peg < 0: risks.append("Negatieve PEG (Krimp).")
        elif peg > 2.5: risks.append(f"Hoge PEG ({peg:.2f}).")
        else: score += 0.5; neut.append(f"PEG ({peg:.2f}) is marktconform.")

    check(metrics.get('fwd_pe'), "Fwd P/E", lambda x: 0<x<20, lambda x: x>40)
    check(metrics.get('roe'), "ROE", lambda x: x>0.15, lambda x: x<0.05)
    check(metrics.get('de'), "Debt/Equity", lambda x: x<1.0, lambda x: x>2.0)
    check(metrics.get('cr'), "Current Ratio", lambda x: x>1.5, lambda x: x<1.0)
    check(metrics.get('pb'), "P/B", lambda x: 0<x<3.0, lambda x: x>10)

    final = (score / total) * 10 if total > 0 else 5.0
    return final, opps, risks, neut

# --- SAVE/LOAD ---
def save_data():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- SIDEBAR ---
st.sidebar.header("📂 Data Beheer")

# Download
count = len(st.session_state['portfolio'])
st.sidebar.download_button(
    label=f"⬇️ Download ({count} items)",
    data=save_data(),
    file_name="aandelen_data.json",
    mime="application/json"
)

# Upload
uploaded_file = st.sidebar.file_uploader("⬆️ Upload Data", type=['json'])
if uploaded_file:
    try:
        # Reset pointer
        uploaded_file.seek(0)
        data = json.load(uploaded_file)
        
        if isinstance(data, list):
            st.session_state['portfolio'] = data
        elif isinstance(data, dict):
            st.session_state['portfolio'] = data.get('portfolio', [])
            st.session_state['wishlist'] = data.get('wishlist', [])
            
        if len(st.session_state['portfolio']) > 0:
            st.sidebar.success(f"✅ {len(st.session_state['portfolio'])} aandelen geladen!")
        else:
            st.sidebar.warning("Bestand is leeg.")
            
    except Exception as e:
        st.sidebar.error(f"Fout: {e}")

st.sidebar.divider()
st.sidebar.info("Tip: .AS (Amsterdam) en .DE (Xetra) staan bovenaan.")

if st.sidebar.button("🔄 Cache Legen"):
    st.cache_data.clear()
    st.rerun()

# --- TABS ---
tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist"])

# ==================================================
# TAB 1: ANALYSE
# ==================================================
with tabs[0]:
    c_search, c_period = st.columns([3, 1])
    with c_search:
        def_q = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek Aandeel:", value=def_q, placeholder="Bijv. Heineken, PDD...", key="search")
        
        sel_ticker = None
        if txt and "." in txt and " " not in txt: sel_ticker = txt
        elif txt:
            res = search_yahoo(txt)
            if res:
                ch = st.selectbox("Resultaten:", res)
                sel_ticker = ch.split(" | ")[0]
            else: sel_ticker = txt.upper()
            
    with c_period:
        per = st.selectbox("Periode", ["1mo", "6mo", "1y", "3y", "5y"], index=2)

    if sel_ticker:
        try:
            stock = yf.Ticker(sel_ticker)
            info = stock.info
            hist = stock.history(period=per)
            
            if hist.empty:
                st.error("Geen data. Probeer een andere ticker.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price_eur = hist['Close'].iloc[-1] * rate
                
                # SMART DATA FETCH
                metrics_ticker = sel_ticker
                metrics_info = info
                
                if sel_ticker in EU_TO_US_MAP:
                    metrics_ticker = EU_TO_US_MAP[sel_ticker]
                    metrics_info = yf.Ticker(metrics_ticker).info
                elif any(safe_get(info, k) is None for k in ['forwardPE', 'pegRatio', 'targetMeanPrice']):
                    short_name = safe_get(info, 'shortName', '')
                    us_ticker = find_primary_ticker(short_name)
                    if us_ticker and us_ticker != sel_ticker:
                        metrics_ticker = us_ticker
                        metrics_info = yf.Ticker(us_ticker).info

                fin = get_finviz_data(metrics_ticker)

                def get_val(y_key, f_key, fallback_calc=None):
                    v = safe_get(metrics_info, y_key)
                    if v is not None: return v, "Yahoo"
                    if f_key and f_key in fin and fin[f_key] is not None: return fin[f_key], "Finviz"
                    if fallback_calc:
                        c = fallback_calc()
                        if c is not None: return c, "Berekend"
                    return None, "N/A"

                # PEG PRIORITY: 1. Yahoo Site (Scraper), 2. Yahoo API, 3. Finviz, 4. Calc
                peg_scrape = get_yahoo_peg_direct(metrics_ticker)
                if peg_scrape:
                    peg = peg_scrape; peg_src = "Yahoo Website (Direct)"
                else:
                    def calc_peg():
                        p = safe_get(metrics_info, 'trailingPE') or safe_get(metrics_info, 'forwardPE')
                        g = safe_get(metrics_info, 'earningsGrowth')
                        if not g: g = safe_get(metrics_info, 'revenueGrowth')
                        # Blokkeer negatieve groei berekening als er geen PEG is
                        if g and g > 0 and p: return p/(g*100)
                        return None
                    peg, peg_src = get_val('pegRatio', 'peg', calc_peg)

                fwd_pe, fwd_src = get_val('forwardPE', 'fwd_pe')
                pb, pb_src = get_val('priceToBook', 'pb')

                tgt, tgt_src = get_val('targetMeanPrice', 'target')
                if tgt:
                    t_curr = safe_get(metrics_info, 'currency', 'USD')
                    t_rate = get_currency_rate(t_curr, "EUR")
                    tgt_eur = tgt * t_rate
                    upside = f"{((tgt_eur-price_eur)/price_eur)*100:+.1f}%"
                else: tgt_eur, upside = None, ""

                de, de_src = get_val('debtToEquity', None)
                if de: de = de/100
                cr, cr_src = get_val('currentRatio', None)
                roe, roe_src = get_val('returnOnEquity', None)
                
                dy, dy_src = get_val('dividendYield', None)
                if dy is None:
                    dr = safe_get(metrics_info, 'dividendRate')
                    if dr: 
                        try: 
                            mp = yf.Ticker(metrics_ticker).history(period="1d")['Close'].iloc[-1]
                            dy = dr/mp; dy_src = "Berekend"
                        except: pass
                if dy and dy > 0.5: dy = dy/100

                beta = safe_get(metrics_info, 'beta')
                marg = safe_get(metrics_info, 'profitMargins')
                
                delta = hist['Close'].diff()
                gain = (delta.where(delta>0, 0)).rolling(14).mean()
                loss = (-delta.where(delta<0, 0)).rolling(14).mean()
                rs = gain/loss
                rsi = 100 - (100/(1+rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50

                # UI
                st.markdown("---")
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1:
                    nm = safe_get(info, 'shortName', sel_ticker)
                    st.title(truncate_text(nm))
                    st.caption(f"{sel_ticker} | Data: {metrics_ticker}")
                with c2: st.metric("Koers", f"€ {price_eur:.2f}")
                with c3: st.metric("Koersdoel", f"€ {tgt_eur:.2f}" if tgt_eur else "N/A", delta=upside, help=f"Bron: {tgt_src}")
                with c4:
                    with st.popover("➕ Toevoegen"):
                        tp, tw = st.tabs(["Portfolio", "Wishlist"])
                        with tp:
                            with st.form("fp"):
                                a = st.number_input("Aantal", 1.0)
                                p = st.number_input("GAK (€)", value=float(round(price_eur, 2)))
                                if st.form_submit_button("Opslaan"):
                                    st.session_state['portfolio'].append({"ticker": sel_ticker, "amount": a, "avg_price_eur": p})
                                    st.toast("Toegevoegd!", icon="✅")
                                    st.rerun()
                        with tw:
                            if st.button("Zet op Wishlist"):
                                exists = any(x['ticker'] == sel_ticker for x in st.session_state['wishlist'])
                                if not exists:
                                    st.session_state['wishlist'].append({"ticker": sel_ticker, "name": nm})
                                    st.toast("Toegevoegd!", icon="❤️")

                met_d = {'peg': peg, 'roe': roe, 'de': de, 'cr': cr, 'fwd_pe': fwd_pe, 'pb': pb}
                score, opps, risks, neut = calculate_score(met_d)
                
                st.markdown("### 🧬 Fundamentele Analyse")
                with st.container(border=True):
                    sc, txt = st.columns([1, 4])
                    with sc:
                        col = "green" if score>=7 else ("orange" if score>=5.5 else "red")
                        st.markdown(f"""<div style="display:flex;justify-content:center;"><div style="border:6px solid {col};width:100px;height:100px;border-radius:50%;display:flex;align-items:center;justify-content:center;"><span style="color:{col};font-size:2.5rem;font-weight:bold;">{score:.1f}</span></div></div>""", unsafe_allow_html=True)
                    with txt:
                        c_k, c_r, c_n = st.columns(3)
                        with c_k:
                            if opps:
                                st.success("**✅ Kansen**")
                                for o in opps: st.write(f"- {o}")
                        with c_r:
                            if risks:
                                st.error("**⚠️ Risico's**")
                                for r in risks: st.write(f"- {r}")
                        with c_n:
                            if neut:
                                st.info("**⚪ Neutraal**")
                                for n in neut: st.write(f"- {n}")

                st.divider()
                st.subheader("📊 Ratio's")
                
                def disp(lbl, v, src, key, fmt="{:.2f}", clogic=None):
                    tt = f"**Bron:** {src}\n\n{TOOLTIPS.get(key, '')}"
                    if v is None: st.metric(lbl, "N/A", help=tt); return
                    icon = "⚪"
                    if clogic:
                        res = clogic(v)
                        if res=="green": icon="🟢"
                        elif res=="orange": icon="🟠"
                        elif res=="blue": icon="🔵"
                        elif res=="red": icon="🔴"
                    st.metric(lbl, f"{icon} {fmt.format(v)}", help=tt)

                m1, m2, m3, m4, m5, m6 = st.columns(6)
                with m1: disp("PEG Ratio", peg, peg_src, "peg", clogic=lambda x: "green" if 0<x<1.5 else ("orange" if x<0 else "yellow"))
                with m2: disp("Forward P/E", fwd_pe, fwd_src, "fwd_pe", clogic=lambda x: "green" if 0<x<20 else "yellow")
                with m3: disp("Current Ratio", cr, cr_src, "cr", clogic=lambda x: "green" if x>1.5 else "orange")
                with m4: disp("Debt/Equity", de, de_src, "de", clogic=lambda x: "green" if x<1 else "orange")
                with m5: disp("P/B Ratio", pb, pb_src, "pb", clogic=lambda x: "green" if 0<x<3 else "yellow")
                with m6: disp("ROE", roe, roe_src, "roe", "{:.1%}", clogic=lambda x: "green" if x>0.15 else "yellow")

                st.write("")
                x1, x2, x3, x4 = st.columns(4)
                with x1: disp("Dividend", dy, dy_src, "div", "{:.2%}", clogic=lambda x: "green" if x>0.03 else ("blue" if x>0.015 else "white"))
                with x2: disp("RSI (14)", rsi_val, "Yahoo", "rsi", "{:.0f}", clogic=lambda x: "green" if x<30 else "white")
                with x3: disp("Beta", beta, "Yahoo", "beta", "{:.2f}", clogic=lambda x: "green" if x<0.8 else ("orange" if x>1.5 else "white"))
                with x4: disp("Winstmarge", marg, "Yahoo", "margin", "{:.1%}", clogic=lambda x: "green" if x>0.2 else ("blue" if x>0.1 else "orange"))

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
                        st.caption(n['date'])
                        st.divider()
                with cv:
                    st.markdown("### 📺 Video")
                    vid = get_youtube_video_id(safe_get(info, 'shortName', sel_ticker))
                    if vid: st.video(f"https://www.youtube.com/watch?v={vid}")
                    url = f"https://www.youtube.com/results?search_query={sel_ticker}+stock+analysis"
                    st.markdown(f"👉 **[Zoek op YouTube]({url})**")

        except Exception as e: st.error(f"Fout: {e}")

# ==================================================
# TAB 2: PORTFOLIO
# ==================================================
with tabs[1]:
    st.title("Mijn Portfolio")
    if st.session_state['portfolio']:
        rows = []
        tot_inv = 0
        tot_val = 0
        sec_dist = {}
        div_cal = []
        
        bar = st.progress(0)
        items = st.session_state['portfolio']
        for i, item in enumerate(items):
            try:
                t = item['ticker']
                s = yf.Ticker(t)
                h = s.history(period="1d")
                
                p_eur = 0
                rate = 1.0
                
                if not h.empty:
                    raw = h['Close'].iloc[-1]
                    curr = safe_get(s.info, 'currency', 'EUR')
                    rate = get_currency_rate(curr, "EUR")
                    p_eur = raw * rate
                else:
                    p_eur = item['avg_price_eur'] 
                
                val = p_eur * item['amount']
                cost = item['avg_price_eur'] * item['amount']
                prof = val - cost
                sec = safe_get(s.info, 'sector', 'Overig')
                sec_dist[sec] = sec_dist.get(sec, 0) + val
                
                dr = safe_get(s.info, 'dividendRate')
                if not dr:
                    dy = safe_get(s.info, 'dividendYield')
                    if dy and p_eur>0: dr = dy * (p_eur/rate)
                if dr:
                    dy_eur = dr * rate * item['amount']
                    ex = safe_get(s.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m-%Y') if ex else "-"
                    div_cal.append({"Ticker": t, "Ex-Date": exs, "Jaarlijks (€)": dy_eur, "Mnd (€)": dy_eur/12})
                
                rows.append({
                    "Ticker": t,
                    "Aantal": item['amount'],
                    "GAK (€)": item['avg_price_eur'],
                    "Huidige Prijs (€)": p_eur,
                    "Waarde (€)": val,
                    "Winst (€)": prof,
                    "Rendement (%)": (prof/cost)*100 if cost>0 else 0
                })
                tot_val += val
                tot_inv += cost
            except: pass
            bar.progress((i+1)/len(items))
        bar.empty()
        
        tres = tot_val - tot_inv
        k1, k2, k3 = st.columns(3)
        k1.metric("Waarde", f"€ {tot_val:,.2f}")
        k2.metric("Winst/Verlies", f"€ {tres:,.2f}", delta=f"{(tres/tot_inv)*100:.1f}%" if tot_inv>0 else "0%")
        k3.metric("Inleg", f"€ {tot_inv:,.2f}")
        st.divider()
        
        c_sec, c_div = st.columns(2)
        with c_sec:
            st.subheader("Sectoren")
            if sec_dist and sum(sec_dist.values()) > 0: # FIX: Check op non-zero
                fig = px.pie(values=list(sec_dist.values()), names=list(sec_dist.keys()), hole=0.4)
                fig.update_layout(margin=dict(t=0,b=0,l=0,r=0), height=250)
                st.plotly_chart(fig, use_container_width=True)
        with c_div:
            st.subheader("💰 Dividend")
            if div_cal:
                df_d = pd.DataFrame(div_cal)
                st.metric("Geschat Mnd Inkomen", f"€ {df_d['Mnd (€)'].sum():.2f}")
                st.dataframe(df_d[["Ticker", "Ex-Date", "Jaarlijks (€)"]], hide_index=True, use_container_width=True)
            else: st.info("Geen dividenddata.")

        st.subheader("Details")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, column_config={
            "Huidige Prijs (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Waarde (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Winst (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Rendement (%)": st.column_config.NumberColumn(format="%.1f%%")
        })
        
        with st.expander("🧮 Rente-op-Rente Planner"):
            cc1, cc2, cc3, cc4 = st.columns(4)
            sv = cc1.number_input("Start (€)", value=float(round(tot_val, 0)), step=100.0)
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
            
    else: st.info("Nog geen aandelen.")

# ==================================================
# TAB 3: WISHLIST (Mobile Optimized)
# ==================================================
with tabs[2]:
    st.title("❤️ Mijn Wishlist")
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            ticker = item['ticker']
            try:
                # Data
                ws = yf.Ticker(ticker)
                w_hist = w_stock.history(period="2d")
                curr = safe_get(ws.info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                
                if not w_hist.empty:
                    cp = w_hist['Close'].iloc[-1] * rate
                    pp = w_hist['Close'].iloc[0] * rate
                    pct = ((cp-pp)/pp)*100
                    
                    wi = ws.info
                    wpeg = safe_get(wi, 'pegRatio')
                    if wpeg is None:
                        pe = safe_get(wi, 'trailingPE')
                        g = safe_get(wi, 'earningsGrowth')
                        if pe and g: wpeg = pe/(g*100)
                    
                    wd = {'peg': wpeg, 'roe': safe_get(wi, 'returnOnEquity'), 'de': safe_get(wi, 'debtToEquity'), 'cr': safe_get(wi, 'currentRatio')}
                    if wd['de']: wd['de'] /= 100
                    wsc, _, _, _ = calculate_score(wd)
                    scol = "green" if wsc>=7 else ("orange" if wsc>=5.5 else "red")
                else:
                    cp=0; pct=0; wsc=0; scol="gray"
            except: cp=0; pct=0; wsc=0; scol="gray"

            # Compact 2-Kolom Layout
            c_info, c_btn = st.columns([4, 1])
            with c_info:
                # Alles in één HTML blok voor perfecte uitlijning op mobiel
                col_txt = "green" if pct >= 0 else "red"
                st.markdown(f"""
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <b>{item['name']}</b><br>
                        <span style='color:gray; font-size:0.8em'>{ticker}</span>
                    </div>
                    <div style="text-align:right;">
                        <b>€ {cp:.2f}</b> <span style='color:{col_txt}; font-size:0.9em'>({pct:+.1f}%)</span><br>
                        <span style='font-size:0.8em'>AI Score: <b style='color:{scol}'>{wsc:.1f}</b></span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            with c_btn:
                if st.button("🔎", key=f"w_{ticker}"):
                    st.session_state['search_query'] = ticker
                    st.toast("Analyse laden...", icon="⏳")
                    st.rerun() # Gaat terug naar analyse tab met query
            st.divider()
            
        if st.button("Legen"):
            st.session_state['wishlist'] = []
            st.rerun()
    else: st.info("Leeg.")
