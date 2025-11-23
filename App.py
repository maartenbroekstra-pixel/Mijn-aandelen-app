import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# ==================================================
# 1. CONFIGURATIE & CONSTANTEN
# ==================================================
st.set_page_config(
    page_title="Aandelen Dashboard Pro", 
    layout="wide", 
    page_icon="📈"
)

# API Key voor Financial Modeling Prep (FMP)
FMP_KEY = "nhXfJ0SDaXmUEVKy5icfcwzQOHqOn5QE"

# Headers zijn ESSENTIEEL om 403 Forbidden errors te voorkomen bij FMP en Yahoo
# Veel API's blokkeren requests zonder 'User-Agent'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Mapping van Europese tickers naar US tickers (FMP heeft betere data voor US tickers)
# Dit zorgt ervoor dat we voor bijvoorbeeld Shell (SHELL.AS) de data van (SHEL) ophalen indien nodig
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD"
}

# Tooltips voor de metrics in de Analyse tab om de gebruiker uitleg te geven
TOOLTIPS = {
    "peg": "Price/Earnings to Growth (PEG).\n🟢 < 1.5: Ondergewaardeerd.\n🔴 > 2.5: Overgewaardeerd.",
    "pe": "Koers/Winst verhouding (P/E).\n🟢 < 20: Koopje.\n🟠 > 30: Premie.",
    "roe": "Rendement op Eigen Vermogen (ROE).\n🟢 > 15%: Sterk management.",
    "de": "Schuldratio (Debt/Equity).\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "div": "Dividend Rendement.\n🟢 > 3%: Interessant.",
    "margin": "Nettowinstmarge.\n🟢 > 20%: Hoge winstmarge.",
    "eps": "Winst per aandeel (EPS).\n🟢 > 0: Winstgevend.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3.0: Redelijk."
}

# ==================================================
# 2. STATE MANAGEMENT
# ==================================================
# Initialiseer de opslag als deze nog niet bestaat in de sessie
if 'portfolio' not in st.session_state: 
    st.session_state['portfolio'] = []

if 'wishlist' not in st.session_state: 
    st.session_state['wishlist'] = []

# ==================================================
# 3. HELPER FUNCTIES (SCRAPERS & TOOLS)
# ==================================================

@st.cache_data(ttl=600)
def get_finviz_data(ticker):
    """
    Scrapet Finviz.com voor extra targets en metrics (vooral US aandelen).
    """
    d = {}
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        # Gebruik headers om te voorkomen dat Finviz ons blokkeert
        r = requests.get(url, headers=HEADERS, timeout=4)
        if r.status_code == 200:
            def parse(label):
                # Regex om de waarde uit de HTML tabel te halen
                m = re.search(rf'>{label}</td>.*?<b>\s*([\d\.-]+)\s*</b>', r.text, re.DOTALL)
                return float(m.group(1)) if m and m.group(1) != '-' else None
            
            d['peg'] = parse('PEG')
            d['target'] = parse('Target Price')
            d['eps'] = parse('EPS \(ttm\)')
            d['pe'] = parse('P/E')
    except Exception: 
        pass
    return d

@st.cache_data(ttl=300)
def get_currency_rate(from_curr, to_curr="EUR"):
    """
    Haalt de actuele wisselkoers op via Yahoo Finance.
    """
    if from_curr == to_curr or not from_curr: 
        return 1.0
    try:
        # Yahoo ticker format: EURUSD=X betekent 1 EUR = x USD
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            # Als we USD naar EUR willen en we hebben EURUSD=X (bijv 1.05), 
            # dan is de factor 1 / 1.05
            if from_curr == "USD": 
                return 1.0 / rate 
            return rate
        return 1.0
    except Exception: 
        return 1.0

def search_yahoo_strict(query):
    """
    Zoekt aandelen via Yahoo Finance API en sorteert op regio (EU eerst).
    """
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=DE&quotesCount=15"
        r = requests.get(url, headers=HEADERS, timeout=3)
        data = r.json()
        results = []
        raw_quotes = data.get('quotes', [])
        
        # Sorteer sleutel: .AS (0) -> .DE/.F (1) -> Andere EU (2) -> US (10) -> Overig (20)
        def sort_key(q):
            s = q.get('symbol', '')
            if s.endswith('.AS'): return 0
            if s.endswith('.DE') or s.endswith('.F'): return 1
            if any(s.endswith(x) for x in ['.PA', '.BR', '.MI', '.MC', '.LS']): return 2
            if '.' not in s: return 10 
            return 20 

        raw_quotes.sort(key=sort_key)
        
        for q in raw_quotes:
            s = q.get('symbol')
            n = q.get('shortname') or q.get('longname') or s
            results.append(f"{s} | {n}")
        return results
    except Exception: 
        return []

def get_google_news(query):
    """
    Haalt recent nieuws op via Google RSS feed.
    """
    try:
        url = f"https://news.google.com/rss/search?q={query}+stock&hl=nl&gl=NL&ceid=NL:nl"
        r = requests.get(url, timeout=4)
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:4]:
            d = i.find('pubDate').text[:16]
            items.append({"title": i.find('title').text, "link": i.find('link').text, "date": d})
        return items
    except Exception: 
        return []

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    """
    Zoekt een relevante analyse video op YouTube.
    """
    try:
        q = f"{query} stock analysis {datetime.now().year}"
        url = f"https://www.youtube.com/results?search_query={q.replace(' ','+')}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        # Regex om video ID te vinden in HTML
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except Exception: 
        return None

# ==================================================
# 4. DATA ENGINE (INTEGRATIE YAHOO & FMP)
# ==================================================
class DataEngine:
    def __init__(self, ticker, original_ticker):
        self.ticker = ticker
        self.original_ticker = original_ticker
        self.yf_obj = yf.Ticker(original_ticker)
        self.yf_info = self.yf_obj.info
        self.fmp_ratios = {}
        self.fmp_quote = {}
        self.finviz_data = {}
        
        # Haal data op bij initialisatie
        self.fetch_fmp()
        self.fetch_finviz()

    def fetch_fmp(self):
        """
        Haalt data op van FMP API.
        BELANGRIJK: Gebruikt headers om 403 fouten te voorkomen.
        """
        if not FMP_KEY: return
        try:
            # Ratios Endpoint
            url_ratios = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{self.ticker}?apikey={FMP_KEY}"
            r = requests.get(url_ratios, headers=HEADERS, timeout=3).json()
            if isinstance(r, list) and r: 
                self.fmp_ratios = r[0]

            # Quote Endpoint
            url_quote = f"https://financialmodelingprep.com/api/v3/quote/{self.ticker}?apikey={FMP_KEY}"
            q = requests.get(url_quote, headers=HEADERS, timeout=3).json()
            if isinstance(q, list) and q: 
                self.fmp_quote = q[0]
        except Exception: 
            pass

    def fetch_finviz(self):
        if "." not in self.ticker:
            self.finviz_data = get_finviz_data(self.ticker)

    def get_combined_metric(self, key_yf, key_fmp_ratio, key_fmp_quote=None, key_finviz=None, is_percent=False):
        """
        Combineert metrics van verschillende bronnen (Yahoo, FMP, Finviz).
        Geeft het gemiddelde en de bron terug.
        """
        values = []
        sources = []
        
        # 1. Yahoo Finance Check
        v_yf = self.yf_info.get(key_yf)
        if v_yf is not None: 
            values.append(float(v_yf)); sources.append("Yahoo")
        
        # 2. FMP Check
        v_fmp = self.fmp_ratios.get(key_fmp_ratio)
        if v_fmp is None and key_fmp_quote: 
            v_fmp = self.fmp_quote.get(key_fmp_quote)
        if v_fmp is not None: 
            values.append(float(v_fmp)); sources.append("FMP")
        
        # 3. Finviz Check
        if key_finviz:
            v_fin = self.finviz_data.get(key_finviz)
            if v_fin is not None: 
                values.append(float(v_fin)); sources.append("Finviz")

        if not values: return None, "N/A"
        
        avg_val = sum(values) / len(values)
        
        # Percentage Correctie Logica
        # Zorgt dat 0.05 wordt weergegeven als 5% en 5.0 ook als 5%
        if is_percent:
            if avg_val < 0.25: 
                avg_val *= 100
        
        return avg_val, " & ".join(sources)

# ==================================================
# 5. AI SCORE LOGICA
# ==================================================
def calculate_ai_score(data):
    """
    Berekent een score (0-10) op basis van fundamentele indicatoren.
    """
    score = 0
    count = 0
    reasons = []
    
    rules = [
        (data['peg'][0], "PEG", lambda x: 0 < x < 1.5, lambda x: x > 2.5, "Goedkoop (PEG)", "Te duur (PEG)"),
        (data['pe'][0], "P/E", lambda x: 0 < x < 20, lambda x: x > 40, "Aantrekkelijke P/E", "Hoge premie (P/E)"),
        (data['roe'][0], "ROE", lambda x: x > 15, lambda x: x < 5, "Sterk rendement (ROE)", "Zwak management (ROE)"),
        (data['de'][0], "D/E", lambda x: x < 1.0, lambda x: x > 2.0, "Gezonde balans", "Veel schulden"),
        (data['cr'][0], "Current", lambda x: x > 1.5, lambda x: x < 0.9, "Goede liquiditeit", "Liquiditeitsproblemen?"),
        (data['margin'][0], "Marge", lambda x: x > 15, lambda x: x < 2, "Hoge winstmarge", "Dunne marges"),
        (data['eps'][0], "EPS", lambda x: x > 0, lambda x: x < 0, "Winstgevend", "Verlieslatend")
    ]
    
    for val, name, good, bad, g_msg, b_msg in rules:
        if val is not None:
            count += 1
            if good(val): 
                score += 1; reasons.append(g_msg)
            elif bad(val): 
                reasons.append(b_msg)
            else: 
                score += 0.5 
            
    final_score = (score / count) * 10 if count > 0 else 5.0
    summary = "Het bedrijf scoort gemiddeld."
    if final_score >= 7.5: summary = f"Sterk aandeel! ({', '.join(reasons[:2]).lower()})"
    elif final_score <= 4.5: summary = f"Risicovol. ({', '.join(reasons[:2]).lower()})"
    elif reasons: summary = f"Gemengd beeld: {reasons[0].lower()} maar {reasons[-1].lower()}."
    return round(final_score, 1), summary

# ==================================================
# 6. SIDEBAR (BACKUP & DATA)
# ==================================================
st.sidebar.header("📂 Data & Backup")

# Download Backup
json_export = json.dumps({'p': st.session_state['portfolio'], 'w': st.session_state['wishlist']})
st.sidebar.download_button(
    label="⬇️ Download Backup",
    data=json_export,
    file_name="aandelen_backup.json",
    mime="application/json"
)

# Upload Backup
uploaded_file = st.sidebar.file_uploader("⬆️ Upload Backup", type=['json'])
if uploaded_file:
    try:
        ld = json.load(uploaded_file)
        # Laadt zowel portfolio als wishlist correct in
        if 'p' in ld: st.session_state['portfolio'] = ld['p']
        if 'w' in ld: st.session_state['wishlist'] = ld['w']
        st.toast("Backup succesvol geladen!", icon="✅")
    except Exception as e: 
        st.error(f"Fout bij laden: {e}")

if st.sidebar.button("🧹 Cache Legen"): 
    st.cache_data.clear()
    st.rerun()

# ==================================================
# 7. TABS & INTERFACE
# ==================================================
tabs = st.tabs(["🔎 Analyse", "📊 Portfolio Pro", "❤️ Wishlist", "🇳🇱 Belasting"])

# --------------------------------------------------
# TAB 1: ANALYSE
# --------------------------------------------------
with tabs[0]:
    c_s, c_p = st.columns([3, 1])
    with c_s:
        dq = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek aandeel (Naam of Ticker):", value=dq, key="search_main")
        sel = None
        if txt:
            if "." in txt and " " not in txt: sel = txt
            else:
                res = search_yahoo_strict(txt)
                sel = st.selectbox("Resultaten:", res).split(" | ")[0] if res else txt.upper()
    with c_p:
        per = st.selectbox("Periode", ["1mo", "6mo", "1y", "5y", "max"], index=2)

    if sel:
        try:
            us_tick = EU_TO_US_MAP.get(sel, sel.split('.')[0] if '.' in sel else sel)
            engine = DataEngine(us_tick, sel)
            info = engine.yf_info
            
            d = {}
            d['peg'] = engine.get_combined_metric('pegRatio', 'pegRatioTTM', None, 'peg')
            d['pe'] = engine.get_combined_metric('trailingPE', 'peRatioTTM', 'pe', 'pe')
            d['roe'] = engine.get_combined_metric('returnOnEquity', 'returnOnEquityTTM', None, None, True)
            d['de'] = engine.get_combined_metric('debtToEquity', 'debtEquityRatioTTM')
            d['cr'] = engine.get_combined_metric('currentRatio', 'currentRatioTTM')
            d['pb'] = engine.get_combined_metric('priceToBook', 'priceToBookRatioTTM')
            d['margin'] = engine.get_combined_metric('profitMargins', 'netProfitMarginTTM', None, None, True)
            d['eps'] = engine.get_combined_metric('trailingEps', 'netIncomePerShareTTM', 'eps', 'eps')
            d['div'] = engine.get_combined_metric('dividendYield', 'dividendYielTTM', None, None, True)
            
            curr_sym = info.get('currencySymbol', '€')
            curr_code = info.get('currency', 'EUR')
            price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
            
            tgt = info.get('targetMeanPrice')
            tgt_src = "Yahoo"
            if not tgt:
                tgt = engine.finviz_data.get('target')
                tgt_src = "Finviz"
            
            score, summary = calculate_ai_score(d)

            # --- HEADER ---
            with st.container(border=True):
                h1, h2, h3 = st.columns([2, 1, 1])
                h1.title(info.get('shortName', sel))
                h1.caption(f"Ticker: {sel} | Valuta: {curr_code}")
                h2.metric("Prijs", f"{curr_sym} {price:.2f}" if price else "-")
                if tgt and price:
                    dlt = (tgt - price) / price
                    h3.metric("Koersdoel", f"{curr_sym} {tgt:.2f}", f"{dlt:.1%}", help=f"Bron: {tgt_src}")
                else: h3.metric("Koersdoel", "N/A")
                
                st.divider()
                a1, a2 = st.columns([1, 4])
                sc = "green" if score >= 7 else "orange" if score >= 5 else "red"
                a1.markdown(f"<div style='text-align:center;color:{sc};font-size:40px;border:3px solid {sc};border-radius:50%;width:100px;height:100px;line-height:90px;margin:auto'><b>{score}</b></div>", unsafe_allow_html=True)
                a2.subheader("AI Oordeel")
                a2.write(summary)

            # --- METRICS ---
            st.subheader("📊 Fundamentals")
            def m_col(l, k, g, b, f="{:.2f}", p=False):
                v, s = d[k]
                if v is None: return st.metric(l, "N/A", help=f"Bron: {s}")
                d_sym = "✅" if g(v) else "⚠️" if b(v) else "⚪"
                disp = f"{v:.2f}%" if p else f.format(v)
                st.metric(l, disp, delta=d_sym, delta_color="off", help=f"Waarde: {v}\nBron: {s}\n{TOOLTIPS.get(k,'')}")

            with st.container(border=True):
                c1,c2,c3,c4 = st.columns(4)
                c1.empty(); m_col("P/E Ratio", 'pe', lambda x:x<20, lambda x:x>35)
                c2.empty(); m_col("PEG Ratio", 'peg', lambda x:x<1.5, lambda x:x>2.5)
                c3.empty(); m_col("EPS", 'eps', lambda x:x>0, lambda x:x<0)
                c4.empty(); m_col("Dividend", 'div', lambda x:x>3, lambda x:x<1, p=True)
                
                c5,c6,c7,c8 = st.columns(4)
                c5.empty(); m_col("ROE", 'roe', lambda x:x>15, lambda x:x<5, p=True)
                c6.empty(); m_col("Marge", 'margin', lambda x:x>15, lambda x:x<2, p=True)
                c7.empty(); m_col("Debt/Eq", 'de', lambda x:x<1, lambda x:x>2)
                c8.empty(); m_col("Current", 'cr', lambda x:x>1.5, lambda x:x<1)

            # --- GRAFIEK ---
            cg, cm = st.columns([2, 1])
            with cg:
                h = engine.yf_obj.history(period=per)
                if not h.empty:
                    f = px.area(h, x=h.index, y='Close', title=f"Koers ({per})")
                    f.update_layout(yaxis_title=f"Prijs ({curr_sym})")
                    st.plotly_chart(f, use_container_width=True)
            with cm:
                st.markdown("##### 📰 Nieuws")
                for n in get_google_news(info.get('shortName', sel)):
                    st.markdown(f"🔹 [{n['title']}]({n['link']})\n*{n['date']}*")
            
            # VIDEO
            st.markdown("##### 📺 Video")
            vid_id = get_youtube_video_id(sel)
            if vid_id: st.video(f"https://www.youtube.com/watch?v={vid_id}")

            # TOEVOEGEN (Calculator verwijderd, simpel formulier)
            st.divider()
            with st.popover("➕ Toevoegen aan Portfolio", use_container_width=True):
                st.subheader("Positie Toevoegen")
                with st.form("add"):
                    amt = st.number_input("Aantal stuks", min_value=0.0001, value=10.0, step=1.0)
                    prc = st.number_input(f"Aankoopprijs ({curr_code})", value=float(price) if price else 0.0)
                    if st.form_submit_button("Opslaan"):
                        st.session_state['portfolio'].append({'ticker':sel, 'amount':amt, 'avg_price':prc, 'currency':curr_code, 'symbol':curr_sym})
                        st.success("Toegevoegd!"); st.rerun()

            if st.button("❤️ Zet op Wishlist"):
                st.session_state['wishlist'].append({'ticker': sel, 'name': info.get('shortName', sel)})
                st.toast("Toegevoegd!")

        except Exception as e: st.error(f"Fout: {e}")

# --------------------------------------------------
# TAB 2: PORTFOLIO PRO
# --------------------------------------------------
with tabs[1]:
    if not st.session_state['portfolio']:
        st.info("Je portfolio is leeg. Voeg aandelen toe via Analyse.")
    else:
        st.markdown("### 📊 Mijn Portfolio")
        
        # Data Voorbereiden
        p_data = []
        tot_val, tot_cost, tot_div = 0, 0, 0
        sectors = {}
        
        # Loop door portfolio
        for i, item in enumerate(st.session_state['portfolio']):
            t, curr, sym = item['ticker'], item.get('currency','EUR'), item.get('symbol','€')
            try:
                tk = yf.Ticker(t); fi = tk.fast_info; inf = tk.info
                live = fi['last_price']
                sec = inf.get('sector','Onbekend')
                
                div = 0
                if 'dividendRate' in inf and inf['dividendRate']: div = inf['dividendRate']
                elif 'dividendYield' in inf and inf['dividendYield']: div = live * inf['dividendYield']
            except:
                live = item['avg_price']; sec = "Onbekend"; div = 0
            
            val_n = live * item['amount']
            cst_n = item['avg_price'] * item['amount']
            prof_n = val_n - cst_n
            div_n = div * item['amount']
            
            # Totalen omrekenen naar EUR
            rt = get_currency_rate(curr, "EUR")
            tot_val += val_n * rt
            tot_cost += cst_n * rt
            tot_div += div_n * rt
            sectors[sec] = sectors.get(sec, 0) + val_n * rt

            p_data.append({
                "Ticker": t,
                "Aantal": float(item['amount']),
                "GAK": float(item['avg_price']),
                "Prijs": live,
                "Waarde": val_n,
                "Winst/Verlies": prof_n * rt, # In EUR voor overzicht
                "Div/Jaar": div_n * rt,
                "Sector": sec,
                "Valuta": sym,
                "_currency_code": curr # Verborgen
            })
        
        # Metrics
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Totale Waarde", f"€ {tot_val:,.2f}")
        prof = tot_val - tot_cost
        pp = (prof/tot_cost*100) if tot_cost else 0
        k2.metric("Winst/Verlies", f"€ {prof:,.2f}", delta=f"{pp:.1f}%")
        k3.metric("Div/Jaar", f"€ {tot_div:,.2f}", delta="Passief")
        yp = (tot_div/tot_val*100) if tot_val else 0
        k4.metric("Yield", f"{yp:.2f}%")
        
        st.divider()
        
        # Grafieken
        c_v1, c_v2 = st.columns(2)
        with c_v1:
            st.subheader("Sectoren")
            if sectors: st.plotly_chart(px.pie(names=list(sectors.keys()), values=list(sectors.values()), hole=0.4), use_container_width=True)
        with c_v2:
            st.subheader("Dividend")
            if p_data: st.plotly_chart(px.bar(pd.DataFrame(p_data), x='Ticker', y='Div/Jaar', color='Sector'), use_container_width=True)

        # Editor & Tabel
        # HIER WAS DE FOUT: We gebruiken st.data_editor zonder .style
        st.subheader("📋 Beheer & Overzicht")
        df_src = pd.DataFrame(p_data)
        
        # Configureren van de editor
        # _currency_code zetten we op None om te verbergen (Fix voor NameError)
        edited_df = st.data_editor(
            df_src,
            column_order=("Ticker", "Aantal", "GAK", "Prijs", "Waarde", "Winst/Verlies", "Div/Jaar", "Sector", "Valuta"),
            column_config={
                "Ticker": st.column_config.TextColumn(disabled=True),
                "Aantal": st.column_config.NumberColumn(min_value=0.0001, required=True),
                "GAK": st.column_config.NumberColumn(label="GAK (Origineel)", min_value=0.0, required=True, format="%.2f"),
                "Prijs": st.column_config.NumberColumn(format="%.2f", disabled=True),
                "Waarde": st.column_config.NumberColumn(format="%.2f", disabled=True),
                "Winst/Verlies": st.column_config.NumberColumn(format="€ %.2f", disabled=True),
                "Div/Jaar": st.column_config.NumberColumn(format="€ %.2f", disabled=True),
                "Sector": st.column_config.TextColumn(disabled=True),
                "Valuta": st.column_config.TextColumn(disabled=True),
                "_currency_code": None # CORRECT VERBERGEN
            },
            hide_index=True,
            use_container_width=True,
            key="port_editor"
        )
        
        # Opslaan logic
        if not df_src.equals(edited_df):
            ns = []
            for _, r in edited_df.iterrows():
                ns.append({'ticker': r['Ticker'], 'amount': r['Aantal'], 'avg_price': r['GAK'], 'currency': r['_currency_code'], 'symbol': r['Valuta']})
            st.session_state['portfolio'] = ns
            st.rerun()

        st.markdown("---")
        with st.expander("🧮 Rente-op-Rente Calculator", expanded=True):
            [attachment_0](attachment)
            c1, c2 = st.columns([1, 2])
            with c1:
                sc = st.number_input("Start", value=float(round(tot_val,2)))
                mi = st.number_input("Mnd Inleg", value=500)
                jr = st.slider("Jaren", 1, 40, 20)
                rn = st.slider("Rendement %", 1, 15, 7)/100
            with c2:
                ys = range(1, jr+1); vs = []; inv = []
                curr = sc; tot_i = sc
                for _ in ys:
                    curr = (curr + mi*12) * (1+rn)
                    tot_i += mi*12
                    vs.append(curr); inv.append(tot_i)
                wst = curr - tot_i
                m1, m2 = st.columns(2)
                m1.metric(f"Waarde na {jr}j", f"€ {curr:,.0f}")
                m2.metric("Rente", f"€ {wst:,.0f}")
                st.plotly_chart(px.line(pd.DataFrame({"Jaar":ys, "Waarde":vs, "Inleg":inv}), x="Jaar", y=["Waarde", "Inleg"]), use_container_width=True)
        
        if st.button("⚠️ Portfolio Wissen"): st.session_state['portfolio'] = []; st.rerun()

# --------------------------------------------------
# TAB 3: WISHLIST
# --------------------------------------------------
with tabs[2]:
    st.subheader("❤️ Wishlist")
    if st.session_state['wishlist']:
        with st.container(border=True):
            for i, w in enumerate(st.session_state['wishlist']):
                t = w['ticker']
                try:
                    tk = yf.Ticker(t); fi = tk.fast_info
                    pr = fi['last_price']; prev = fi['previous_close']
                    pct = ((pr-prev)/prev)*100
                    curs = tk.info.get('currencySymbol', '€')
                    col = "green" if pct>=0 else "red"
                    
                    # Compacte layout
                    c1, c2, c3, c4 = st.columns([1, 3, 2, 0.5])
                    c1.write(f"**{t}**")
                    c2.write(w['name'])
                    c3.markdown(f"{curs} {pr:.2f} (<span style='color:{col}'>{pct:+.2f}%</span>)", unsafe_allow_html=True)
                    if c4.button("✕", key=f"del_{i}"): st.session_state['wishlist'].pop(i); st.rerun()
                except: st.error(f"Fout: {t}")
    else: st.info("Leeg.")

# --------------------------------------------------
# TAB 4: BELASTING
# --------------------------------------------------
with tabs[3]:
    st.header("🧮 Box 3 Calculator")
    c1, c2 = st.columns(2)
    with c1:
        sv = st.number_input("Vermogen 1 jan", value=50000.0, step=1000.0)
        ev = st.number_input("Vermogen 31 dec", value=55000.0, step=1000.0)
        ni = st.number_input("Netto Inleg", value=0.0)
        hv = st.number_input("Heffingsvrij", value=57000.0)
    with c2:
        fr = st.number_input("Forfaitair %", value=6.04)
        bt = st.number_input("Belasting %", value=36.0)
    
    st.divider()
    wr = (ev - sv) - ni
    wr_pct = (wr/sv*100) if sv else 0
    gs = max(0, sv - hv)
    fi = gs * (fr/100)
    tb = fi * (bt/100)
    
    res1, res2 = st.columns(2)
    with res1:
        st.metric("Werkelijk Rendement", f"€ {wr:,.2f}", f"{wr_pct:.2f}%")
    with res2:
        st.metric("Te Betalen Belasting", f"€ {tb:,.2f}", delta=f"- {tb:.2f}", delta_color="inverse")
        st.write(f"**Netto:** € {wr - tb:,.2f}")
