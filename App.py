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
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# Headers om 403 foutmeldingen bij Yahoo/Finviz te voorkomen
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Mapping van Europese tickers naar US tickers (voor data dekking bij Finviz)
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD"
}

# Tooltip teksten voor uitleg bij metrics
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
# 2. STATE MANAGEMENT & DATA OPSLAG
# ==================================================
if 'portfolio' not in st.session_state: 
    st.session_state['portfolio'] = []

if 'wishlist' not in st.session_state: 
    st.session_state['wishlist'] = []

# ==================================================
# 3. HELPER FUNCTIES (SCRAPERS & CALCULATIES)
# ==================================================

@st.cache_data(ttl=600)
def get_finviz_data(ticker):
    """
    Scrapet Finviz.com voor extra targets en metrics.
    Gebruikt headers om bot-detectie te omzeilen.
    """
    d = {}
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        r = requests.get(url, headers=HEADERS, timeout=4)
        if r.status_code == 200:
            def parse(label):
                m = re.search(rf'>{label}</td>.*?<b>\s*([\d\.-]+)\s*</b>', r.text, re.DOTALL)
                if m and m.group(1) != '-':
                    return float(m.group(1))
                return None
            
            d['peg'] = parse('PEG')
            d['target'] = parse('Target Price')
            d['eps'] = parse('EPS \(ttm\)')
            d['pe'] = parse('P/E')
            d['de'] = parse('Debt/Eq') # Fallback voor D/E
    except Exception:
        pass
    return d

@st.cache_data(ttl=300)
def get_currency_rate(from_curr, to_curr="EUR"):
    """
    Haalt de actuele wisselkoers op via Yahoo Finance.
    Handelt USD -> EUR en EUR -> USD af.
    """
    if from_curr == to_curr or not from_curr: 
        return 1.0
    try:
        # Yahoo ticker format: EURUSD=X betekent 1 EUR = x USD
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD": 
                # Als Yahoo zegt 1 EUR = 1.05 USD, dan is 1 USD = 1/1.05 EUR
                return 1.0 / rate 
            return rate
        return 1.0
    except Exception:
        return 1.0

def search_yahoo_strict(query):
    """
    Zoekt aandelen via Yahoo Finance API.
    Sorteert resultaten zodat Europese (Amsterdam/Duitsland) aandelen bovenaan staan.
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
            symbol = q.get('symbol')
            name = q.get('shortname') or q.get('longname') or symbol
            results.append(f"{symbol} | {name}")
        return results
    except Exception:
        return []

def get_google_news(query):
    """Haalt recent nieuws op via Google RSS feed."""
    try:
        url = f"https://news.google.com/rss/search?q={query}+stock&hl=nl&gl=NL&ceid=NL:nl"
        r = requests.get(url, timeout=4)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:4]:
            pub_date = i.find('pubDate').text[:16]
            items.append({
                "title": i.find('title').text,
                "link": i.find('link').text,
                "date": pub_date
            })
        return items
    except Exception:
        return []

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    """Zoekt een relevante analyse video op YouTube."""
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
# 4. DATA ENGINE CLASS (INTEGRATIE YAHOO & FINVIZ)
# ==================================================

class DataEngine:
    def __init__(self, ticker, original_ticker):
        self.ticker = ticker  # Ticker voor Finviz (vaak US)
        self.original_ticker = original_ticker # Ticker voor Yahoo (origineel)
        self.yf_obj = yf.Ticker(original_ticker)
        self.yf_info = self.yf_obj.info
        self.finviz_data = {}
        
        # Data ophalen bij initialisatie
        self.fetch_finviz()

    def fetch_finviz(self):
        """Haalt data op van Finviz als het een US aandeel is."""
        if "." not in self.ticker:
            self.finviz_data = get_finviz_data(self.ticker)

    def get_combined_metric(self, key_yf, key_finviz=None, is_percent=False, is_dividend=False, is_debt_equity=False):
        """
        Combineert datapunten van Yahoo en Finviz.
        Prioriteit ligt bij Yahoo. Finviz is backup.
        """
        values = []
        sources = []
        
        # 1. Check Yahoo
        val_yf = self.yf_info.get(key_yf)
        if val_yf is not None:
            values.append(float(val_yf))
            sources.append("Yahoo")
        
        # 2. Check Finviz (alleen als Yahoo faalt, om dubbeling te voorkomen, of als we middelen)
        if key_finviz and not values:
            val_fin = self.finviz_data.get(key_finviz)
            if val_fin is not None:
                values.append(float(val_fin))
                sources.append("Finviz")

        # Resultaat bepalen
        if not values:
            return None, "N/A"
        
        avg_val = values[0] # We pakken de primaire bron
        
        # --- CORRECTIE LOGICA ---
        
        # 1. Debt/Equity Correctie
        # Yahoo geeft D/E vaak als 150 (voor 150%). Wij willen ratio 1.5.
        if is_debt_equity:
            if avg_val > 5.0: # Als het groter is dan 5, is het waarschijnlijk een percentage
                avg_val /= 100.0

        # 2. Percentage Correctie
        elif is_percent:
            if is_dividend:
                # Dividend: < 0.6 betekent waarschijnlijk factor (0.05 = 5%), dus * 100
                if avg_val < 0.6:
                    avg_val *= 100
            else:
                # Andere percentages (ROE, Margin): < 1.0 betekent factor, > 1.0 is al procent
                if abs(avg_val) <= 1.0 and avg_val != 0:
                    avg_val *= 100
        
        source_str = " & ".join(sources)
        return avg_val, source_str

# ==================================================
# 5. AI SCORE LOGICA
# ==================================================
def calculate_ai_score(data):
    """Berekent een 0-10 score op basis van fundamentele metrics."""
    score = 0
    count = 0
    reasons = []
    
    # Lijst van regels: (Waarde, Naam, GoedConditie, SlechtConditie, GoedBericht, SlechtBericht)
    rules = [
        (data['peg'][0], "PEG", lambda x: 0 < x < 1.5, lambda x: x > 2.5, "Goedkoop gewaardeerd (PEG)", "Te duur (PEG)"),
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
                score += 1
                reasons.append(g_msg)
            elif bad(val):
                reasons.append(b_msg)
            else:
                score += 0.5 # Neutraal
            
    final_score = (score / count) * 10 if count > 0 else 5.0
    
    # Samenvatting genereren
    summary = "Het bedrijf scoort gemiddeld op basis van de fundamentals."
    if final_score >= 7.5:
        summary = f"Sterk aandeel! Positieve punten: {', '.join(reasons[:2]).lower()}."
    elif final_score <= 4.5:
        summary = f"Risicovol aandeel. Let op: {', '.join(reasons[:2]).lower()}."
    elif reasons:
        summary = f"Gemengd beeld. Plus: {reasons[0].lower()}, maar let op: {reasons[-1].lower()}."
    
    return round(final_score, 1), summary

# ==================================================
# 6. UI: SIDEBAR (BACKUP)
# ==================================================
st.sidebar.header("📂 Data & Backup")

# Download JSON
json_export = json.dumps({'p': st.session_state['portfolio'], 'w': st.session_state['wishlist']})
st.sidebar.download_button(
    label="⬇️ Download Backup",
    data=json_export,
    file_name="aandelen_backup.json",
    mime="application/json"
)

# Upload JSON
uploaded_file = st.sidebar.file_uploader("⬆️ Upload Backup", type=['json'])
if uploaded_file and st.sidebar.button("📂 Data Inladen / Herstellen"):
    try:
        loaded_data = json.load(uploaded_file)
        if 'p' in loaded_data: st.session_state['portfolio'] = loaded_data['p']
        if 'w' in loaded_data: st.session_state['wishlist'] = loaded_data['w']
        st.toast("Data succesvol geladen! Voeg nu gerust nieuwe aandelen toe.", icon="✅")
    except Exception as e:
        st.error(f"Fout bij laden bestand: {e}")

if st.sidebar.button("🧹 Cache Legen (Reset)"):
    st.cache_data.clear()
    st.rerun()

# ==================================================
# 7. HOOFDINTERFACE TABS
# ==================================================
tabs = st.tabs(["🔎 Analyse", "📊 Portfolio Pro", "❤️ Wishlist", "🇳🇱 Belasting"])

# --------------------------------------------------
# TAB 1: ANALYSE
# --------------------------------------------------
with tabs[0]:
    # Zoekbalk sectie
    col_search, col_period = st.columns([3, 1])
    with col_search:
        current_query = st.session_state.get('search_query', "")
        search_input = st.text_input("Zoek aandeel (Naam of Ticker):", value=current_query, key="search_main")
        
        selected_ticker = None
        if search_input:
            if "." in search_input and " " not in search_input: 
                selected_ticker = search_input # Direct ticker gebruik
            else:
                results = search_yahoo_strict(search_input)
                if results:
                    selected_ticker = st.selectbox("Zoekresultaten:", results).split(" | ")[0]
                else:
                    selected_ticker = search_input.upper()
    
    with col_period:
        chart_period = st.selectbox("Grafiek Periode", ["1mo", "6mo", "1y", "5y", "max"], index=2)

    if selected_ticker:
        try:
            # Data ophalen
            us_equivalent = EU_TO_US_MAP.get(selected_ticker, selected_ticker.split('.')[0] if '.' in selected_ticker else selected_ticker)
            engine = DataEngine(us_equivalent, selected_ticker)
            info = engine.yf_info
            
            # Metrics verzamelen
            metrics = {}
            metrics['peg'] = engine.get_combined_metric('pegRatio', 'peg')
            metrics['pe'] = engine.get_combined_metric('trailingPE', 'pe')
            metrics['roe'] = engine.get_combined_metric('returnOnEquity', None, is_percent=True)
            metrics['de'] = engine.get_combined_metric('debtToEquity', 'de', is_debt_equity=True)
            metrics['cr'] = engine.get_combined_metric('currentRatio', None)
            metrics['pb'] = engine.get_combined_metric('priceToBook', None)
            metrics['margin'] = engine.get_combined_metric('profitMargins', None, is_percent=True)
            metrics['eps'] = engine.get_combined_metric('trailingEps', 'eps')
            metrics['div'] = engine.get_combined_metric('dividendYield', None, is_percent=True, is_dividend=True)
            
            # Prijzen & Valuta
            curr_sym = info.get('currencySymbol', '€')
            curr_code = info.get('currency', 'EUR')
            current_price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
            
            # Koersdoel
            target_val = info.get('targetMeanPrice')
            target_src = "Yahoo"
            if not target_val:
                target_val = engine.finviz_data.get('target')
                target_src = "Finviz"
            
            # Score berekenen
            score, summary = calculate_ai_score(metrics)

            # --- HEADER ---
            with st.container(border=True):
                h1, h2, h3 = st.columns([2, 1, 1])
                with h1:
                    st.title(info.get('shortName', selected_ticker))
                    st.caption(f"Ticker: {selected_ticker} | Valuta: {curr_code}")
                with h2:
                    st.metric("Huidige Koers", f"{curr_sym} {current_price:.2f}" if current_price else "-")
                with h3:
                    if target_val and current_price:
                        delta_target = (target_val - current_price) / current_price
                        st.metric("Koersdoel", f"{curr_sym} {target_val:.2f}", f"{delta_target:.1%}", help=f"Bron: {target_src}")
                    else:
                        st.metric("Koersdoel", "N/A")
                
                st.divider()
                
                # AI Score weergave
                a1, a2 = st.columns([1, 4])
                with a1:
                    score_col = "green" if score >= 7 else "orange" if score >= 5 else "red"
                    st.markdown(f"""
                        <div style='text-align:center; color:{score_col}; font-size:40px; font-weight:bold; 
                        border:3px solid {score_col}; border-radius:50%; width:100px; height:100px; 
                        line-height:90px; margin:auto'>{score}</div>
                    """, unsafe_allow_html=True)
                with a2:
                    st.subheader("AI Oordeel")
                    st.write(summary)

            # --- METRICS GRID ---
            st.subheader("📊 Fundamentals")
            
            def display_metric(label, key, good_fn, bad_fn, fmt="{:.2f}", is_pct=False):
                val, src = metrics[key]
                if val is None:
                    st.metric(label, "N/A", help=f"Bron: {src}")
                    return
                
                # Kleur indicator
                delta_symbol = "✅" if good_fn(val) else "⚠️" if bad_fn(val) else "⚪"
                
                # Formattering
                val_display = f"{val:.2f}%" if is_pct else fmt.format(val)
                
                # Tooltip opbouw
                tooltip_text = f"Waarde: {val}\nBron: {src}\n\n{TOOLTIPS.get(key, '')}"
                
                st.metric(label, val_display, delta=delta_symbol, delta_color="off", help=tooltip_text)

            with st.container(border=True):
                c1, c2, c3, c4 = st.columns(4)
                with c1: display_metric("P/E Ratio", 'pe', lambda x: x<20, lambda x: x>35)
                with c2: display_metric("PEG Ratio", 'peg', lambda x: x<1.5, lambda x: x>2.5)
                with c3: display_metric("EPS", 'eps', lambda x: x>0, lambda x: x<0)
                with c4: display_metric("Dividend", 'div', lambda x: x>3, lambda x: x<1, is_pct=True)
                
                c5, c6, c7, c8 = st.columns(4)
                with c5: display_metric("ROE", 'roe', lambda x: x>15, lambda x: x<5, is_pct=True)
                with c6: display_metric("Marge", 'margin', lambda x: x>15, lambda x: x<2, is_pct=True)
                with c7: display_metric("Debt/Eq", 'de', lambda x: x<1, lambda x: x>2)
                with c8: display_metric("Current", 'cr', lambda x: x>1.5, lambda x: x<1)

            # --- GRAFIEK & NIEUWS ---
            col_chart, col_news = st.columns([2, 1])
            with col_chart:
                hist_data = engine.yf_obj.history(period=chart_period)
                if not hist_data.empty:
                    fig = px.area(hist_data, x=hist_data.index, y='Close', title=f"Koersverloop ({chart_period})")
                    fig.update_layout(yaxis_title=f"Prijs ({curr_sym})")
                    st.plotly_chart(fig, use_container_width=True)
            
            with col_news:
                st.markdown("##### 📰 Recent Nieuws")
                news_items = get_google_news(info.get('shortName', selected_ticker))
                for item in news_items:
                    st.markdown(f"🔹 [{item['title']}]({item['link']})\n*{item['date']}*")
                
                # Video sectie
                st.markdown("##### 📺 Analyse Video")
                vid_id = get_youtube_video_id(selected_ticker)
                if vid_id:
                    st.video(f"https://www.youtube.com/watch?v={vid_id}")

            # --- ACTIES (TOEVOEGEN AAN PORTFOLIO) ---
            st.divider()
            col_add, col_wish = st.columns(2)
            
            with col_add:
                with st.popover("➕ Toevoegen aan Portfolio", use_container_width=True):
                    st.subheader("Positie Toevoegen")
                    
                    # Valuta Check & Converter voor input
                    rate = 1.0
                    if curr_code != "EUR":
                        rate = get_currency_rate(curr_code, "EUR")
                        st.info(f"💱 Wisselkoers: 1 {curr_code} = € {rate:.4f}")
                    
                    with st.form("add_position_form"):
                        amount_input = st.number_input("Aantal stuks", min_value=0.0001, value=10.0, step=1.0)
                        # Input prijs is in originele valuta (zoals de gebruiker het ziet in de app/broker)
                        price_input = st.number_input(f"Aankoopprijs per stuk ({curr_sym})", value=float(current_price) if current_price else 0.0)
                        
                        # Live berekening weergave (indien niet EUR)
                        if curr_code != "EUR":
                            eur_total = price_input * amount_input * rate
                            st.caption(f"💶 Totale aankoopwaarde: € {eur_total:,.2f}")

                        if st.form_submit_button("Opslaan in Portfolio"):
                            st.session_state['portfolio'].append({
                                'ticker': selected_ticker,
                                'amount': amount_input,
                                'avg_price': price_input, # We slaan op in originele valuta
                                'currency': curr_code,
                                'symbol': curr_sym
                            })
                            st.success(f"{selected_ticker} toegevoegd!")
                            st.rerun()

            with col_wish:
                if st.button("❤️ Zet op Wishlist", use_container_width=True):
                    st.session_state['wishlist'].append({'ticker': selected_ticker, 'name': info.get('shortName', selected_ticker)})
                    st.toast("Toegevoegd aan Wishlist!")

        except Exception as e:
            st.error(f"Er is een fout opgetreden: {e}")
            # Voor debugging: st.write(e)

# --------------------------------------------------
# TAB 2: PORTFOLIO PRO
# --------------------------------------------------
with tabs[1]:
    if not st.session_state['portfolio']:
        st.info("Je portfolio is leeg. Voeg aandelen toe via de Analyse tab.")
    else:
        st.markdown("### 🛠️ Portfolio Beheer")
        
        # 1. LIVE DATA OPHALEN & DATAFRAME BOUWEN
        portfolio_data = []
        
        total_value_eur = 0
        total_cost_eur = 0
        total_dividend_eur = 0
        sector_distribution = {}
        
        # We itereren over de portfolio items
        for i, item in enumerate(st.session_state['portfolio']):
            ticker = item['ticker']
            currency = item.get('currency', 'EUR')
            symbol = item.get('symbol', '€')
            
            # Live data fetchen
            try:
                stock_obj = yf.Ticker(ticker)
                # Gebruik fast_info voor snelheid
                live_price = stock_obj.fast_info['last_price']
                stock_info = stock_obj.info
                sector = stock_info.get('sector', 'Onbekend')
                
                # Dividend bepalen (Rate = bedrag, Yield = %)
                div_amt = 0
                if 'dividendRate' in stock_info and stock_info['dividendRate']:
                    div_amt = stock_info['dividendRate']
                elif 'dividendYield' in stock_info and stock_info['dividendYield']:
                    div_amt = live_price * stock_info['dividendYield']
            except:
                # Fallback als fetch faalt
                live_price = item['avg_price']
                sector = "Onbekend"
                div_amt = 0
            
            # Berekeningen: Eerst alles in Native valuta
            val_native = live_price * item['amount']
            # Cost in native (GAK * Aantal)
            cost_native = item['avg_price'] * item['amount']
            
            # Winst in native
            profit_native = val_native - cost_native
            div_total_native = div_amt * item['amount']
            
            # Omrekenen naar Euro voor totalen & display
            eur_rate = get_currency_rate(currency, "EUR")
            
            val_eur = val_native * eur_rate
            cost_eur = cost_native * eur_rate # We gebruiken huidige koers voor benadering historische kosten in EUR (simplistisch, maar effectief voor huidig overzicht)
            profit_eur = val_eur - cost_eur # Winst in euro's (incl valuta resultaat)
            div_eur = div_total_native * eur_rate
            
            # Totalen bijwerken
            total_value_eur += val_eur
            total_cost_eur += cost_eur
            total_dividend_eur += div_eur
            
            # Sector data
            sector_distribution[sector] = sector_distribution.get(sector, 0) + val_eur

            # Rij toevoegen voor de Editor DataFrame
            # BELANGRIJK: Prijs & GAK blijven in originele valuta voor herkenbaarheid
            # Waarde & Winst gaan naar Euro voor uniformiteit in portfolio
            portfolio_data.append({
                "Ticker": ticker,
                "Aantal": float(item['amount']),          # Bewerkbaar
                "GAK": float(item['avg_price']),          # Bewerkbaar (Originele Valuta)
                "Huidige Prijs": live_price,              # Read-only (Originele Valuta)
                "Waarde (€)": val_eur,                    # Read-only (Euro)
                "Winst (€)": profit_eur,                  # Read-only (Euro)
                "Div/Jaar (€)": div_eur,                  # Read-only (Euro)
                "Sector": sector,
                "Valuta": symbol,
                "_currency_code": currency                # Verborgen
            })

        # 2. DASHBOARD METRICS (KPI's)
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        
        kpi1.metric("Totale Waarde", f"€ {total_value_eur:,.2f}")
        
        abs_profit = total_value_eur - total_cost_eur
        pct_profit = (abs_profit / total_cost_eur * 100) if total_cost_eur else 0
        kpi2.metric("Totaal Winst/Verlies", f"€ {abs_profit:,.2f}", delta=f"{pct_profit:.1f}%")
        
        kpi3.metric("Geschat Dividend/Jaar", f"€ {total_dividend_eur:,.2f}", delta="Passief Inkomen")
        
        yield_pct = (total_dividend_eur / total_value_eur * 100) if total_value_eur else 0
        kpi4.metric("Portfolio Yield", f"{yield_pct:.2f}%")
        
        st.divider()

        # 3. GRAFIEKEN
        col_chart1, col_chart2 = st.columns(2)
        with col_chart1:
            st.subheader("Sectoren")
            if sector_distribution:
                fig_pie = px.pie(names=list(sector_distribution.keys()), values=list(sector_distribution.values()), hole=0.4)
                st.plotly_chart(fig_pie, use_container_width=True)
        
        with col_chart2:
            st.subheader("Dividend Verwachting")
            if portfolio_data:
                df_charts = pd.DataFrame(portfolio_data)
                # Voor de grafiek gebruiken we de euro dividend kolom
                fig_bar = px.bar(df_charts, x='Ticker', y='Div/Jaar (€)', color='Sector', title="Dividend per Positie (€)")
                st.plotly_chart(fig_bar, use_container_width=True)

        # 4. TABEL MET BEWERK FUNCTIE (st.data_editor)
        st.subheader("📋 Positie Overzicht & Bewerken")
        
        df_editor_source = pd.DataFrame(portfolio_data)
        
        # Configuratie van de editor kolommen
        column_settings = {
            "Ticker": st.column_config.TextColumn("Aandeel", disabled=True),
            "Aantal": st.column_config.NumberColumn("Aantal", min_value=0.0001, required=True, step=1.0),
            "GAK": st.column_config.NumberColumn("GAK (Origineel)", min_value=0.0, required=True, format="%.2f", help="Gemiddelde Aankoopkoers in originele valuta"),
            "Huidige Prijs": st.column_config.NumberColumn("Prijs (Origineel)", format="%.2f", disabled=True, help="Huidige koers in originele valuta"),
            "Waarde (€)": st.column_config.NumberColumn("Waarde (€)", format="€ %.2f", disabled=True),
            "Winst (€)": st.column_config.NumberColumn("Winst (€)", format="€ %.2f", disabled=True),
            "Div/Jaar (€)": st.column_config.NumberColumn("Div/Jaar (€)", format="€ %.2f", disabled=True),
            "Sector": st.column_config.TextColumn(disabled=True),
            "Valuta": st.column_config.TextColumn(disabled=True)
        }

        # De Editor
        edited_df = st.data_editor(
            df_editor_source,
            column_config=column_settings,
            column_order=("Ticker", "Aantal", "GAK", "Huidige Prijs", "Waarde (€)", "Winst (€)", "Div/Jaar (€)", "Sector", "Valuta"),
            hide_index=True,
            use_container_width=True,
            key="main_portfolio_editor"
        )

        # 5. OPSLAAN VAN WIJZIGINGEN
        new_state_portfolio = []
        data_changed = False
        
        if not df_editor_source.equals(edited_df):
            data_changed = True
            for index, row in edited_df.iterrows():
                new_state_portfolio.append({
                    'ticker': row['Ticker'],
                    'amount': row['Aantal'],
                    'avg_price': row['GAK'],
                    'currency': row['_currency_code'],
                    'symbol': row['Valuta']
                })
            
            # Update state en herlaad pagina om totalen bij te werken
            st.session_state['portfolio'] = new_state_portfolio
            st.rerun()

        # 6. COMPOUND CALCULATOR
        st.markdown("---")
        with st.expander("🧮 Rente-op-Rente (Compound Interest) Calculator", expanded=True):
            col_cal1, col_cal2 = st.columns([1, 2])
            
            with col_cal1:
                st.write("**Scenario Instellingen**")
                start_cap = st.number_input("Startkapitaal (Huidig Portfolio)", value=float(round(total_value_eur, 2)))
                mnd_inleg = st.number_input("Maandelijkse Inleg (€)", value=500)
                jaren = st.slider("Aantal Jaren", 1, 40, 20)
                rendement = st.slider("Verwacht Rendement (%)", 1, 15, 7) / 100
            
            with col_cal2:
                # Berekening
                years_range = range(1, jaren + 1)
                values_list = []
                invested_list = []
                
                current_balance = start_cap
                total_invested = start_cap
                
                for _ in years_range:
                    for _ in range(12):
                        current_balance = current_balance * (1 + rendement/12) + mnd_inleg
                        total_invested += mnd_inleg
                    values_list.append(current_balance)
                    invested_list.append(total_invested)
                
                winst_rente = current_balance - total_invested
                
                mc1, mc2 = st.columns(2)
                mc1.metric(f"Waarde over {jaren} jaar", f"€ {current_balance:,.0f}")
                mc2.metric("Rente Winst", f"€ {winst_rente:,.0f}", delta="Gratis Geld")
                
                # Plot
                df_comp = pd.DataFrame({
                    "Jaar": years_range,
                    "Portfolio Waarde": values_list,
                    "Eigen Inleg": invested_list
                })
                fig_comp = px.line(df_comp, x="Jaar", y=["Portfolio Waarde", "Eigen Inleg"], title="Vermogensgroei Projectie")
                st.plotly_chart(fig_comp, use_container_width=True)

        # Knop om alles te wissen
        if st.button("⚠️ Portfolio Volledig Wissen"):
            st.session_state['portfolio'] = []
            st.rerun()

# --------------------------------------------------
# TAB 3: WISHLIST (COMPACT)
# --------------------------------------------------
with tabs[2]:
    st.subheader("❤️ Mijn Wishlist")
    if st.session_state['wishlist']:
        # We gebruiken een container voor een nette lijst
        with st.container(border=True):
            for i, item in enumerate(st.session_state['wishlist']):
                ticker = item['ticker']
                name = item['name']
                
                # Live koers ophalen
                try:
                    tk = yf.Ticker(ticker)
                    fi = tk.fast_info
                    price = fi['last_price']
                    prev = fi['previous_close']
                    pct_change = ((price - prev) / prev) * 100
                    currency_sym = tk.info.get('currencySymbol', '€')
                    
                    color_style = "green" if pct_change >= 0 else "red"
                    
                    # Compacte rij layout
                    c1, c2, c3, c4 = st.columns([1, 4, 2, 1])
                    c1.write(f"**{ticker}**")
                    c2.write(name)
                    c3.markdown(f"{currency_sym} {price:.2f} (<span style='color:{color_style}'>{pct_change:+.2f}%</span>)", unsafe_allow_html=True)
                    
                    # Verwijder knop
                    if c4.button("🗑️", key=f"del_wl_{i}"):
                        st.session_state['wishlist'].pop(i)
                        st.rerun()
                    
                    if i < len(st.session_state['wishlist']) - 1:
                        st.divider()
                except:
                    st.error(f"Kon data voor {ticker} niet laden.")
    else:
        st.info("Je wishlist is nog leeg.")

# --------------------------------------------------
# TAB 4: BELASTING
# --------------------------------------------------
with tabs[3]:
    st.header("🧮 Werkelijk Rendement & Box 3 Belasting")
    
    col_tax1, col_tax2 = st.columns(2)
    with col_tax1:
        st.subheader("1. Vermogen & Inleg")
        start_vermogen = st.number_input("Vermogen 1 januari (€)", value=50000.0, step=1000.0)
        eind_vermogen = st.number_input("Vermogen 31 december (€)", value=55000.0, step=1000.0)
        netto_inleg = st.number_input("Netto Inleg dit jaar (€)", value=0.0, help="Stortingen min opnames")
        heffingsvrij = st.number_input("Heffingsvrij Vermogen (€)", value=57000.0)

    with col_tax2:
        st.subheader("2. Fiscale Tarieven")
        forfaitair_pct = st.number_input("Forfaitair Rendement (%)", value=6.04, step=0.01)
        belasting_pct = st.number_input("Belastingtarief Box 3 (%)", value=36.0, step=0.5)
    
    st.divider()
    
    # Berekeningen
    werkelijk_rendement_euro = (eind_vermogen - start_vermogen) - netto_inleg
    werkelijk_rendement_pct = (werkelijk_rendement_euro / start_vermogen * 100) if start_vermogen > 0 else 0
    
    grondslag = max(0, start_vermogen - heffingsvrij)
    fictief_inkomen = grondslag * (forfaitair_pct / 100)
    te_betalen = fictief_inkomen * (belasting_pct / 100)
    
    # Resultaten
    col_res1, col_res2 = st.columns(2)
    
    with col_res1:
        with st.container(border=True):
            st.markdown("### 💰 Werkelijk Resultaat")
            if werkelijk_rendement_euro >= 0:
                st.metric("Winst in Euro's", f"€ {werkelijk_rendement_euro:,.2f}", delta="Winst")
            else:
                st.metric("Verlies in Euro's", f"€ {werkelijk_rendement_euro:,.2f}", delta="Verlies", delta_color="inverse")
            st.metric("Rendement %", f"{werkelijk_rendement_pct:.2f}%")

    with col_res2:
        with st.container(border=True):
            st.markdown("### 🏛️ Belastingdienst (Box 3)")
            if grondslag > 0:
                st.write(f"Belastbare Grondslag: € {grondslag:,.2f}")
                st.write(f"Fictief Inkomen: € {fictief_inkomen:,.2f}")
                st.metric("Te Betalen Belasting", f"€ {te_betalen:,.2f}", delta="- Uitgaven", delta_color="inverse")
                
                netto_resultaat = werkelijk_rendement_euro - te_betalen
                st.divider()
                st.write(f"**Netto resultaat na belasting:** € {netto_resultaat:,.2f}")
            else:
                st.success("Je vermogen valt binnen het heffingsvrije vermogen. Er is **€ 0,00** belasting verschuldigd.")
