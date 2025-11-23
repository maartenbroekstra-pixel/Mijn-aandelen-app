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

# --- API KEYS & CONSTANTEN ---
FMP_KEY = "nhXfJ0SDaXmUEVKy5icfcwzQOHqOn5QE"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Mapping: EU Ticker -> US Ticker (FMP werkt het best met US tickers)
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD"
}

TOOLTIPS = {
    "peg": "Price/Earnings to Growth (5jr).\n🟢 < 1.5: Ondergewaardeerd.\n🔴 Negatief: Krimp.\n🟠 > 2.5: Duur.",
    "pe": "Koers/Winst verhouding.\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Premie.",
    "roe": "Rendement op Eigen Vermogen.\n🟢 > 15%: Sterk management.",
    "de": "Debt/Equity Ratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3.0: Redelijk.",
    "div": "Dividend Rendement.\n🟢 > 3%: Hoog inkomen.",
    "margin": "Nettowinstmarge.\n🟢 > 20%: Zeer winstgevend.",
    "eps": "Winst per aandeel (Earnings Per Share).\n🟢 Positief: Winst.\n🔴 Negatief: Verlies.",
    "target": "Koersdoel analisten."
}

# --- STATE MANAGEMENT ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []

# --- DATA ENGINE ---

class DataEngine:
    def __init__(self, ticker, original_ticker):
        self.ticker = ticker  # US Ticker (voor FMP)
        self.original_ticker = original_ticker # Originele (voor Yahoo Charts/Euro)
        self.yf_obj = yf.Ticker(original_ticker)
        self.yf_info = self.yf_obj.info
        self.fmp_ratios = {}
        self.fmp_quote = {}
        self.fetch_fmp()

    def fetch_fmp(self):
        """Haalt data op van Financial Modeling Prep"""
        try:
            # 1. Ratios (TTM)
            url_ratios = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{self.ticker}?apikey={FMP_KEY}"
            r_ratios = requests.get(url_ratios, timeout=3).json()
            if r_ratios and isinstance(r_ratios, list):
                self.fmp_ratios = r_ratios[0]

            # 2. Quote (Live price, EPS, PE)
            url_quote = f"https://financialmodelingprep.com/api/v3/quote/{self.ticker}?apikey={FMP_KEY}"
            r_quote = requests.get(url_quote, timeout=3).json()
            if r_quote and isinstance(r_quote, list):
                self.fmp_quote = r_quote[0]
        except Exception as e:
            print(f"FMP Error: {e}")

    def get_combined_metric(self, key_yf, key_fmp_ratio, key_fmp_quote=None, is_percent=False):
        """
        Haalt waarden op uit Yahoo en FMP, en berekent het gemiddelde.
        Handelt N/A af en normaliseert percentages.
        """
        val_yf = self.yf_info.get(key_yf)
        
        # Probeer eerst ratio endpoint, dan quote endpoint voor FMP
        val_fmp = self.fmp_ratios.get(key_fmp_ratio)
        if val_fmp is None and key_fmp_quote:
            val_fmp = self.fmp_quote.get(key_fmp_quote)

        # Opschonen waarden (None check)
        v1 = float(val_yf) if val_yf is not None else None
        v2 = float(val_fmp) if val_fmp is not None else None

        # Bron bepaling
        source = "N/A"
        final_val = None

        if v1 is not None and v2 is not None:
            final_val = (v1 + v2) / 2
            source = "Yahoo & FMP (Avg)"
        elif v1 is not None:
            final_val = v1
            source = "Yahoo Only"
        elif v2 is not None:
            final_val = v2
            source = "FMP Only"

        # Percentage Check:
        # FMP geeft 0.15 voor 15%, Yahoo geeft soms 0.15, soms n.v.t.
        # We gaan ervan uit dat raw data < 5.0 waarschijnlijk een factor is (dus 0.70 = 70%) voor marges/ROE.
        # Voor PE/PEG/EPS doen we dit niet.
        if is_percent and final_val is not None:
            # Veiligheidscheck: als waarde > 10 (bijv 20%), dan is het waarschijnlijk al geschaald door een provider
            # Maar FMP en Yahoo zijn meestal decimalen (0.x). 
            pass 

        return final_val, source

# --- HULPFUNCTIES ---

@st.cache_data(ttl=300)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD" and rate > 1.05: return 1/rate # Sanity
            return rate
        return 1.0
    except: return 1.0

def search_yahoo_strict(query):
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=DE&quotesCount=10"
        r = requests.get(url, headers=HEADERS, timeout=3)
        d = r.json()
        res = []
        if 'quotes' in d:
            for q in d['quotes']:
                res.append(f"{q['symbol']} | {q.get('shortname','')}")
        return res
    except: return []

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    try:
        url = f"https://www.youtube.com/results?search_query={query.replace(' ','+')}+stock+analysis+{datetime.now().year}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except: return None

def get_google_news(query):
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={query}+stock&hl=nl&gl=NL&ceid=NL:nl", timeout=4)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:3]:
            d = i.find('pubDate').text[:16]
            items.append({"title": i.find('title').text, "link": i.find('link').text, "date": d})
        return items
    except: return []

# --- AI SCORE LOGICA ---
def calculate_ai_score(data):
    score, count = 0, 0
    opps, risks = [], []
    
    # metrics: val, metric_name, good_condition, bad_condition
    rules = [
        (data['peg'][0], "PEG", lambda x: 0 < x < 1.5, lambda x: x < 0 or x > 3.0),
        (data['pe'][0], "P/E", lambda x: 0 < x < 20, lambda x: x > 40),
        (data['roe'][0], "ROE", lambda x: x > 0.15, lambda x: x < 0.05),
        (data['de'][0], "D/E", lambda x: x < 1.0, lambda x: x > 2.0),
        (data['cr'][0], "Current", lambda x: x > 1.5, lambda x: x < 0.9),
        (data['margin'][0], "Marge", lambda x: x > 0.20, lambda x: x < 0.05),
        (data['eps'][0], "EPS", lambda x: x > 0, lambda x: x < 0) # Basic EPS check
    ]
    
    for val, name, good, bad in rules:
        if val is not None:
            count += 1
            if good(val): 
                score += 1; opps.append(f"{name}")
            elif bad(val):
                risks.append(f"{name}")
            else:
                score += 0.5
            
    final = (score/count)*10 if count > 0 else 5.0
    return round(final, 1), opps, risks

# --- UI SETUP ---
st.sidebar.header("Data Beheer")
st.sidebar.download_button("⬇️ Download Data", json.dumps({'p': st.session_state['portfolio'], 'w': st.session_state['wishlist']}), "data.json")
uploaded_file = st.sidebar.file_uploader("⬆️ Upload Data", type=['json'])
if uploaded_file:
    d = json.load(uploaded_file)
    st.session_state['portfolio'] = d.get('p', [])
    st.session_state['wishlist'] = d.get('w', [])
    st.toast("Data geladen!")

if st.sidebar.button("🧹 Cache Legen"): st.cache_data.clear(); st.rerun()

tabs = st.tabs(["🔎 Deep Dive Analyse", "📊 Mijn Portfolio", "❤️ Wishlist", "🇳🇱 Belasting Tool"])

# ==================================================
# 1. ANALYSE (VISUALISATIE UPDATE)
# ==================================================
with tabs[0]:
    c_search, c_per = st.columns([3, 1])
    with c_search:
        dq = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek aandeel:", value=dq, placeholder="Bijv: ASML, TSLA, SHELL...", key="search_main")
        sel = None
        if txt:
            if "." in txt and " " not in txt: sel = txt
            else:
                res = search_yahoo_strict(txt)
                if res: sel = st.selectbox("Selecteer:", res).split(" | ")[0]
                else: sel = txt.upper()
    with c_per:
        per = st.selectbox("Grafiek Periode", ["1mo", "6mo", "1y", "5y", "max"], index=2)

    if sel:
        try:
            # --- INTELLIGENT TICKER MAPPING ---
            us_ticker = sel
            if sel in EU_TO_US_MAP: 
                us_ticker = EU_TO_US_MAP[sel]
            elif "." in sel:
                # Probeer suffix te strippen voor FMP als fallback
                base = sel.split('.')[0]
                # Simpele check of base werkt, anders behoud sel
                us_ticker = base 
            
            # --- DATA OPHALEN ---
            engine = DataEngine(us_ticker, sel)
            info = engine.yf_info
            
            # --- METRICS BEREKENEN (Yahoo + FMP Gemiddelde) ---
            # Structuur: (YahooKey, FMPRatioKey, FMPQuoteKey, IsPercentage)
            d = {}
            d['peg'] = engine.get_combined_metric('pegRatio', 'pegRatioTTM')
            d['pe'] = engine.get_combined_metric('trailingPE', 'peRatioTTM', 'pe') # Yahoo trailing vs FMP TTM
            d['roe'] = engine.get_combined_metric('returnOnEquity', 'returnOnEquityTTM', is_percent=True)
            d['de'] = engine.get_combined_metric('debtToEquity', 'debtEquityRatioTTM')
            # Fix voor D/E: Yahoo geeft soms 150 (%), FMP 1.5. Normaliseren naar decimaal
            if d['de'][0] and d['de'][0] > 10: d['de'] = (d['de'][0]/100, d['de'][1])

            d['cr'] = engine.get_combined_metric('currentRatio', 'currentRatioTTM')
            d['pb'] = engine.get_combined_metric('priceToBook', 'priceToBookRatioTTM')
            d['margin'] = engine.get_combined_metric('profitMargins', 'netProfitMarginTTM', is_percent=True)
            d['eps'] = engine.get_combined_metric('trailingEps', 'netIncomePerShareTTM', 'eps')
            
            # Dividend is tricky, yahoo is %, FMP is vaak raw. We pakken Yahoo leading of FMP dividendYielTTM
            d['div'] = engine.get_combined_metric('dividendYield', 'dividendYielTTM', is_percent=True)
            
            # Target
            tgt_val = info.get('targetMeanPrice')
            cur_price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
            
            # AI Score
            score, opps, risks = calculate_ai_score(d)

            # --- HEADER ---
            with st.container(border=True):
                col_head1, col_head2, col_head3 = st.columns([2, 1, 1])
                with col_head1:
                    st.title(f"{info.get('shortName', sel)}")
                    st.caption(f"Ticker: {sel} | Data Source Ticker: {us_ticker}")
                with col_head2:
                    curr = info.get('currency', 'EUR')
                    price_display = f"{info.get('currencySymbol', '€')}{cur_price:.2f}" if cur_price else "N/A"
                    st.metric("Huidige Koers", price_display)
                with col_head3:
                     # Gauge Chart voor Score
                    fig_g = go.Figure(go.Indicator(
                        mode = "gauge+number",
                        value = score,
                        domain = {'x': [0, 1], 'y': [0, 1]},
                        title = {'text': "AI Score"},
                        gauge = {
                            'axis': {'range': [0, 10]},
                            'bar': {'color': "white"},
                            'steps': [
                                {'range': [0, 5], 'color': "#ff4b4b"},
                                {'range': [5, 7], 'color': "#ffa500"},
                                {'range': [7, 10], 'color': "#09ab3b"}],
                        }))
                    fig_g.update_layout(height=130, margin=dict(l=20,r=20,t=30,b=20))
                    st.plotly_chart(fig_g, use_container_width=True)

            # --- METRICS GRID ---
            st.subheader("📊 Financiële Gezondheid")
            
            def show_metric_card(label, key, fmt="{:.2f}", is_pct=False):
                val, src = d[key]
                if val is None:
                    st.metric(label, "N/A", help=f"Geen data gevonden in Yahoo of FMP.\n{TOOLTIPS.get(key, '')}")
                    return

                # Kleur bepaling
                color = "off" # default
                if key == 'peg': color = "normal" if 0 < val < 1.5 else "inverse"
                if key == 'eps': color = "normal" if val > 0 else "inverse" # Groen bij winst
                
                # Delta simulatie (kleurcode via delta is beperkt, we gebruiken formatting)
                display_val = fmt.format(val)
                if is_pct: display_val = f"{val:.1%}" # Zorgt voor 0.73 -> 73.0%
                
                # Custom Styling trick via help of delta
                st.metric(label, display_val, help=f"Waarde: {val}\nBron: {src}\n\n{TOOLTIPS.get(key, '')}")

            with st.container(border=True):
                r1c1, r1c2, r1c3, r1c4 = st.columns(4)
                with r1c1: show_metric_card("Koers/Winst (P/E)", 'pe')
                with r1c2: show_metric_card("PEG Ratio", 'peg')
                with r1c3: show_metric_card("Winst/Aandeel (EPS)", 'eps')
                with r1c4: 
                    # Target logic
                    if tgt_val and cur_price:
                        upside = (tgt_val - cur_price) / cur_price
                        st.metric("Koersdoel", f"{tgt_val:.2f}", f"{upside:.1%}")
                    else: st.metric("Koersdoel", "N/A")

                r2c1, r2c2, r2c3, r2c4 = st.columns(4)
                with r2c1: show_metric_card("Winstmarge", 'margin', is_pct=True)
                with r2c2: show_metric_card("ROE", 'roe', is_pct=True)
                with r2c3: show_metric_card("Dividend", 'div', is_pct=True)
                with r2c4: show_metric_card("P/B Ratio", 'pb')

                r3c1, r3c2, r3c3, r3c4 = st.columns(4)
                with r3c1: show_metric_card("Liquiditeit (CR)", 'cr')
                with r3c2: show_metric_card("Schuld (D/E)", 'de')
                with r3c3: 
                    # Korte sterktes/zwaktes tekst
                    st.info(f"✅: {', '.join(opps[:2])}" if opps else "Geen uitschieters")
                with r3c4:
                    st.warning(f"⚠️: {', '.join(risks[:2])}" if risks else "Veilig")

            # --- GRAFIEK & NIEUWS ---
            c_chart, c_extra = st.columns([2, 1])
            with c_chart:
                hist = engine.yf_obj.history(period=per)
                if not hist.empty:
                    fig = px.area(hist, x=hist.index, y='Close', title=f"Koersverloop ({per})")
                    fig.update_layout(xaxis_title="", yaxis_title="Prijs", showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.error("Geen grafiek data beschikbaar.")
            
            with c_extra:
                tab_news, tab_vid = st.tabs(["Nieuws", "Video"])
                with tab_news:
                    news = get_google_news(info.get('shortName', sel))
                    if news:
                        for n in news:
                            st.markdown(f"🔹 [{n['title']}]({n['link']})\n*{n['date']}*")
                    else: st.write("Geen recent nieuws.")
                with tab_vid:
                    vid_id = get_youtube_video_id(sel)
                    if vid_id: st.video(f"https://www.youtube.com/watch?v={vid_id}")
                    else: st.write("Geen video gevonden.")
            
            # --- KNOPPEN ---
            c_btn1, c_btn2 = st.columns(2)
            with c_btn1:
                if st.button("➕ Voeg toe aan Portfolio", use_container_width=True):
                    st.session_state['portfolio'].append({'ticker': sel, 'amount': 10, 'avg_price_eur': cur_price if cur_price else 0})
                    st.success("Toegevoegd!")
            with c_btn2:
                if st.button("❤️ Zet op Wishlist", use_container_width=True):
                    st.session_state['wishlist'].append({'ticker': sel, 'name': info.get('shortName', sel)})
                    st.success("Toegevoegd!")

        except Exception as e:
            st.error(f"Er ging iets mis bij het ophalen van data: {e}")
            import traceback
            st.code(traceback.format_exc())

# ==================================================
# 2. PORTFOLIO
# ==================================================
with tabs[1]:
    if not st.session_state['portfolio']:
        st.info("Je portfolio is leeg. Voeg aandelen toe via de Analyse tab.")
    else:
        # Dataframe bouwen
        p_data = []
        total_val = 0
        total_cost = 0
        
        for item in st.session_state['portfolio']:
            try:
                t = item['ticker']
                cur_p = yf.Ticker(t).fast_info['last_price']
                val = cur_p * item['amount']
                cost = item['avg_price_eur'] * item['amount']
                
                total_val += val
                total_cost += cost
                
                p_data.append({
                    "Ticker": t,
                    "Aantal": item['amount'],
                    "GAK": f"€{item['avg_price_eur']:.2f}",
                    "Huidig": f"€{cur_p:.2f}",
                    "Waarde": val,
                    "Rendement": val - cost
                })
            except: pass
            
        df = pd.DataFrame(p_data)
        
        # KPI's
        k1, k2, k3 = st.columns(3)
        k1.metric("Totale Waarde", f"€ {total_val:,.2f}")
        profit = total_val - total_cost
        k2.metric("Totaal Rendement", f"€ {profit:,.2f}", delta=f"{(profit/total_cost)*100:.1f}%" if total_cost else "0%")
        k3.metric("Aantal Posities", len(df))
        
        # Tabel styling
        st.dataframe(
            df.style.format({"Waarde": "€{:.2f}", "Rendement": "€{:.2f}"}), 
            use_container_width=True, 
            hide_index=True
        )
        
        if st.button("Portfolio Wissen"):
            st.session_state['portfolio'] = []
            st.rerun()

# ==================================================
# 3. WISHLIST
# ==================================================
with tabs[2]:
    if st.session_state['wishlist']:
        for w in st.session_state['wishlist']:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{w['name']}** ({w['ticker']})")
                if c2.button("🔎", key=f"s_{w['ticker']}"):
                    st.session_state['search_query'] = w['ticker']
                    st.rerun()
    else:
        st.info("Nog geen favorieten.")

# ==================================================
# 4. BELASTING
# ==================================================
with tabs[3]:
    st.markdown("### 🇳🇱 Box 3 Calculator")
    col_tax1, col_tax2 = st.columns(2)
    with col_tax1:
        year = st.selectbox("Belastingjaar", [2024, 2025])
        fictief = 6.04 if year == 2024 else 5.88
        tax_rate = 36
        st.write(f"*Fictief rendement: {fictief}% | Tarief: {tax_rate}%*")
        
    with col_tax2:
        vermogen = st.number_input("Totaal Vermogen (1 jan)", value=50000.0)
        vrijstelling = st.number_input("Heffingsvrij Vermogen", value=57000.0)
    
    grondslag = max(0, vermogen - vrijstelling)
    rendement = grondslag * (fictief / 100)
    te_betalen = rendement * (tax_rate / 100)
    
    st.divider()
    st.metric("Te betalen belasting", f"€ {te_betalen:.2f}")
    st.caption("Dit is een schatting o.b.v. beleggingen (overige bezittingen).")
