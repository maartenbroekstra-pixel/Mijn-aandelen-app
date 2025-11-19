import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
import json
from datetime import datetime

# --- CONFIGURATIE ---
st.set_page_config(page_title="Mijn Aandelen Dashboard", layout="wide", page_icon="📈")

# Hulpfunctie: Converteer grote getallen naar leesbaar formaat
def format_large_number(num):
    if num >= 1_000_000_000_000:
        return f"€ {num / 1_000_000_000_000:.2f}T"
    elif num >= 1_000_000_000:
        return f"€ {num / 1_000_000_000:.2f}B"
    elif num >= 1_000_000:
        return f"€ {num / 1_000_000:.2f}M"
    else:
        return f"€ {num:,.0f}"

# Hulpfunctie: Haal wisselkoers op (USD naar EUR)
@st.cache_data(ttl=3600) # Cache voor 1 uur
def get_eur_rate():
    try:
        ticker = yf.Ticker("EUR=X")
        return ticker.history(period="1d")['Close'].iloc[-1]
    except:
        return 0.92 # Fallback

# Hulpfunctie: RSI Berekening
def calculate_rsi(data, window=14):
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# --- DATA LADEN & OPSLAAN ---
if 'portfolio' not in st.session_state:
    st.session_state['portfolio'] = []

def load_portfolio(uploaded_file):
    if uploaded_file is not None:
        try:
            data = json.load(uploaded_file)
            st.session_state['portfolio'] = data
            st.success("Portfolio geladen!")
        except:
            st.error("Fout bij laden bestand.")

def save_portfolio():
    return json.dumps(st.session_state['portfolio'])

# --- ZIJBALK: PORTFOLIO BEHEER ---
st.sidebar.header("💼 Portfolio Beheer")

# Toevoegen
with st.sidebar.expander("Aandeel Toevoegen", expanded=False):
    with st.form("add_stock"):
        new_ticker = st.text_input("Ticker (bijv. ASML.DE, MSFT)", value="").upper().strip()
        new_amount = st.number_input("Aantal stuks", min_value=0.0, value=1.0, step=0.1)
        new_price = st.number_input("Gem. Aankoopkoers (€)", min_value=0.0, value=100.0, step=0.1)
        submitted = st.form_submit_button("Toevoegen")
        
        if submitted and new_ticker:
            st.session_state['portfolio'].append({
                "ticker": new_ticker,
                "amount": new_amount,
                "avg_price": new_price
            })
            st.success(f"{new_ticker} toegevoegd!")
            st.rerun()

# Backup Functie
st.sidebar.markdown("---")
st.sidebar.subheader("💾 Backup")
st.sidebar.info("Op Streamlit Cloud wordt data gereset na herstarten. Download je portfolio hieronder om het te bewaren.")
json_data = save_portfolio()
st.sidebar.download_button("Download Portfolio", json_data, "mijn_portfolio.json", "application/json")
uploaded_file = st.sidebar.file_uploader("Upload Portfolio", type=['json'])
if uploaded_file:
    load_portfolio(uploaded_file)

# --- NAVIGATIE ---
mode = st.radio("Kies weergave:", ["🔎 Analyse Dashboard", "📊 Mijn Portfolio Overzicht"], horizontal=True)

# ==================================================
# DEEL 1: ANALYSE DASHBOARD
# ==================================================
if mode == "🔎 Analyse Dashboard":
    # Zoekbalk en Periode Selectie
    col_search, col_period = st.columns([2, 1])
    with col_search:
        ticker_input = st.text_input("Zoek Aandeel (Ticker):", value="ASML.DE", placeholder="Bijv. SHEL.DE, TSLA, VWRL.AS").upper()
        st.caption("💡 Tip voor Xetra: gebruik **.DE** (bijv. ADS.DE). Voor Amsterdam: **.AS**")
    
    with col_period:
        period = st.selectbox("Periode:", ["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"], index=3)

    if ticker_input:
        try:
            stock = yf.Ticker(ticker_input)
            info = stock.info
            hist = stock.history(period=period)
            
            # Wisselkoers check
            currency = info.get('currency', 'EUR')
            eur_rate = 1.0
            if currency == 'USD':
                eur_rate = 1 / get_eur_rate() # USD naar EUR correctie
            
            current_price = info.get('currentPrice', hist['Close'].iloc[-1]) * eur_rate
            
            # Header
            st.title(f"{info.get('longName', ticker_input)}")
            st.markdown(f"**Sector:** {info.get('sector', 'Onbekend')} | **Industrie:** {info.get('industry', 'Onbekend')} | **Beurs:** {info.get('exchange', 'Onbekend')}")
            st.metric("Huidige Koers", f"€ {current_price:.2f}")
            
            st.markdown("---")

            # --- METRICS BEREKENEN ---
            # We proberen zoveel mogelijk 'None' te voorkomen
            pe = info.get('trailingPE')
            eps = info.get('trailingEps')
            book_val = info.get('bookValue')
            
            # PEG Zelf berekenen als fallback (PE / Growth)
            peg = info.get('pegRatio')
            if peg is None and pe is not None and 'earningsGrowth' in info:
                 if info['earningsGrowth']:
                    peg = pe / (info['earningsGrowth'] * 100)

            # RSI Berekenen
            rsi_series = calculate_rsi(hist)
            current_rsi = rsi_series.iloc[-1] if not rsi_series.empty else 50

            # P/B Zelf berekenen
            pb = info.get('priceToBook')
            if pb is None and book_val:
                # Converteren naar originele valuta voor berekening
                orig_price = info.get('currentPrice', hist['Close'].iloc[-1])
                pb = orig_price / book_val

            # --- VISUELE METRICS ---
            st.subheader("🚦 Fundamentele Check")
            
            m1, m2, m3, m4 = st.columns(4)
            
            # 1. PEG
            with m1:
                val = peg if peg else 0
                color = "🟢" if 0 < val < 1 else ("🟡" if val < 2 else "🔴")
                st.metric("PEG Ratio", f"{color} {val:.2f}" if peg else "N/A", help="Onder 1.0 is ondergewaardeerd t.o.v. groei.")

            # 2. ROE
            with m2:
                roe = info.get('returnOnEquity')
                val = roe if roe else 0
                color = "🟢" if val > 0.15 else ("🟡" if val > 0.10 else "🔴")
                st.metric("ROE", f"{color} {val:.1%}" if roe else "N/A", help="Rendement op eigen vermogen. >15% is sterk management.")

            # 3. P/B
            with m3:
                val = pb if pb else 0
                sector = info.get('sector', '').lower()
                color = "⚪" # Default
                if "tech" not in sector:
                    color = "🟢" if val < 1.5 else ("🟡" if val < 3 else "🔴")
                st.metric("P/B Ratio", f"{color} {val:.2f}" if pb else "N/A", help="Prijs t.o.v. boekwaarde. Tech mag hoog zijn, banken laag (<1).")
            
            # 4. RSI
            with m4:
                color = "🟢" if current_rsi < 30 else ("🔴" if current_rsi > 70 else "⚪")
                st.metric("RSI (14)", f"{color} {current_rsi:.0f}", help="<30 = Oversold (Koopkans?), >70 = Overbought (Duur?)")

            m5, m6, m7, m8 = st.columns(4)

            # 5. Schuld/Eigen Vermogen
            with m5:
                de = info.get('debtToEquity')
                val = de/100 if de else 0
                color = "🟢" if val < 1.0 else ("🟡" if val < 2.0 else "🔴")
                st.metric("Debt/Equity", f"{color} {val:.2f}" if de else "N/A", help="Schuld t.o.v. eigen vermogen. <1.0 is veilig.")

            # 6. Current Ratio
            with m6:
                cr = info.get('currentRatio')
                color = "🟢" if cr and cr > 1.5 else ("🔴" if cr and cr < 1.0 else "🟡")
                st.metric("Current Ratio", f"{color} {cr:.2f}" if cr else "N/A", help="Kan het bedrijf rekeningen betalen? >1.5 is veilig.")

            # 7. Beta
            with m7:
                beta = info.get('beta')
                color = "🔵" if beta and 0.8 < beta < 1.2 else ("🟠" if beta else "⚪")
                st.metric("Beta", f"{beta:.2f}" if beta else "1.00", help="Volatiliteit. 1.0 = markt. >1.5 is wild.")

            # 8. Dividend
            with m8:
                div = info.get('dividendYield')
                st.metric("Dividend", f"{div:.1%}" if div else "0%", help="Jaarlijks dividendpercentage.")

            # --- GRAFIEK ---
            st.markdown("### 📈 Koersverloop")
            fig = go.Figure()
            # Kleur groen als laatste prijs hoger is dan eerste prijs in periode
            line_color = '#00C805' if hist['Close'].iloc[-1] >= hist['Close'].iloc[0] else '#FF5000'
            
            fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], mode='lines', name='Koers', line=dict(color=line_color, width=2)))
            fig.update_layout(
                template="plotly_dark",
                margin=dict(l=0, r=0, t=30, b=0),
                height=350,
                xaxis_title="",
                yaxis_title="Prijs"
            )
            st.plotly_chart(fig, use_container_width=True)

            # --- NIEUWS ---
            st.markdown("### 📰 Laatste Nieuws")
            news = stock.news
            if news:
                for item in news[:3]: # Toon max 3 items
                    with st.expander(f"{item.get('title')}"):
                        st.write(f"Bron: {item.get('publisher')} | {datetime.fromtimestamp(item.get('providerPublishTime')).strftime('%d-%m %H:%M')}")
                        if 'link' in item:
                            st.markdown(f"[Lees artikel]({item['link']})")
            else:
                st.info("Geen recent nieuws gevonden.")

        except Exception as e:
            st.error(f"Kon data niet laden voor {ticker_input}. Check de ticker (gebruik .DE voor Duitsland). Error: {e}")

# ==================================================
# DEEL 2: MIJN PORTFOLIO
# ==================================================
elif mode == "📊 Mijn Portfolio Overzicht":
    st.title("Mijn Aandelen")

    if not st.session_state['portfolio']:
        st.info("Je portfolio is leeg. Voeg aandelen toe in de zijbalk! 👈")
    else:
        portfolio_data = []
        total_value_eur = 0
        total_cost_eur = 0
        sector_dist = {}

        # Progress bar voor laden
        progress_text = "Koersen ophalen..."
        my_bar = st.progress(0, text=progress_text)
        total_items = len(st.session_state['portfolio'])

        eur_rate_usd = get_eur_rate()

        for i, item in enumerate(st.session_state['portfolio']):
            ticker_sym = item['ticker']
            amount = item['amount']
            avg_price_eur = item['avg_price'] # We gaan er vanuit dat invoer in EUR is voor eenvoud

            try:
                stock = yf.Ticker(ticker_sym)
                hist = stock.history(period="1d")
                info = stock.info
                
                if not hist.empty:
                    # Valuta Conversie
                    raw_price = hist['Close'].iloc[-1]
                    currency = info.get('currency', 'EUR')
                    
                    conversion_rate = 1.0
                    if currency == 'USD':
                        conversion_rate = 1 / eur_rate_usd
                    elif currency == 'GBP':
                        # Simpele fallback, idealiter haal je GBP=X op
                        conversion_rate = 1.17 
                    
                    current_price_eur = raw_price * conversion_rate
                    
                    # Berekeningen
                    value_eur = current_price_eur * amount
                    cost_eur = avg_price_eur * amount
                    profit_eur = value_eur - cost_eur
                    profit_pct = ((value_eur - cost_eur) / cost_eur) * 100 if cost_eur > 0 else 0
                    
                    # Sector data
                    sector = info.get('sector', 'Overig')
                    sector_dist[sector] = sector_dist.get(sector, 0) + value_eur

                    portfolio_data.append({
                        "Ticker": ticker_sym,
                        "Bedrijf": info.get('shortName', ticker_sym),
                        "Sector": sector,
                        "Aantal": amount,
                        "Koers (€)": round(current_price_eur, 2),
                        "GAK (€)": round(avg_price_eur, 2),
                        "Waarde (€)": round(value_eur, 2),
                        "Rendement (€)": round(profit_eur, 2),
                        "Rendement (%)": round(profit_pct, 2)
                    })

                    total_value_eur += value_eur
                    total_cost_eur += cost_eur
            except Exception as e:
                st.error(f"Fout bij laden {ticker_sym}: {e}")
            
            my_bar.progress((i + 1) / total_items)
        
        my_bar.empty()

        # --- TOTAAL OVERZICHT ---
        tot_profit = total_value_eur - total_cost_eur
        tot_profit_pct = (tot_profit / total_cost_eur) * 100 if total_cost_eur > 0 else 0

        k1, k2, k3 = st.columns(3)
        k1.metric("Totale Waarde", f"€ {total_value_eur:,.2f}")
        k2.metric("Totaal Rendement", f"€ {tot_profit:,.2f}", delta=f"{tot_profit_pct:.1f}%")
        k3.metric("Geïnvesteerd", f"€ {total_cost_eur:,.2f}")

        st.markdown("---")

        # --- GRAFIEKEN EN TABELLEN ---
        col_chart, col_table = st.columns([1, 2])

        with col_chart:
            st.subheader("Sector Verdeling")
            if sector_dist:
                fig = px.pie(values=list(sector_dist.values()), names=list(sector_dist.keys()), hole=0.4)
                fig.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=300)
                st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.subheader("Details")
            if portfolio_data:
                df = pd.DataFrame(portfolio_data)
                
                # Gebruik st.dataframe met column_config voor betere styling (geen df.style error meer)
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Rendement (%)": st.column_config.NumberColumn(
                            "Rendement %",
                            format="%.1f%%",
                        ),
                        "Rendement (€)": st.column_config.NumberColumn(
                            "Winst/Verlies",
                            format="€ %.2f",
                        ),
                         "Waarde (€)": st.column_config.ProgressColumn(
                            "Waarde",
                            format="€ %.2f",
                            min_value=0,
                            max_value=max(df["Waarde (€)"])
                        ),
                    }
                )
                
                # Knop om portfolio te wissen
                if st.button("🗑️ Alles wissen"):
                    st.session_state['portfolio'] = []
                    st.rerun()
