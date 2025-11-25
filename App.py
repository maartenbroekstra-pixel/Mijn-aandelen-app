import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import time

# ==================================================
# 1. CONFIGURATIE & CONSTANTEN
# ==================================================
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Uitgebreide mapping om Earnings data te vinden (Yahoo heeft betere data voor US tickers)
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD", "SHELL.AS": "SHEL",
    "WKL.AS": "WTKWY", "9PDA.DU": "PROSY", "RAND.AS": "RANJY", "AKZA.AS": "AKZOY",
    "DSM.AS": "DSM", "KPN.AS": "KKPNY", "NN.AS": "NNGP", "ASM.AS": "ASMIY"
}

TOOLTIPS = {
    "peg": "Price/Earnings to Growth (PEG).\n🟢 < 1.5: Ondergewaardeerd.\n🔴 > 2.5: Overgewaardeerd.",
    "pe": "Koers/Winst verhouding (P/E).\n🟢 < 20: Koopje.\n🟠 > 30: Premie.",
    "roe": "Rendement op Eigen Vermogen (ROE).\n🟢 > 15%: Sterk management.",
    "de": "Schuldratio (Debt/Equity).\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "div": "Dividend Rendement.\n🟢 > 3%: Interessant.",
    "margin": "Nettowinstmarge.\n🟢 > 20%: Hoge winstmarge.",
    "eps": "Winst per aandeel (EPS).\n🟢 > 0: Winstgevend."
}

DEFAULT_PORTFOLIO = []

SECTOR_COLORS = {
    "Technology": "#1f77b4", "Financial Services": "#ff7f0e", "Healthcare": "#2ca02c",
    "Consumer Cyclical": "#d62728", "Industrials": "#9467bd", "Consumer Defensive": "#8c564b",
    "Energy": "#e377c2", "Communication Services": "#7f7f7f", "Real Estate": "#bcbd22",
    "Basic Materials": "#17becf", "Utilities": "#aec7e8", "ETF/Overig": "#ffbb78"
}

# ==================================================
# 2. STATE MANAGEMENT
# ==================================================
if 'portfolio' not in st.session_state: 
    st.session_state['portfolio'] = DEFAULT_PORTFOLIO

if 'wishlist' not in st.session_state: 
    st.session_state['wishlist'] = []

if 'search_results' not in st.session_state:
    st.session_state['search_results'] = []

# ==================================================
# 3. HELPER FUNCTIES
# ==================================================

@st.cache_data(ttl=600)
def get_finviz_data(ticker):
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
            d['de'] = parse('Debt/Eq')
            d['roe'] = parse('ROE') 
            d['margin'] = parse('Profit Margin')
            d['div'] = parse('Dividend %')
    except Exception:
        pass
    return d

@st.cache_data(ttl=300)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: 
        return 1.0
    try:
        ticker = "EURUSD=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD": 
                return 1.0 / rate 
            return rate
        return 1.0
    except Exception:
        return 1.0

@st.cache_data(ttl=3600)
def get_extended_portfolio_data(ticker):
    """
    Haalt data op.
    FIX: YTD berekening robuuster gemaakt.
    FIX: Earnings data ophalen verbeterd.
    """
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        
        # 1. Type bepalen
        q_type = info.get('quoteType', 'EQUITY').upper()
        if 'ETF' in q_type: q_type = 'ETF'
        else: q_type = 'Aandeel'
            
        # 2. Earnings Data (Surprise Chart)
        earnings_surprise = None
        earnings_history_list = []
        
        try:
            # Probeer US data te pakken voor earnings (veel completer bij Yahoo)
            us_ticker = EU_TO_US_MAP.get(ticker, ticker)
            earning_tk = yf.Ticker(us_ticker)
            
            dates = earning_tk.earnings_dates
            if dates is not None and not dates.empty:
                if dates.index.tz is not None:
                    dates.index = dates.index.tz_localize(None)
                
                now = datetime.now()
                # Haal historie op: data in het verleden
                past_dates = dates[dates.index < now].sort_index(ascending=False)
                
                # Pak laatste 4 gerapporteerde kwartalen met data
                valid_history = past_dates.dropna(subset=['Reported EPS', 'EPS Estimate']).head(4)
                
                if not valid_history.empty:
                    # Data voor Chart
                    for date, row in valid_history.iterrows():
                         earnings_history_list.append({
                             'date': date.strftime('%Y-%m-%d'),
                             'reported': row['Reported EPS'],
                             'estimate': row['EPS Estimate'],
                             'surprise': row['Surprise(%)'] if 'Surprise(%)' in row else 0
                         })
                    earnings_history_list.reverse() # Oud naar nieuw

                    # Data voor Tabel (laatste resultaat)
                    last_report = valid_history.iloc[0]
                    reported = last_report.get('Reported EPS')
                    estimate = last_report.get('EPS Estimate')
                    earnings_date = valid_history.index[0]
                    
                    if pd.notnull(reported) and pd.notnull(estimate):
                        # Zelf surprise berekenen als check
                        val_est = float(estimate)
                        val_rep = float(reported)
                        if val_est != 0:
                            surp_pct = ((val_rep - val_est) / abs(val_est)) * 100
                        else:
                            surp_pct = 0
                            
                        earnings_surprise = {
                            'date': earnings_date,
                            'reported': val_rep,
                            'estimate': val_est,
                            'surprise_pct': surp_pct
                        }
        except Exception:
            pass

        # 3. Dividend Maanden
        dividend_months = []
        try:
            hist = tk.dividends
            if not hist.empty:
                if hist.index.tz is not None:
                    hist.index = hist.index.tz_localize(None)
                last_year = datetime.now() - timedelta(days=365)
                recent_divs = hist[hist.index > last_year]
                for date, amount in recent_divs.items():
                    dividend_months.append({'month': date.month, 'amount': amount})
        except Exception:
            pass

        # 4. YTD Berekening (Handmatig en robuust)
        ytd_start_price = None
        current_year = datetime.now().year
        start_date = f"{current_year}-01-01"
        try:
            # Haal data op van begin dit jaar tot nu
            hist_ytd = tk.history(start=start_date, interval="1d")
            if not hist_ytd.empty:
                ytd_start_price = hist_ytd['Close'].iloc[0] # Eerste slotkoers van het jaar
        except:
            pass

        previous_close = info.get('regularMarketPreviousClose')
        current_price = info.get('currentPrice') or previous_close

        return {
            'type': q_type,
            'earnings': earnings_surprise,
            'earnings_history': earnings_history_list, 
            'dividend_history': dividend_months,
            'sector': info.get('sector', 'ETF/Overig'),
            'live_price': current_price,
            'previous_close': previous_close,
            'ytd_start_price': ytd_start_price,
            'currency_symbol': info.get('currencySymbol', '€'),
            'shortName': info.get('shortName', ticker),
            'longName': info.get('longName', ticker),
            'fallback_div_rate': info.get('dividendRate'),
            'fallback_div_yield': info.get('dividendYield')
        }
    except Exception:
        return None

def search_yahoo_strict(query):
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&quotesCount=15"
        r = requests.get(url, headers=HEADERS, timeout=3)
        data = r.json()
        results = []
        raw_quotes = data.get('quotes', [])
        for q in raw_quotes:
            symbol = q.get('symbol')
            name = q.get('shortname') or q.get('longname' ) or symbol
            results.append(f"{symbol} | {name}")
        return results
    except Exception:
        return []

def get_google_news(query):
    try:
        # Aangepast naar US/Engels voor betere earnings resultaten
        query_encoded = query.replace(' ', '+')
        url = f"https://news.google.com/rss/search?q={query_encoded}&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, headers=HEADERS, timeout=4)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:3]:
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
    try:
        q = f"{query} stock analysis {datetime.now().year}"
        url = f"https://www.youtube.com/results?search_query={q.replace(' ','+')}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except Exception:
        return None

# ==================================================
# 4. DATA ENGINE CLASS (Voor Analyse Tab)
# ==================================================
class DataEngine:
    def __init__(self, ticker, original_ticker):
        self.ticker = ticker 
        self.original_ticker = original_ticker 
        self.yf_obj = yf.Ticker(original_ticker)
        self.yf_info = self.yf_obj.info
        self.finviz_data = {}
        self.fetch_finviz()

    def fetch_finviz(self):
        if "." not in self.ticker:
            self.finviz_data = get_finviz_data(self.ticker)

    def _normalize_value(self, val, is_percent, is_dividend, is_debt_equity, source_name):
        if val is None: return None
        if is_debt_equity:
            if val > 5.0: return val / 100.0
            return val
        if is_percent:
            if is_dividend: return val
            else:
                if abs(val) <= 1.0 and val != 0: return val * 100
        return val

    def get_robust_peg(self):
        values = []
        sources = []
        val_yf = self.yf_info.get('pegRatio')
        if val_yf: values.append(float(val_yf)); sources.append("Yahoo")
        val_fin = self.finviz_data.get('peg')
        if val_fin: values.append(float(val_fin)); sources.append("Finviz")
        if not values: return None, "N/A"
        return sum(values) / len(values), " & ".join(sources)

    def get_combined_metric(self, key_yf, key_finviz=None, is_percent=False, is_dividend=False, is_debt_equity=False, use_average=True):
        valid_values = []
        sources = []
        raw_yf = self.yf_info.get(key_yf)
        if raw_yf is not None:
            norm_yf = self._normalize_value(float(raw_yf), is_percent, is_dividend, is_debt_equity, "Yahoo")
            valid_values.append(norm_yf)
            sources.append("Yahoo")
        
        if not use_average and valid_values: return valid_values[0], sources[0]

        if key_finviz:
            raw_fin = self.finviz_data.get(key_finviz)
            if raw_fin is not None:
                norm_fin = self._normalize_value(float(raw_fin), is_percent, is_dividend, is_debt_equity, "Finviz")
                valid_values.append(norm_fin)
                sources.append("Finviz")

        if not valid_values: return None, "N/A"
        if not use_average: return valid_values[0], sources[0]
        
        avg_val = sum(valid_values) / len(valid_values)
        source_str = " & ".join(sources) + (f" (Avg)" if len(sources) > 1 else "")
        return avg_val, source_str

# ==================================================
# 5. AI SCORE LOGICA
# ==================================================
def calculate_ai_score(data):
    score = 5.0
    reasons = []
    
    eps_val, _ = data['eps']
    if eps_val is not None:
        if eps_val > 0: score += 1.0; reasons.append("Winstgevend")
        else: score -= 2.5; reasons.append("Verlieslatend")
    
    peg_val, _ = data['peg']
    if peg_val is not None:
        if 0 < peg_val < 1.5: score += 1.0; reasons.append("Goede PEG")
        elif peg_val > 2.5: score -= 0.5
        elif peg_val < 0: score -= 1.0

    pe_val, _ = data['pe']
    if pe_val is not None:
        if 0 < pe_val < 20: score += 0.5
        elif pe_val > 40: score -= 0.5

    de_val, _ = data['de']
    if de_val is not None:
        if de_val < 1.0: score += 1.0; reasons.append("Gezonde Balans")
        elif de_val > 2.0: score -= 1.0; reasons.append("Veel Schulden")

    cr_val, _ = data['cr']
    if cr_val is not None:
        if cr_val > 1.5: score += 0.5
        elif cr_val < 1.0: score -= 0.5; reasons.append("Krap")

    margin_val, _ = data['margin']
    if margin_val is not None:
        if margin_val > 20: score += 1.0; reasons.append("Hoge Marges")
        elif margin_val < 5: score -= 0.5

    roe_val, _ = data['roe']
    if roe_val is not None:
        if roe_val > 15: score += 0.5
    
    div_val, _ = data['div']
    if div_val is not None:
        is_decimal = div_val < 1.0
        threshold = 0.025 if is_decimal else 2.5
        if div_val > threshold: score += 0.5; reasons.append("Mooi Dividend")

    final_score = max(0.0, min(10.0, score))
    summary = "Het bedrijf scoort gemiddeld."
    if final_score >= 7.5: summary = f"Sterk aandeel! Positieve punten: {', '.join(reasons[:3]).lower()}."
    elif final_score <= 4.0: summary = "De fundamentals zien er zwak uit."
    elif reasons: summary = f"Gemengd beeld. Opvallend: {', '.join(reasons[:2]).lower()}."
    
    return round(final_score, 1), summary

# ==================================================
# 6. UI: SIDEBAR
# ==================================================
st.sidebar.header("📂 Data & Backup")

json_export = json.dumps({'p': st.session_state['portfolio'], 'w': st.session_state['wishlist']})
st.sidebar.download_button(
    label="⬇️ Download Backup",
    data=json_export,
    file_name="aandelen_backup.json",
    mime="application/json"
)

uploaded_file = st.sidebar.file_uploader("⬆️ Upload Backup", type=['json'])
if uploaded_file and st.sidebar.button("📂 Data Inladen / Herstellen"):
    try:
        loaded_data = json.load(uploaded_file)
        if 'p' in loaded_data: st.session_state['portfolio'] = loaded_data['p']
        if 'w' in loaded_data: st.session_state['wishlist'] = loaded_data['w']
        st.toast("Data succesvol geladen!", icon="✅")
        st.rerun()
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
    def run_search():
        query = st.session_state.search_main
        if query:
            if "." in query and " " not in query:
                st.session_state['search_results'] = [query]
            else:
                st.session_state['search_results'] = search_yahoo_strict(query)
        else:
            st.session_state['search_results'] = []

    col_search, col_period = st.columns([3, 1])
    with col_search:
        search_input = st.text_input(
            "Zoek aandeel (Typ en druk op Enter):", 
            key="search_main", 
            on_change=run_search
        )
        selected_ticker = None
        if st.session_state['search_results']:
            selection = st.selectbox("Zoekresultaten:", st.session_state['search_results'])
            if selection: selected_ticker = selection.split(" | ")[0]
        elif search_input and "." in search_input:
            selected_ticker = search_input
    
    with col_period:
        chart_period = st.selectbox("Grafiek Periode", ["1mo", "6mo", "1y", "5y", "max"], index=2)

    if selected_ticker:
        try:
            us_equivalent = EU_TO_US_MAP.get(selected_ticker, selected_ticker.split('.')[0] if '.' in selected_ticker else selected_ticker)
            engine = DataEngine(us_equivalent, selected_ticker)
            info = engine.yf_info
            
            metrics = {}
            metrics['peg'] = engine.get_robust_peg()
            metrics['pe'] = engine.get_combined_metric('trailingPE', 'pe', use_average=False)
            metrics['div'] = engine.get_combined_metric('dividendYield', 'div', is_percent=True, is_dividend=True, use_average=False)
            metrics['roe'] = engine.get_combined_metric('returnOnEquity', 'roe', is_percent=True)
            metrics['de'] = engine.get_combined_metric('debtToEquity', 'de', is_debt_equity=True)
            metrics['cr'] = engine.get_combined_metric('currentRatio', None)
            metrics['pb'] = engine.get_combined_metric('priceToBook', None)
            metrics['margin'] = engine.get_combined_metric('profitMargins', 'margin', is_percent=True)
            metrics['eps'] = engine.get_combined_metric('trailingEps', 'eps')
            
            raw_currency = info.get('currency', 'EUR').upper()
            curr_sym = '$' if raw_currency == 'USD' else '€' if raw_currency == 'EUR' else info.get('currencySymbol', '€')
            current_price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
            target_val = info.get('targetMeanPrice') or engine.finviz_data.get('target')
            
            score, summary = calculate_ai_score(metrics)

            with st.container(border=True):
                h1, h2, h3 = st.columns([2, 1, 1])
                with h1:
                    st.title(info.get('shortName', selected_ticker))
                    st.caption(f"Ticker: {selected_ticker} | Valuta: {raw_currency}")
                with h2:
                    st.metric("Huidige Koers", f"{curr_sym} {current_price:.2f}" if current_price else "-")
                with h3:
                    if target_val and current_price:
                        delta_target = (target_val - current_price) / current_price
                        st.metric("Koersdoel", f"{curr_sym} {target_val:.2f}", f"{delta_target:.1%}")
                    else: st.metric("Koersdoel", "N/A")
                st.divider()
                a1, a2 = st.columns([1, 4])
                with a1:
                    score_col = "green" if score >= 7 else "orange" if score >= 5 else "red"
                    st.markdown(f"<div style='text-align:center; color:{score_col}; font-size:40px; font-weight:bold; border:3px solid {score_col}; border-radius:50%; width:100px; height:100px; line-height:90px; margin:auto'>{score}</div>", unsafe_allow_html=True)
                with a2:
                    st.subheader("AI Oordeel")
                    st.write(summary)

            st.subheader("📊 Fundamentals")
            def display_metric(label, key, good_fn, bad_fn, fmt="{:.2f}", is_pct=False):
                val, src = metrics[key]
                if val is None: st.metric(label, "N/A", help=f"Bron: {src}"); return
                delta_symbol = "✅" if good_fn(val) else "⚠️" if bad_fn(val) else "⚪"
                val_display = f"{val:.2f}%" if is_pct else fmt.format(val)
                st.metric(label, val_display, delta=delta_symbol, delta_color="off", help=f"Waarde: {val:.2f}\nBron: {src}\n{TOOLTIPS.get(key, '')}")

            with st.container(border=True):
                c1, c2, c3, c4 = st.columns(4)
                with c1: display_metric("P/E Ratio", 'pe', lambda x: x<20, lambda x: x>35)
                with c2: display_metric("PEG Ratio", 'peg', lambda x: x<1.5, lambda x: x>2.5)
                with c3: display_metric("EPS", 'eps', lambda x: x>0, lambda x: x<0)
                with c4: display_metric("Dividend", 'div', lambda x: x>3 or (x<1 and x>0.03), lambda x: x<1 and x>0.01, is_pct=True)
                c5, c6, c7, c8 = st.columns(4)
                with c5: display_metric("ROE", 'roe', lambda x: x>15, lambda x: x<5, is_pct=True)
                with c6: display_metric("Marge", 'margin', lambda x: x>20, lambda x: x<2, is_pct=True)
                with c7: display_metric("Debt/Eq", 'de', lambda x: x<1, lambda x: x>2)
                with c8: display_metric("Current", 'cr', lambda x: x>1.5, lambda x: x<1)

            col_chart, col_news = st.columns([2, 1])
            with col_chart:
                hist_data = engine.yf_obj.history(period=chart_period)
                if not hist_data.empty:
                    fig = px.area(hist_data, x=hist_data.index, y='Close', title=f"Koersverloop ({chart_period})")
                    st.plotly_chart(fig, use_container_width=True)
            with col_news:
                st.markdown("##### 📰 Earnings & Nieuws")
                # Specifiek earnings nieuws zoeken
                q_news = f"{info.get('shortName', selected_ticker)} earnings financial results"
                news_items = get_google_news(q_news)
                if not news_items: # Fallback naar algemeen nieuws
                     news_items = get_google_news(info.get('shortName', selected_ticker))
                
                for item in news_items: st.markdown(f"🔹 [{item['title']}]({item['link']})\n*{item['date']}*")
                
                st.markdown("##### 📺 Analyse Video")
                vid_id = get_youtube_video_id(selected_ticker)
                if vid_id: st.video(f"https://www.youtube.com/watch?v={vid_id}")

            st.divider()
            col_add, col_wish = st.columns(2)
            with col_add:
                with st.popover("➕ Toevoegen aan Portfolio", use_container_width=True):
                    st.subheader("Positie Toevoegen")
                    rate = 1.0
                    if raw_currency != "EUR":
                        rate = get_currency_rate(raw_currency, "EUR")
                        st.info(f"Koers: 1 {raw_currency} = € {rate:.4f}")
                        cur_price_eur = (float(current_price) * rate) if current_price else 0.0
                        st.write(f"Prijs: {curr_sym} {current_price} (≈ € {cur_price_eur:.2f})")
                    else: st.write(f"Prijs: € {current_price}")

                    with st.form("add_position_form"):
                        amount_input = st.number_input("Aantal stuks", min_value=0.0001, value=10.0, step=1.0)
                        price_input_eur = st.number_input("Aankoopprijs (€)", value=(float(current_price)*rate) if current_price else 0.0)
                        
                        if st.form_submit_button("Opslaan"):
                            price_native = price_input_eur / rate if rate > 0 else price_input_eur
                            st.session_state['portfolio'].append({
                                'ticker': selected_ticker,
                                'amount': amount_input,
                                'avg_price': price_native,
                                'currency': raw_currency,
                                'symbol': curr_sym
                            })
                            st.success(f"{selected_ticker} toegevoegd!")
                            st.rerun()
            with col_wish:
                if st.button("❤️ Zet op Wishlist", use_container_width=True):
                    st.session_state['wishlist'].append({'ticker': selected_ticker, 'name': info.get('shortName', selected_ticker)})
                    st.toast("Toegevoegd aan Wishlist!")
        except Exception as e:
            st.error(f"Fout: {e}")

# --------------------------------------------------
# TAB 2: PORTFOLIO PRO
# --------------------------------------------------
with tabs[1]:
    if not st.session_state['portfolio']:
        st.info("Je portfolio is leeg. Voeg aandelen toe via de Analyse tab.")
    else:
        st.markdown("### 📊 Portfolio Dashboard Pro")
        
        portfolio_data = []
        monthly_div_projection = {i: 0.0 for i in range(1, 13)}
        sector_distribution = {}
        
        total_val_eur = 0
        total_cost_eur = 0
        total_div_eur = 0
        
        total_day_change_eur = 0
        total_year_change_eur = 0

        etf_stats = {'value': 0, 'cost': 0, 'profit': 0}
        stock_stats = {'value': 0, 'cost': 0, 'profit': 0}
        
        earnings_history_store = {} 

        progress_bar = st.progress(0, text="Portfolio data analyseren...")
        
        for i, item in enumerate(st.session_state['portfolio']):
            ticker = item['ticker']
            progress_bar.progress((i + 1) / len(st.session_state['portfolio']), text=f"Analyseren: {ticker}...")
            
            ext_data = get_extended_portfolio_data(ticker)
            
            # Fallbacks
            live_price = ext_data['live_price'] if ext_data and ext_data['live_price'] else item['avg_price']
            q_type = ext_data['type'] if ext_data else 'Aandeel'
            sector = ext_data['sector'] if ext_data else 'Onbekend'
            short_name = ext_data['shortName'] if ext_data else ticker
            long_name = ext_data['longName'] if ext_data else short_name
            
            amount = item['amount']
            avg_price = item['avg_price']
            currency = item.get('currency', 'EUR')
            symbol = item.get('symbol', '€')
            eur_rate = get_currency_rate(currency, "EUR")
            
            val_native = live_price * amount
            cost_native = avg_price * amount
            val_eur = val_native * eur_rate
            cost_eur = cost_native * eur_rate
            profit_eur = val_eur - cost_eur
            
            gak_eur = avg_price * eur_rate

            # Dag resultaat
            day_gain_native = 0
            if ext_data and ext_data['previous_close']:
                day_gain_native = (live_price - ext_data['previous_close']) * amount
            total_day_change_eur += (day_gain_native * eur_rate)

            # Jaar resultaat (YTD)
            year_gain_native = 0
            if ext_data and ext_data['ytd_start_price']:
                 year_gain_native = (live_price - ext_data['ytd_start_price']) * amount
            total_year_change_eur += (year_gain_native * eur_rate)

            # Dividend
            yearly_div_eur = 0
            if ext_data:
                if ext_data['dividend_history']:
                    for div_pay in ext_data['dividend_history']:
                        payout_eur = div_pay['amount'] * amount * eur_rate
                        monthly_div_projection[div_pay['month']] += payout_eur
                        yearly_div_eur += payout_eur
                elif ext_data.get('fallback_div_rate'):
                    yearly_div_eur = ext_data['fallback_div_rate'] * amount * eur_rate
                elif ext_data.get('fallback_div_yield') and ext_data['fallback_div_yield'] > 0:
                    yearly_div_eur = (live_price * ext_data['fallback_div_yield']) * amount * eur_rate

            total_val_eur += val_eur
            total_cost_eur += cost_eur
            total_div_eur += yearly_div_eur
            
            sector_distribution[sector] = sector_distribution.get(sector, 0) + val_eur

            if q_type == 'ETF':
                etf_stats['value'] += val_eur
                etf_stats['cost'] += cost_eur
                etf_stats['profit'] += profit_eur
            else:
                stock_stats['value'] += val_eur
                stock_stats['cost'] += cost_eur
                stock_stats['profit'] += profit_eur

            # Earnings
            earnings_str = "-"
            earnings_color = "black"
            if ext_data:
                if ext_data['earnings_history']:
                    earnings_history_store[ticker] = ext_data['earnings_history']
                
                if ext_data['earnings']:
                    surp = ext_data['earnings']['surprise_pct']
                    date_str = ext_data['earnings']['date'].strftime('%d-%m')
                    if surp > 0:
                        earnings_str = f"Beat: +{surp:.1f}% ({date_str})"
                        earnings_color = "green"
                    else:
                        earnings_str = f"Miss: {surp:.1f}% ({date_str})"
                        earnings_color = "red"

            portfolio_data.append({
                "Ticker": ticker,
                "Naam": short_name,
                "LangeNaam": long_name,
                "Type": q_type,
                "Aantal": float(amount),
                "GAK": float(avg_price),
                "GAK (€)": float(gak_eur),
                "Prijs": live_price,
                "Waarde (€)": val_eur,
                "Winst (€)": profit_eur,
                "Div/Jaar (€)": yearly_div_eur,
                "Earnings": earnings_str,
                "_earnings_color": earnings_color,
                "Sector": sector,
                "Valuta": symbol,
                "_currency_code": currency,
                "Verwijder": False
            })
            
        progress_bar.empty()

        # KPI's
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        
        abs_profit = total_val_eur - total_cost_eur
        pct_profit = (abs_profit / total_cost_eur * 100) if total_cost_eur else 0
        yield_pct = (total_div_eur / total_val_eur * 100) if total_val_eur else 0
        
        k1.metric("Totale Waarde", f"€ {total_val_eur:,.2f}")
        k2.metric("Totaal Winst", f"€ {abs_profit:,.2f}", delta=f"{pct_profit:.1f}%")
        k3.metric("Dag Resultaat", f"€ {total_day_change_eur:,.2f}", delta="Vandaag")
        k4.metric("Jaar Resultaat (YTD)", f"€ {total_year_change_eur:,.2f}", delta="Dit jaar")
        k5.metric("Verwacht Dividend", f"€ {total_div_eur:,.2f}", delta="Jaarbasis")
        k6.metric("Portfolio Yield", f"{yield_pct:.2f}%")
        
        st.divider()

        # 1. ETF vs Aandelen
        st.subheader("⚖️ Aandelen vs. ETF's")
        comp1, comp2 = st.columns(2)
        with comp1:
            etf_prof_pct = (etf_stats['profit'] / etf_stats['cost'] * 100) if etf_stats['cost'] > 0 else 0
            stock_prof_pct = (stock_stats['profit'] / stock_stats['cost'] * 100) if stock_stats['cost'] > 0 else 0
            c_a, c_b = st.columns(2)
            c_a.metric("ETF's Waarde", f"€ {etf_stats['value']:,.0f}", delta=f"{etf_prof_pct:.1f}%")
            c_b.metric("Losse Aandelen", f"€ {stock_stats['value']:,.0f}", delta=f"{stock_prof_pct:.1f}%")
            
            fig_dist = px.pie(names=['ETF', 'Aandelen'], values=[etf_stats['value'], stock_stats['value']], hole=0.5, color_discrete_sequence=['#3498db', '#e74c3c'])
            fig_dist.update_layout(height=250, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(fig_dist, use_container_width=True)

        with comp2:
            monthly_vals = [monthly_div_projection[i] for i in range(1, 13)]
            if sum(monthly_vals) > 0:
                st.markdown("**Maandelijks Dividend Projectie**")
                months = ["Jan", "Feb", "Mrt", "Apr", "Mei", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]
                df_divs = pd.DataFrame({'Maand': months, 'Dividend (€)': monthly_vals})
                fig_div = px.bar(df_divs, x='Maand', y='Dividend (€)', text_auto='.2s')
                fig_div.update_traces(marker_color='#2ecc71')
                fig_div.update_layout(height=300)
                st.plotly_chart(fig_div, use_container_width=True)
            else:
                st.info("Geen gedetailleerde uitbetalingsdata beschikbaar.")

        # 3. Sectoren & Dividend
        col_sec, col_divstock = st.columns(2)
        with col_sec:
            st.subheader("Sectoren")
            if sector_distribution:
                fig_pie = px.pie(names=list(sector_distribution.keys()), values=list(sector_distribution.values()), hole=0.4, color=list(sector_distribution.keys()), color_discrete_map=SECTOR_COLORS)
                st.plotly_chart(fig_pie, use_container_width=True)
        with col_divstock:
            st.subheader("Dividend per Positie")
            df_charts = pd.DataFrame(portfolio_data)
            if not df_charts.empty:
                df_charts = df_charts.sort_values(by="Div/Jaar (€)", ascending=False)
                fig_bar = px.bar(df_charts, x='Ticker', y='Div/Jaar (€)', color='Sector', color_discrete_map=SECTOR_COLORS)
                st.plotly_chart(fig_bar, use_container_width=True)

        st.divider()

        # Nieuws & Earnings
        st.subheader("🔔 Portfolio Nieuws & Earnings")
        n1, n2 = st.columns([2, 1])
        with n1:
            st.markdown("**Recente Earnings (Laatste kwartaal)**")
            df_earnings = pd.DataFrame(portfolio_data)
            df_earnings = df_earnings[df_earnings['Earnings'] != "-"]
            if not df_earnings.empty:
                html_table = "<table><tr><th>Ticker</th><th>Resultaat</th><th>Datum</th></tr>"
                for _, row in df_earnings.iterrows():
                    color = row['_earnings_color']
                    ticker = row['Ticker']
                    txt = row['Earnings'].split('(')[0]
                    date_txt = row['Earnings'].split('(')[1].replace(')', '')
                    html_table += f"<tr><td><b>{ticker}</b></td><td style='color:{color}'>{txt}</td><td>{date_txt}</td></tr>"
                html_table += "</table>"
                st.caption("Data Bron: Yahoo Finance (Schatting vs Gerapporteerd)")
            else:
                st.info("Geen recente earnings data gevonden in Yahoo Finance.")

        with n2:
            st.markdown("**Laatste Earnings Nieuws**")
            sorted_port = sorted(portfolio_data, key=lambda x: x['Waarde (€)'], reverse=True)[:3]
            for item in sorted_port:
                # Specifieke query voor kwartaalcijfers
                query = f"{item['LangeNaam']} quarterly earnings results"
                news = get_google_news(query)
                if news:
                    with st.expander(f"Cijfers: {item['Ticker']}"):
                        for n in news[:2]:
                            st.markdown(f"[{n['title']}]({n['link']})\n*{n['date']}*")
                else:
                    st.caption(f"Geen specifiek earnings nieuws voor {item['Ticker']}")

        # Earnings Grafiek Sectie
        if earnings_history_store:
            st.divider()
            st.subheader("📉 Earnings Surprise Historie (4 Kwartalen)")
            
            available_tickers = list(earnings_history_store.keys())
            if available_tickers:
                eps_ticker = st.selectbox("Selecteer aandeel voor detailgrafiek:", available_tickers)
                
                if eps_ticker:
                    hist_data = earnings_history_store[eps_ticker]
                    df_eps = pd.DataFrame(hist_data)
                    
                    fig_eps = go.Figure()
                    fig_eps.add_trace(go.Bar(
                        x=df_eps['date'],
                        y=df_eps['estimate'],
                        name='Verwachting (Estimate)',
                        marker_color='lightgray'
                    ))
                    fig_eps.add_trace(go.Bar(
                        x=df_eps['date'],
                        y=df_eps['reported'],
                        name='Gerapporteerd',
                        marker_color='#1f77b4'
                    ))
                    
                    fig_eps.update_layout(
                        title=f"Earnings vs Estimates: {eps_ticker} (Bron: Yahoo Finance)",
                        xaxis_title="Datum",
                        yaxis_title="EPS",
                        barmode='group'
                    )
                    st.plotly_chart(fig_eps, use_container_width=True)
            else:
                st.info("Geen uitgebreide earnings historie beschikbaar.")

        st.divider()

        # Editor Tabel
        st.subheader("📋 Positie Overzicht & Bewerken")
        df_editor_source = pd.DataFrame(portfolio_data)
        column_settings = {
            "Ticker": st.column_config.TextColumn("Ticker", disabled=True),
            "Naam": st.column_config.TextColumn("Naam", disabled=True),
            "Type": st.column_config.TextColumn("Type", disabled=True),
            "Aantal": st.column_config.NumberColumn("Aantal", min_value=0.0001, required=True, step=1.0),
            "GAK": st.column_config.NumberColumn("GAK (Origineel)", format="%.2f"),
            "GAK (€)": st.column_config.NumberColumn("GAK (€)", format="€ %.2f", disabled=True),
            "Waarde (€)": st.column_config.NumberColumn("Waarde (€)", format="€ %.2f", disabled=True),
            "Winst (€)": st.column_config.NumberColumn("Winst (€)", format="€ %.2f", disabled=True),
            "Div/Jaar (€)": st.column_config.NumberColumn("Div/Jaar (€)", format="€ %.2f", disabled=True),
            "Earnings": st.column_config.TextColumn("Earnings", disabled=True),
            "Verwijder": st.column_config.CheckboxColumn("🗑️", default=False)
        }
        edited_df = st.data_editor(
            df_editor_source, 
            column_config=column_settings, 
            column_order=("Verwijder", "Ticker", "Naam", "Type", "Aantal", "GAK", "GAK (€)", "Waarde (€)", "Winst (€)", "Div/Jaar (€)", "Earnings"), 
            hide_index=True, 
            use_container_width=True, 
            key="main_portfolio_editor"
        )

        if not df_editor_source.equals(edited_df):
            new_state_portfolio = []
            for index, row in edited_df.iterrows():
                if not row['Verwijder']:
                    new_state_portfolio.append({'ticker': row['Ticker'], 'amount': row['Aantal'], 'avg_price': row['GAK'], 'currency': row['_currency_code'], 'symbol': row['Valuta']})
            st.session_state['portfolio'] = new_state_portfolio
            st.rerun()

        # Rente op Rente Calculator
        st.markdown("---")
        with st.expander("🧮 Rente-op-Rente (Compound Interest) Calculator", expanded=True):
            col_cal1, col_cal2 = st.columns([1, 2])
            with col_cal1:
                st.write("**Scenario Instellingen**")
                start_cap = st.number_input("Startkapitaal (Huidig Portfolio)", value=float(round(total_val_eur, 2)))
                mnd_inleg = st.number_input("Maandelijkse Inleg (€)", value=500)
                jaren = st.slider("Aantal Jaren", 1, 40, 20)
                rendement = st.slider("Verwacht Rendement (%)", 1, 15, 7) / 100
            with col_cal2:
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
                df_comp = pd.DataFrame({"Jaar": years_range, "Portfolio Waarde": values_list, "Eigen Inleg": invested_list})
                fig_comp = px.line(df_comp, x="Jaar", y=["Portfolio Waarde", "Eigen Inleg"], title="Vermogensgroei Projectie")
                st.plotly_chart(fig_comp, use_container_width=True)

        if st.button("⚠️ Portfolio Volledig Wissen"):
            st.session_state['portfolio'] = []
            st.rerun()

# --------------------------------------------------
# TAB 3 & 4 (Ongewijzigd)
# --------------------------------------------------
with tabs[2]:
    st.subheader("❤️ Mijn Wishlist")
    if st.session_state['wishlist']:
        with st.container(border=True):
            for i, item in enumerate(st.session_state['wishlist']):
                ticker = item['ticker']
                name = item['name']
                try:
                    tk = yf.Ticker(ticker)
                    fi = tk.fast_info
                    price = fi['last_price']
                    prev = fi['previous_close']
                    pct_change = ((price - prev) / prev) * 100
                    currency_sym = tk.info.get('currencySymbol', '€')
                    color_style = "green" if pct_change >= 0 else "red"
                    c1, c2, c3, c4 = st.columns([1, 4, 2, 1])
                    c1.write(f"**{ticker}**")
                    c2.write(name)
                    c3.markdown(f"{currency_sym} {price:.2f} (<span style='color:{color_style}'>{pct_change:+.2f}%</span>)", unsafe_allow_html=True)
                    if c4.button("🗑️", key=f"del_wl_{i}"): st.session_state['wishlist'].pop(i); st.rerun()
                    if i < len(st.session_state['wishlist']) - 1: st.divider()
                except: st.error(f"Kon data voor {ticker} niet laden.")
    else: st.info("Je wishlist is nog leeg.")

with tabs[3]:
    st.header("🧮 Werkelijk Rendement & Box 3 Belasting")
    c1, c2 = st.columns(2)
    with c1:
        start_vermogen = st.number_input("Vermogen 1 jan (€)", value=50000.0)
        eind_vermogen = st.number_input("Vermogen 31 dec (€)", value=55000.0)
        netto_inleg = st.number_input("Netto Inleg (€)", value=0.0)
        heffingsvrij = st.number_input("Heffingsvrij (€)", value=57000.0)
    with c2:
        forfaitair = st.number_input("Forfaitair (%)", value=6.04)
        belasting = st.number_input("Belasting (%)", value=36.0)
    st.divider()
    werkelijk = (eind_vermogen - start_vermogen) - netto_inleg
    pct = (werkelijk / start_vermogen * 100) if start_vermogen > 0 else 0
    grondslag = max(0, start_vermogen - heffingsvrij)
    fictief = grondslag * (forfaitair / 100)
    te_betalen = fictief * (belasting / 100)
    r1, r2 = st.columns(2)
    with r1: st.metric("Werkelijk Resultaat", f"€ {werkelijk:,.2f}", f"{pct:.2f}%")
    with r2: st.metric("Te Betalen Box 3", f"€ {te_betalen:,.2f}", delta="- Belasting", delta_color="inverse")
