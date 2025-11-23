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

# ==================================================
# 1. CONFIGURATIE & CONSTANTEN
# ==================================================
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# Headers om blokkades te voorkomen
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Mapping Europese tickers -> US (voor betere data indien nodig)
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD", "SHELL.AS": "SHEL",
    "WKL.AS": "WTKWY", "9PDA.DU": "PROSY", "AHLA.F": "BABA"
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

# Jouw portfolio data (Leeg gemaakt zoals gevraagd)
DEFAULT_PORTFOLIO = []

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
def get_basic_portfolio_data(ticker):
    """
    Haalt basisdata op voor de portfolio tabel (snel).
    """
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        
        q_type = info.get('quoteType', 'EQUITY').upper()
        if 'ETF' in q_type: q_type = 'ETF'
        else: q_type = 'Aandeel'
            
        return {
            'type': q_type,
            'sector': info.get('sector', 'ETF/Overig'),
            'live_price': info.get('currentPrice') or info.get('regularMarketPreviousClose'),
            'currency_symbol': info.get('currencySymbol', '€'),
            'shortName': info.get('shortName', ticker),
            'dividendRate': info.get('dividendRate', 0),
            'dividendYield': info.get('dividendYield', 0)
        }
    except Exception:
        return None

@st.cache_data(ttl=3600)
def get_earnings_history_data(ticker):
    """
    Haalt specifieke earnings history op voor de grafiek (zoals in screenshot).
    """
    try:
        # Gebruik US mapping voor betere earnings data (Europese tickers missen vaak historie in Yahoo)
        us_ticker = EU_TO_US_MAP.get(ticker, ticker)
        tk = yf.Ticker(us_ticker)
        
        # Haal earnings dates op
        dates = tk.earnings_dates
        if dates is None or dates.empty:
            return None
            
        # Tijdzones verwijderen om warnings te voorkomen
        if dates.index.tz is not None:
            dates.index = dates.index.tz_localize(None)
            
        # Filter: 1.5 jaar terug en 1 jaar vooruit
        now = datetime.now()
        start_date = now - timedelta(days=550)
        end_date = now + timedelta(days=365)
        
        mask = (dates.index >= start_date) & (dates.index <= end_date)
        df_filtered = dates.loc[mask].copy()
        
        # Sorteer op datum
        df_filtered = df_filtered.sort_index()
        
        return df_filtered
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
        # Headers toegevoegd tegen blokkades
        url = f"https://news.google.com/rss/search?q={query}&hl=nl&gl=NL&ceid=NL:nl"
        r = requests.get(url, headers=HEADERS, timeout=4)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:5]:
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
                st.markdown("##### 📰 Recent Nieuws")
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
# TAB 2: PORTFOLIO PRO (VERNIEUWD)
# --------------------------------------------------
with tabs[1]:
    if not st.session_state['portfolio']:
        st.info("Je portfolio is leeg. Voeg aandelen toe via de Analyse tab.")
    else:
        st.markdown("### 📊 Portfolio Dashboard Pro")
        
        portfolio_data = []
        total_val_eur = 0
        total_cost_eur = 0
        
        # 1. Tabel Data Genereren
        for item in st.session_state['portfolio']:
            ticker = item['ticker']
            basic_data = get_basic_portfolio_data(ticker)
            
            # Fallbacks
            live_price = basic_data['live_price'] if basic_data and basic_data['live_price'] else item['avg_price']
            short_name = basic_data['shortName'] if basic_data else ticker
            
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
            
            total_val_eur += val_eur
            total_cost_eur += cost_eur
            
            portfolio_data.append({
                "Ticker": ticker,
                "Naam": short_name,
                "Aantal": float(amount),
                "GAK": float(avg_price),
                "Prijs": live_price,
                "Waarde (€)": val_eur,
                "Winst (€)": profit_eur,
                "Valuta": symbol,
                "_currency_code": currency,
                "Verwijder": False
            })

        # KPI's
        k1, k2, k3 = st.columns(3)
        k1.metric("Totale Waarde", f"€ {total_val_eur:,.2f}")
        abs_profit = total_val_eur - total_cost_eur
        pct_profit = (abs_profit / total_cost_eur * 100) if total_cost_eur else 0
        k2.metric("Totaal Rendement", f"€ {abs_profit:,.2f}", delta=f"{pct_profit:.1f}%")
        k3.metric("Aantal Posities", len(portfolio_data))
        
        st.divider()

        # 2. Editor Tabel
        df_editor_source = pd.DataFrame(portfolio_data)
        column_settings = {
            "Ticker": st.column_config.TextColumn("Ticker", disabled=True),
            "Naam": st.column_config.TextColumn("Naam", disabled=True),
            "Aantal": st.column_config.NumberColumn("Aantal", min_value=0.0001, required=True, step=1.0),
            "GAK": st.column_config.NumberColumn("GAK", format="%.2f"),
            "Waarde (€)": st.column_config.NumberColumn("Waarde (€)", format="€ %.2f", disabled=True),
            "Winst (€)": st.column_config.NumberColumn("Winst (€)", format="€ %.2f", disabled=True),
            "Verwijder": st.column_config.CheckboxColumn("🗑️", default=False)
        }
        edited_df = st.data_editor(df_editor_source, column_config=column_settings, 
                                 column_order=("Verwijder", "Ticker", "Naam", "Aantal", "GAK", "Waarde (€)", "Winst (€)"), 
                                 hide_index=True, use_container_width=True, key="main_portfolio_editor")

        # Save changes if any
        if not df_editor_source.equals(edited_df):
            new_state_portfolio = []
            for index, row in edited_df.iterrows():
                if not row['Verwijder']:
                    new_state_portfolio.append({
                        'ticker': row['Ticker'], 
                        'amount': row['Aantal'], 
                        'avg_price': row['GAK'], 
                        'currency': row['_currency_code'], 
                        'symbol': row['Valuta']
                    })
            st.session_state['portfolio'] = new_state_portfolio
            st.rerun()

        st.markdown("---")
        
        # 3. INTERACTIEVE DETAILS SECTIE
        st.header("📉 Detail Analyse & Earnings")
        
        # Selectbox voor aandeel keuze (maakt het interactief zoals gevraagd)
        ticker_list = [f"{p['Ticker']} - {p['Naam']}" for p in portfolio_data]
        selected_option = st.selectbox("Selecteer een aandeel voor details:", ticker_list)
        
        if selected_option:
            sel_ticker = selected_option.split(" - ")[0]
            sel_name = selected_option.split(" - ")[1]
            
            col_chart, col_info = st.columns([2, 1])
            
            with col_chart:
                st.subheader(f"Earnings: {sel_name}")
                earnings_df = get_earnings_history_data(sel_ticker)
                
                if earnings_df is not None and not earnings_df.empty:
                    # Data voorbereiden voor Plotly Scatter
                    earnings_df['Period'] = earnings_df.index.strftime('%Y-%m-%d')
                    
                    fig = go.Figure()

                    # Verwachte EPS (Lichtblauw)
                    fig.add_trace(go.Scatter(
                        x=earnings_df.index, 
                        y=earnings_df['EPS Estimate'],
                        mode='markers',
                        name='Verwacht',
                        marker=dict(size=14, color='#a9cce3', line=dict(width=2, color='#2980b9')),
                        text=[f"Verwacht: {x:.2f}" if pd.notnull(x) else "" for x in earnings_df['EPS Estimate']],
                        hoverinfo='text+x'
                    ))

                    # Werkelijke EPS (Donkerblauw)
                    fig.add_trace(go.Scatter(
                        x=earnings_df.index, 
                        y=earnings_df['Reported EPS'],
                        mode='markers',
                        name='Werkelijk',
                        marker=dict(size=14, color='#2980b9'),
                        text=[f"Werkelijk: {x:.2f}" if pd.notnull(x) else "" for x in earnings_df['Reported EPS']],
                        hoverinfo='text+x'
                    ))

                    # Opmaak (Clean look zoals screenshot)
                    fig.update_layout(
                        title="Earnings: Verwacht vs. Werkelijk",
                        xaxis_title="Datum",
                        yaxis_title="EPS",
                        template="plotly_white",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                        hovermode="x unified"
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # Beat/Miss Berekening van laatste rapportage
                    now = datetime.now()
                    past_reports = earnings_df[earnings_df.index < now]
                    if not past_reports.empty:
                        last = past_reports.iloc[-1]
                        if pd.notnull(last['Reported EPS']) and pd.notnull(last['EPS Estimate']):
                            diff = last['Reported EPS'] - last['EPS Estimate']
                            pct = (diff / abs(last['EPS Estimate']) * 100) if last['EPS Estimate'] != 0 else 0
                            color = "green" if diff >= 0 else "red"
                            txt = "BEAT" if diff >= 0 else "MISS"
                            st.markdown(f"**Laatste Resultaat:** <span style='color:{color}; font-weight:bold'>{txt}</span> met {abs(pct):.1f}%", unsafe_allow_html=True)
                else:
                    st.info("Geen gedetailleerde earnings historie beschikbaar voor dit aandeel.")

            with col_info:
                st.subheader("📰 Relevant Nieuws")
                news = get_google_news(f"{sel_name} stock news")
                if news:
                    with st.container(border=True):
                        for n in news:
                            st.markdown(f"**[{n['title']}]({n['link']})**")
                            st.caption(f"📅 {n['date']}")
                            st.divider()
                else:
                    st.write("Geen recent nieuws gevonden.")

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
