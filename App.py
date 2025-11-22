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

# --- CONSTANTEN ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD"
}

TAX_DEFAULTS = {
    2023: {"invest": 6.17, "tax": 32},
    2024: {"invest": 6.04, "tax": 36},
    2025: {"invest": 5.88, "tax": 36}
}

# Uitgebreide Tooltips
TOOLTIPS = {
    "peg": "Price/Earnings to Growth.\n🟢 < 1.5: Het aandeel is goedkoop t.o.v. de groei.\n🟠 > 2.5: De groei is duur betaald.",
    "fwd_pe": "Forward P/E (Verwachte K/W).\n🟢 < 20: Aantrekkelijke waardering.\n🔴 > 40: Hoge verwachtingen ingeprijsd.",
    "roe": "Return on Equity (Winst op Eigen Vermogen).\n🟢 > 15%: Management gaat efficiënt om met geld.",
    "de": "Debt to Equity (Schuldratio).\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Risicovol veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Kan makkelijk korte termijn schulden betalen.",
    "pb": "Price to Book (Prijs/Boekwaarde).\n🟢 < 3.0: Redelijke prijs voor de bezittingen.",
    "div_yield": "Dividend Rendement (Jaarlijks).\n🟢 > 3%: Mooie inkomstenstroom.",
    "profit_margin": "Winstmarge (Netto).\n🟢 > 20%: Zeer winstgevend bedrijf.",
    "target": "Gemiddeld koersdoel van analisten."
}

# --- HULPFUNCTIES ---

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=22):
    if text and len(text) > max_len: return text[:max_len] + ".."
    return text

def clean_float(val_str):
    """Converteert string naar float. Handelt % af (deelt door 100)."""
    if not val_str: return None
    try:
        clean = re.sub(r'[^\d\.-]', '', val_str)
        if not clean or clean == '.' or clean == '-': return None
        val = float(clean)
        if '%' in val_str: val /= 100
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
        return 0.95 if from_curr == "USD" else 1.0
    except: return 1.0

# --- SCRAPERS ---

@st.cache_data(ttl=300)
def scrape_yahoo_live(ticker):
    data = {}
    try:
        # Key Statistics
        url = f"https://finance.yahoo.com/quote/{ticker}/key-statistics"
        r = requests.get(url, headers=HEADERS, timeout=5)
        txt = r.text
        
        # Regex die zoekt naar getallen in de buurt van labels
        def get_stat(regex_lbl):
            # Probeer de tabel cel ernaast
            m = re.search(rf'{regex_lbl}.*?</td>.*?<td[^>]*>(.*?)</td>', txt, re.DOTALL)
            if m: return clean_float(re.sub(r'<.*?>', '', m.group(1)))
            return None

        data['peg'] = get_stat(r'PEG Ratio \(5 yr expected\)')
        data['fwd_pe'] = get_stat(r'Forward P/E')
        data['pb'] = get_stat(r'Price/Book')
        data['profit_margin'] = get_stat(r'Profit Margin')
        data['roe'] = get_stat(r'Return on Equity')
        data['de'] = get_stat(r'Total Debt/Equity')
        data['cr'] = get_stat(r'Current Ratio')
        
        # Targets zitten op analysis tab
        r2 = requests.get(f"https://finance.yahoo.com/quote/{ticker}/analysis", headers=HEADERS, timeout=5)
        m_tgt = re.search(r'Average Target.*?(\d{1,5}\.\d{2})', r2.text, re.DOTALL)
        if m_tgt: data['target'] = clean_float(m_tgt.group(1))

    except: pass
    return data

@st.cache_data(ttl=300)
def scrape_finviz(ticker):
    d = {}
    try:
        r = requests.get(f"https://finviz.com/quote.ashx?t={ticker}", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            txt = r.text
            def p(label):
                m = re.search(rf'>{label}</td>[^<]*<td[^>]*><b>\s*([\d\.-]+%?)\s*</b>', txt, re.DOTALL)
                return clean_float(m.group(1)) if m and m.group(1)!='-' else None
            
            d['peg'] = p('PEG')
            d['fwd_pe'] = p('Forward P/E')
            d['pb'] = p('P/B')
            d['roe'] = p('ROE')
            d['de'] = p('Debt/Eq')
            d['cr'] = p('Current Ratio')
            d['profit_margin'] = p('Profit Margin')
            d['div_yield'] = p('Dividend %')
            d['target'] = p('Target Price')
            d['eps'] = p('EPS \(ttm\)')
    except: pass
    return d

@st.cache_data(ttl=300)
def scrape_google_finance(ticker):
    data = {}
    search_ticker = ticker
    if "." in ticker and ".AS" in ticker: search_ticker = f"{ticker.replace('.AS','')}:AMS"
    elif "." not in ticker: search_ticker = f"{ticker}:NASDAQ"

    try:
        url = f"https://www.google.com/finance/quote/{search_ticker}"
        r = requests.get(url, headers=HEADERS, timeout=4)
        txt = r.text
        
        # Google zoekt op label text -> getal
        def get_g(lbl):
            m = re.search(rf'{lbl}.{{0,100}}?>([\d\.,]+)%?<', txt, re.IGNORECASE | re.DOTALL)
            if m: return clean_float(m.group(1))
            return None
        
        data['pe'] = get_g("P/E ratio")
        data['div_yield'] = get_g("Dividend yield")
    except: pass
    return data

def get_averaged_data(ticker, us_ticker, yf_info_fallback):
    # Data ophalen
    df = scrape_finviz(us_ticker) if us_ticker else {}
    dy = scrape_yahoo_live(us_ticker if us_ticker else ticker)
    dg = scrape_google_finance(ticker)
    
    keys = ['peg', 'fwd_pe', 'pb', 'roe', 'de', 'cr', 'profit_margin', 'div_yield', 'target']
    
    final = {}
    sources = {}

    for k in keys:
        vals = []
        src_names = []
        
        # Verzamel niet-None waardes
        if df.get(k) is not None: vals.append(df[k]); src_names.append("Finviz")
        if dy.get(k) is not None: vals.append(dy[k]); src_names.append("Yahoo")
        if dg.get(k) is not None: vals.append(dg[k]); src_names.append("Google")
        
        # Als scrapers falen, gebruik API
        if not vals:
            api_map = {
                'peg': 'pegRatio', 'fwd_pe': 'forwardPE', 'pb': 'priceToBook',
                'roe': 'returnOnEquity', 'de': 'debtToEquity', 'cr': 'currentRatio',
                'profit_margin': 'profitMargins', 'target': 'targetMeanPrice', 'div_yield': 'dividendYield'
            }
            api_val = safe_get(yf_info_fallback, api_map.get(k))
            
            if api_val is not None:
                if k == 'de' and api_val > 10: api_val /= 100
                if k == 'div_yield' and api_val > 0.5: api_val /= 100
                vals.append(api_val)
                src_names.append("API")

        # BEREKEN HET GEMIDDELDE
        if vals:
            final[k] = sum(vals) / len(vals)
            # Maak een mooie string: "Gemiddelde van: Yahoo + Finviz"
            sources[k] = " + ".join(src_names)
        else:
            final[k] = None
            sources[k] = "Geen data"
            
    final['eps'] = df.get('eps', safe_get(yf_info_fallback, 'trailingEps'))
    return final, sources

# --- AI & SEARCH (VERBETERD MET VOLZINNEN) ---
def generate_ai_analysis(metrics, eps=None):
    score = 0
    count = 0
    reasons = [] # Lijst voor positieve punten (tekst)
    risks = []   # Lijst voor negatieve punten (tekst)
    
    # 1. Check Metrics
    # PEG
    peg = metrics.get('peg')
    if peg:
        count += 1
        if 0 < peg < 1.5: 
            score += 1; reasons.append(f"Het aandeel is aantrekkelijk gewaardeerd ten opzichte van de groei (PEG: {peg:.2f}).")
        elif peg > 2.5: 
            risks.append(f"De groei is relatief duur betaald op dit moment (PEG: {peg:.2f}).")
        else: score += 0.5
    
    # PE
    pe = metrics.get('fwd_pe')
    if pe:
        count += 1
        if 0 < pe < 20: 
            score += 1; reasons.append(f"De verwachte koers/winst verhouding is gunstig (< 20).")
        elif pe > 40: 
            risks.append(f"De koers/winst verhouding is aan de hoge kant (> 40).")
        else: score += 0.5

    # ROE
    roe = metrics.get('roe')
    if roe:
        count += 1
        if roe > 0.15: 
            score += 1; reasons.append(f"Het management behaalt een uitstekend rendement op eigen vermogen ({roe:.1%}).")
        elif roe < 0.05:
            risks.append(f"Het rendement op eigen vermogen is aan de lage kant ({roe:.1%}).")
        else: score += 0.5

    # Schuld (D/E)
    de = metrics.get('de')
    if de:
        count += 1
        if de < 1.0: 
            score += 1; reasons.append("De balans is zeer gezond met weinig schulden.")
        elif de > 2.0:
            risks.append(f"De schuldenlast is fors (Debt/Equity: {de:.2f}), dit brengt risico met zich mee.")
        else: score += 0.5

    # 2. Bereken Basis Score
    final_score = (score / count * 10) if count > 0 else 5.0
    
    # 3. Penalty Logic
    # Penalty voor weinig data
    if count < 4:
        final_score *= 0.8
        risks.append("Let op: Er was weinig data beschikbaar, dus deze score is minder betrouwbaar.")

    # Penalty voor Verlies
    margin = metrics.get('profit_margin')
    if (eps and eps < 0) or (margin and margin < 0):
        final_score -= 3.0
        risks.insert(0, "Het bedrijf maakt momenteel verlies, wat een groot risico is.")
    
    # Cap de score
    if final_score > 10: final_score = 10
    if final_score < 1: final_score = 1
    
    return round(final_score, 1), reasons, risks

def search_yahoo_strict(query):
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10"
        r = requests.get(url, headers=HEADERS, timeout=3)
        qs = r.json().get('quotes', [])
        res = []
        for q in qs:
            res.append(f"{q['symbol']} | {q.get('shortname','')}")
        return res
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

# --- DATA MANAGER ---
def handle_upload():
    f = st.session_state.get('uploader')
    if f:
        try:
            f.seek(0)
            d = json.load(f)
            if isinstance(d, dict):
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
if st.sidebar.button("🧹 Cache Legen (Reset)"): st.cache_data.clear(); st.rerun()

tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist", "🇳🇱 Belasting"])

# ==================================================
# 1. ANALYSE (VERBETERD)
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
            info = tick.info
            hist = tick.history(period=per)
            
            if hist.empty: st.error("Geen koersdata gevonden.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price = hist['Close'].iloc[-1] * rate
                
                # Bepaal US ticker voor Finviz
                us_mt = sel
                if sel in EU_TO_US_MAP: us_mt = EU_TO_US_MAP[sel]
                elif "." in sel:
                    sn = safe_get(info, 'shortName', '').split(' ')[0]
                    found = search_yahoo_strict(sn)
                    if found and "." not in found[0].split(" | ")[0]:
                        us_mt = found[0].split(" | ")[0]

                # Scrapen
                with st.spinner(f"Data verzamelen en gemiddelden berekenen..."):
                    data, sources = get_averaged_data(sel, us_mt if us_mt != sel else None, info)
                
                # AI Score met volzinnen
                score, reasons, risks = generate_ai_analysis(data, data.get('eps'))

                # --- Header ---
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
                        st.metric("Doel (Avg)", f"€ {tgt_eur:.2f}", delta=f"{upside:+.1f}%", help=f"Gemiddelde van: {sources.get('target')}")
                    else: st.metric("Doel", "-")
                with c4:
                    with st.popover("➕ Acties"):
                        with st.form("add"):
                            n = st.number_input("Aantal", 1.0)
                            p = st.number_input("GAK", value=float(round(price, 2)))
                            if st.form_submit_button("Toevoegen Portfolio"):
                                st.session_state['portfolio'].append({'ticker': sel, 'amount': n, 'avg_price_eur': p})
                                st.rerun()
                        if st.button("Aan Wishlist toevoegen"):
                            st.session_state['wishlist'].append({'ticker': sel, 'name': safe_get(info, 'shortName')})
                            st.rerun()

                # --- AI Oordeel (NIEUW DESIGN) ---
                if score >= 7:
                    container_type = st.success
                    ai_icon = "🚀"
                elif score >= 5.5:
                    container_type = st.warning
                    ai_icon = "⚖️"
                else:
                    container_type = st.error
                    ai_icon = "🐻"

                st.markdown("### 🤖 AI Analyse")
                with st.container(border=True):
                    col_icon, col_txt = st.columns([1, 6])
                    with col_icon:
                        st.markdown(f"<h1 style='text-align: center; font-size: 50px;'>{score}</h1>", unsafe_allow_html=True)
                        st.markdown(f"<p style='text-align: center;'>{ai_icon}</p>", unsafe_allow_html=True)
                    with col_txt:
                        if reasons:
                            st.markdown("**Pluspunten:**")
                            for r in reasons: st.markdown(f"- {r}")
                        else:
                            st.write("Geen uitgesproken sterke punten gevonden op basis van deze data.")
                        
                        if risks:
                            st.divider()
                            st.markdown("**Aandachtspunten & Risico's:**")
                            for r in risks: st.markdown(f"- {r}")
                        else:
                            st.write("Geen grote rode vlaggen gevonden in de fundamentele cijfers.")

                # --- Metrics Grid (MET PERCENTAGES & BRONNEN) ---
                st.markdown("### 📊 Fundamentele Cijfers")
                
                m_col1 = st.columns(4)
                keys1 = ['peg', 'fwd_pe', 'roe', 'div_yield']
                lbls1 = ['PEG Ratio', 'Fwd P/E', 'ROE', 'Dividend']
                
                for i, (k, l) in enumerate(zip(keys1, lbls1)):
                    v = data.get(k)
                    src = sources.get(k, "?")
                    
                    # Formatting logic
                    if v is not None:
                        if k in ['roe', 'div_yield']: 
                            display_val = f"{v*100:.2f}%" # Percentage
                        else: 
                            display_val = f"{v:.2f}"      # Normaal getal
                        
                        # Kleurtjes
                        c = "⚪"
                        if k=='peg': c="🟢" if 0<v<1.5 else "🟠"
                        if k=='fwd_pe': c="🟢" if 0<v<20 else "🟠"
                        if k=='roe': c="🟢" if v>0.15 else "🟠"
                    else:
                        display_val = "-"
                        c = ""

                    with m_col1[i]:
                        st.metric(l, f"{c} {display_val}", help=TOOLTIPS.get(k, "Geen uitleg."))
                        st.caption(f"Bron: {src}") # EXPLICIETE BRON VERMELDING

                m_col2 = st.columns(4)
                keys2 = ['cr', 'de', 'pb', 'profit_margin']
                lbls2 = ['Current Ratio', 'Debt/Equity', 'P/B', 'Winstmarge']
                
                for i, (k, l) in enumerate(zip(keys2, lbls2)):
                    v = data.get(k)
                    src = sources.get(k, "?")
                    
                    if v is not None:
                        if k == 'profit_margin': 
                            display_val = f"{v*100:.2f}%" # Percentage
                        else: 
                            display_val = f"{v:.2f}"
                        
                        c = "⚪"
                        if k=='cr': c="🟢" if v>1.5 else "🟠"
                        if k=='profit_margin': c="🟢" if v>0.2 else "🟠"
                    else:
                        display_val = "-"
                        c = ""
                    
                    with m_col2[i]:
                        st.metric(l, f"{c} {display_val}", help=TOOLTIPS.get(k, "Geen uitleg."))
                        st.caption(f"Bron: {src}") # EXPLICIETE BRON VERMELDING

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
        if st.button("Wishlist Legen", key="wl_clr"): st.session_state['wishlist'] = []; st.rerun()
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
