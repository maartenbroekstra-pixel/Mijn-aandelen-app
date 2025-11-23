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

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- API KEYS ---
FMP_KEY = "nhXfJ0SDaXmUEVKy5icfcwzQOHqOn5QE"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# --- CONSTANTEN & MAPPINGS ---
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING", "MT.AS": "MT",
    "DSM.AS": "DSM", "BESI.AS": "BESIY", "NOVO-B.CO": "NVO", "VOW3.DE": "VWAGY"
}

TOOLTIPS = {
    "peg": "Price/Earnings to Growth.\n🟢 < 1.5: Ondergewaardeerd.\n🔴 > 2.5: Overgewaardeerd.",
    "pe": "Koers/Winst verhouding.\n🟢 < 20: Koopje.\n🟠 > 30: Premie.",
    "roe": "Rendement op Eigen Vermogen.\n🟢 > 15%: Sterk.",
    "div": "Dividend Rendement.\n🟢 > 3%: Interessant.",
}

# --- STATE ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []

# --- HELPERS ---

@st.cache_data(ttl=300)
def get_currency_rate(from_curr, to_curr="EUR"):
    """Haalt wisselkoers op."""
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        # Yahoo format: EURUSD=X of EUR=X
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD" and rate > 1.10: return 1/rate # Sanity check
            return rate
        return 1.0
    except: return 1.0 # Fallback

def search_yahoo_strict(query):
    """Zoekt aandelen, sorteert EU bovenaan."""
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=DE&quotesCount=10"
        r = requests.get(url, headers=HEADERS, timeout=3)
        d = r.json()
        raw = d.get('quotes', [])
        
        def sort_key(q):
            s = q.get('symbol', '')
            if s.endswith('.AS'): return 0
            if s.endswith('.DE') or s.endswith('.F'): return 1
            if '.' not in s: return 10
            return 20
        
        raw.sort(key=sort_key)
        return [f"{q['symbol']} | {q.get('shortname','')}" for q in raw]
    except: return []

# --- DATA ENGINE ---
class DataEngine:
    def __init__(self, ticker, original_ticker):
        self.ticker = ticker
        self.original_ticker = original_ticker
        self.yf_obj = yf.Ticker(original_ticker)
        self.yf_info = self.yf_obj.info
        self.fmp_ratios = {}
        self.fetch_fmp()

    def fetch_fmp(self):
        try:
            url = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{self.ticker}?apikey={FMP_KEY}"
            r = requests.get(url, timeout=2).json()
            if isinstance(r, list) and r: self.fmp_ratios = r[0]
        except: pass

    def get_metric(self, y_key, f_key, is_pct=False):
        vals = []
        if self.yf_info.get(y_key): vals.append(float(self.yf_info[y_key]))
        if self.fmp_ratios.get(f_key): vals.append(float(self.fmp_ratios[f_key]))
        
        if not vals: return None
        avg = sum(vals)/len(vals)
        if is_pct and avg < 0.9: avg *= 100
        return avg

# --- AI CALC ---
def calculate_ai_score(d):
    score = 5.0
    points = 0
    count = 0
    
    checks = [
        (d.get('peg'), 0, 1.5), (d.get('pe'), 0, 20),
        (d.get('roe'), 15, 100), (d.get('margin'), 15, 100),
        (d.get('de'), 0, 1.0), (d.get('cr'), 1.5, 10)
    ]
    
    for val, min_v, max_v in checks:
        if val is not None:
            count += 1
            if min_v < val < max_v: points += 1
            elif val < 0: points -= 0.5 # Strafpunten
            
    if count > 0: score = (points / count) * 10
    return max(1.0, min(10.0, round(score if score > 0 else 5.0, 1)))

# --- TABS SETUP ---
st.sidebar.header("📂 Data")
if st.sidebar.button("🧹 Reset"): st.cache_data.clear(); st.rerun()
tabs = st.tabs(["🔎 Analyse", "📊 Portfolio Pro", "❤️ Wishlist", "🇳🇱 Belasting"])

# ==================================================
# 1. ANALYSE
# ==================================================
with tabs[0]:
    c_s, c_p = st.columns([3, 1])
    query = st.text_input("Zoek aandeel:", key="search")
    sel = None
    if query:
        res = search_yahoo_strict(query)
        sel = st.selectbox("Selecteer:", res).split(" | ")[0] if res else query.upper()
    
    if sel:
        try:
            # Data laden
            us_ticker = EU_TO_US_MAP.get(sel, sel.split('.')[0] if '.' in sel else sel)
            engine = DataEngine(us_ticker, sel)
            info = engine.yf_info
            
            # Metrics
            metrics = {
                'peg': engine.get_metric('pegRatio', 'pegRatioTTM'),
                'pe': engine.get_metric('trailingPE', 'peRatioTTM'),
                'roe': engine.get_metric('returnOnEquity', 'returnOnEquityTTM', True),
                'div': engine.get_metric('dividendYield', 'dividendYielTTM', True),
                'margin': engine.get_metric('profitMargins', 'netProfitMarginTTM', True),
                'de': engine.get_metric('debtToEquity', 'debtEquityRatioTTM'),
                'cr': engine.get_metric('currentRatio', 'currentRatioTTM')
            }
            score = calculate_ai_score(metrics)
            
            # Header
            curr_sym = info.get('currencySymbol', '€')
            curr_code = info.get('currency', 'EUR')
            price = info.get('currentPrice') or info.get('regularMarketPreviousClose') or 0
            
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 1, 1])
                c1.title(f"{info.get('shortName', sel)}")
                c1.caption(f"{sel} | Valuta: {curr_code}")
                c2.metric("Prijs", f"{curr_sym}{price:.2f}")
                
                col = "green" if score >= 7 else "orange" if score >= 5 else "red"
                c3.markdown(f"<span style='color:{col};font-size:24px;font-weight:bold'>AI: {score}</span>", unsafe_allow_html=True)

            # Metrics Grid
            col1, col2, col3, col4 = st.columns(4)
            def m(l, v, k, s="%"):
                if v: col1.metric(l, f"{v:.2f}{s}") if k in ['pe','peg','de','cr'] else col1.metric(l, f"{v:.1f}%")
            
            with st.container(border=True):
                cc1, cc2, cc3, cc4 = st.columns(4)
                with cc1: st.metric("P/E", f"{metrics['pe']:.1f}" if metrics['pe'] else "-")
                with cc2: st.metric("PEG", f"{metrics['peg']:.2f}" if metrics['peg'] else "-")
                with cc3: st.metric("ROE", f"{metrics['roe']:.1f}%" if metrics['roe'] else "-", delta="Goal > 15%")
                with cc4: st.metric("Div Yield", f"{metrics['div']:.2f}%" if metrics['div'] else "-")

            # TOEVOEGEN AAN PORTFOLIO (Met Valuta Conversie)
            st.divider()
            with st.popover("➕ Toevoegen aan Portfolio", use_container_width=True):
                st.subheader("Transactie toevoegen")
                with st.form("add_form"):
                    amount = st.number_input("Aantal", min_value=1.0, value=10.0)
                    
                    # Valuta Keuze
                    val_choice = st.radio("Invoerprijs is in:", [f"Origineel ({curr_code})", "Euro (€)"], horizontal=True)
                    
                    input_price = st.number_input(f"Prijs per stuk", value=float(price))
                    
                    if st.form_submit_button("Opslaan"):
                        final_price = input_price
                        
                        # Conversie logica als gebruiker EUR kiest maar aandeel USD is
                        if "Euro" in val_choice and curr_code != "EUR":
                            rate_eur_to_native = get_currency_rate("EUR", curr_code) # Haal koers op (bijv 1 EUR = 1.05 USD)
                            final_price = input_price * rate_eur_to_native
                            st.info(f"Omgerekend: €{input_price} = {final_price:.2f} {curr_code}")
                        
                        st.session_state['portfolio'].append({
                            'ticker': sel,
                            'amount': amount,
                            'avg_price': final_price, # Altijd opslaan in native currency voor consistente winst berekening
                            'currency': curr_code,
                            'symbol': curr_sym
                        })
                        st.success("Toegevoegd!")
                        st.rerun()

        except Exception as e: st.error(f"Fout: {e}")

# ==================================================
# 2. PORTFOLIO PRO
# ==================================================
with tabs[1]:
    if not st.session_state['portfolio']:
        st.info("Nog geen aandelen. Voeg ze toe via de Analyse tab.")
    else:
        # --- DATA PROCESSING (Batch) ---
        p_data = []
        total_value_eur = 0
        total_div_eur = 0
        sectors = {}
        
        # We gebruiken een progress bar omdat we live data ophalen
        prog = st.progress(0)
        
        for i, item in enumerate(st.session_state['portfolio']):
            t = item['ticker']
            curr = item.get('currency', 'EUR')
            
            # Live data fetchen
            try:
                tk = yf.Ticker(t)
                live_price = tk.fast_info['last_price']
                info = tk.info
                sector = info.get('sector', 'Onbekend')
                
                # Dividend (check trailing of yield)
                div_rate = info.get('dividendRate') # Bedrag per aandeel
                if not div_rate:
                    yld = info.get('dividendYield', 0)
                    div_rate = live_price * yld if yld else 0
            except: 
                live_price = item['avg_price']
                sector = "Onbekend"
                div_rate = 0

            # Conversie naar EUR voor totalen
            rate_to_eur = get_currency_rate(curr, "EUR")
            
            val_native = live_price * item['amount']
            cost_native = item['avg_price'] * item['amount']
            div_native = div_rate * item['amount']
            
            val_eur = val_native * rate_to_eur
            div_eur = div_native * rate_to_eur
            
            total_value_eur += val_eur
            total_div_eur += div_eur
            
            # Sector data
            sectors[sector] = sectors.get(sector, 0) + val_eur

            p_data.append({
                "Ticker": t,
                "Aantal": item['amount'],
                "Prijs": f"{item.get('symbol','')}{live_price:.2f}",
                "GAK": f"{item.get('symbol','')}{item['avg_price']:.2f}",
                "Waarde (EUR)": val_eur, # Voor sortering
                "Waarde": f"€ {val_eur:,.2f}",
                "Winst": val_eur - (cost_native * rate_to_eur),
                "Div/Jaar": f"€ {div_eur:.2f}",
                "Sector": sector
            })
            prog.progress((i+1)/len(st.session_state['portfolio']))
        
        prog.empty()
        
        # --- DASHBOARD METRICS ---
        st.markdown("### 🚀 Portfolio Overzicht")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Totale Waarde", f"€ {total_value_eur:,.2f}")
        k2.metric("Geschat Dividend/Jaar", f"€ {total_div_eur:,.2f}", delta="Passive Income")
        if total_value_eur > 0:
            yld_tot = (total_div_eur / total_value_eur) * 100
            k3.metric("Portfolio Yield", f"{yld_tot:.2f}%")
        
        # --- VISUALS ---
        st.divider()
        c_vis1, c_vis2 = st.columns(2)
        
        with c_vis1:
            st.subheader("Allocatie & Sector")
            fig_pie = px.pie(names=list(sectors.keys()), values=list(sectors.values()), hole=0.4, title="Sector Verdeling")
            st.plotly_chart(fig_pie, use_container_width=True)
            
        with c_vis2:
            st.subheader("Dividend Kampioenen")
            # Maak dataset voor grafiek
            df_chart = pd.DataFrame(p_data).sort_values("Waarde (EUR)", ascending=False)
            # Dividend Bar Chart
            fig_bar = px.bar(df_chart, x='Ticker', y='Div/Jaar', title="Dividend per Positie (in €)", color='Waarde (EUR)')
            st.plotly_chart(fig_bar, use_container_width=True)

        # --- TABEL ---
        st.subheader("📜 Details")
        df_display = pd.DataFrame(p_data)
        
        # Kleurcodering voor winst
        def color_profit(val):
            color = '#d4edda' if val >= 0 else '#f8d7da' # Lichtgroen / Lichtrood
            return f'background-color: {color}'

        st.dataframe(
            df_display[['Ticker', 'Aantal', 'Prijs', 'GAK', 'Waarde', 'Winst', 'Div/Jaar', 'Sector']]
            .style.applymap(color_profit, subset=['Winst'])
            .format({"Winst": "€ {:.2f}"}),
            use_container_width=True,
            hide_index=True
        )

        # --- RENTE OP RENTE CALCULATOR ---
        st.markdown("---")
        with st.expander("🧮 Rente-op-Rente Calculator (Compound Interest)", expanded=True):
            [attachment_0](attachment)
            col_cal1, col_cal2 = st.columns([1, 2])
            
            with col_cal1:
                st.write("**Instellingen**")
                init_inv = st.number_input("Startbedrag", value=float(round(total_value_eur, 2)))
                monthly = st.number_input("Maandelijkse Inleg", value=500)
                years = st.slider("Aantal Jaren", 5, 40, 20)
                rate = st.slider("Verwacht Rendement (%)", 1, 15, 7) / 100
            
            with col_cal2:
                # Berekening
                future_values = []
                months = range(years * 12)
                current_val = init_inv
                total_contrib = init_inv
                
                for m in months:
                    current_val = current_val * (1 + rate/12) + monthly
                    total_contrib += monthly
                    if m % 12 == 0: # Jaarlijks datapunt
                        future_values.append(current_val)
                
                final_val = current_val
                winst_rente = final_val - total_contrib
                
                st.metric("Eindbedrag over " + str(years) + " jaar", f"€ {final_val:,.2f}")
                st.write(f"Totaal ingelegd: € {total_contrib:,.2f} | **Rente-op-rente winst: € {winst_rente:,.2f}**")
                
                # Grafiek
                chart_data = pd.DataFrame({
                    "Jaar": range(len(future_values)),
                    "Waarde": future_values
                })
                fig_comp = px.line(chart_data, x="Jaar", y="Waarde", title="Vermogensgroei", markers=True)
                fig_comp.update_traces(line_color='#00CC96', fill='tozeroy')
                st.plotly_chart(fig_comp, use_container_width=True)

        if st.button("🗑️ Portfolio Wissen"):
            st.session_state['portfolio'] = []
            st.rerun()

# ==================================================
# 3. WISHLIST
# ==================================================
with tabs[2]:
    if st.session_state['wishlist']:
        for w in st.session_state['wishlist']:
            st.markdown(f"⭐ **{w['name']}** ({w['ticker']})")
            if st.button("🔎 Analyseer", key=f"btn_{w['ticker']}"):
                st.session_state['search'] = w['ticker'] # Werkt als key in text_input
                st.rerun()
            st.divider()
    else: st.info("Je wishlist is leeg.")

# ==================================================
# 4. BELASTING
# ==================================================
with tabs[3]:
    st.header("Box 3 Tool")
    verm = st.number_input("Totaal Vermogen", value=50000)
    vrij = 57000
    if verm > vrij:
        belast = verm - vrij
        tax = belast * 0.0604 * 0.36 # 2024 regels
        st.error(f"Te betalen: € {tax:.2f}")
    else:
        st.success("Belastingvrij!")
