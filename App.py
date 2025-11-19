import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
import json
import requests
from datetime import datetime

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- HULPFUNCTIES ---

@st.cache_data(ttl=3600)
def get_currency_rate(from_currency, to_currency="EUR"):
    if from_currency == to_currency: return 1.0
    try:
        if from_currency == "USD" and to_currency == "EUR": ticker = "EUR=X"
        elif from_currency == "GBP" and to_currency == "EUR": ticker = "EURGBP=X"
        else: ticker = f"{from_currency}{to_currency}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty: return data['Close'].iloc[-1]
        return 1.0
    except: return 1.0

def calculate_rsi(data, window=14):
    if data.empty: return 50
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def safe_get(info_dict, key, default=None):
    val = info_dict.get(key)
    return val if val is not None else default

# --- NIEUWE FUNCTIE: ZOEKEN OP NAAM ---
def search_yahoo(query):
    """Zoekt tickers op basis van naam via Yahoo API"""
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=6&newsCount=0"
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers)
        data = r.json()
        
        results = []
        if 'quotes' in data:
            for quote in data['quotes']:
                symbol = quote.get('symbol')
                name = quote.get('shortname', quote.get('longname', symbol))
                exchange = quote.get('exchange', 'N/A')
                # Filter optioneel: zet Xetra/Amsterdam bovenaan in je hoofd
                results.append(f"{symbol} | {name} ({exchange})")
        return results
    except:
        return []

# --- DATA MANAGEMENT ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []

def save_portfolio(): return json.dumps(st.session_state['portfolio'])
def load_portfolio(uploaded_file):
    if uploaded_file is not None:
        try:
            data = json.load(uploaded_file)
            st.session_state['portfolio'] = data
            st.success("Portfolio geladen!")
        except: st.error("Fout bij laden.")

# --- ZIJBALK ---
st.sidebar.title("💼 Portfolio Manager")

with st.sidebar.expander("➕ Aandeel Toevoegen", expanded=False):
    st.write("Zoek eerst het aandeel:")
    search_query_port = st.text_input("Naam of Ticker", key="search_port")
    
    selected_ticker_port = None
    if search_query_port:
        results = search_yahoo(search_query_port)
        if results:
            selection = st.selectbox("Kies aandeel:", results, key="select_port")
            selected_ticker_port = selection.split(" | ")[0] # Pak alleen ticker deel
        else:
            st.warning("Geen resultaten.")
    
    with st.form("add_stock_form"):
        st.caption(f"Geselecteerd: {selected_ticker_port if selected_ticker_port else '...'}")
        new_amount = st.number_input("Aantal stuks", min_value=0.0, value=1.0, step=0.1)
        new_price = st.number_input("GAK (in €)", min_value=0.0, value=50.0, step=0.1)
        
        submitted = st.form_submit_button("Toevoegen")
        if submitted and selected_ticker_port:
            st.session_state['portfolio'].append({
                "ticker": selected_ticker_port,
                "amount": new_amount,
                "avg_price_eur": new_price
            })
            st.success(f"{selected_ticker_port} toegevoegd!")
            st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("💾 Backup")
json_data = save_portfolio()
st.sidebar.download_button("Download JSON", json_data, "mijn_portfolio.json", "application/json")
uploaded_file = st.sidebar.file_uploader("Upload JSON", type=['json'])
if uploaded_file: load_portfolio(uploaded_file)

# --- HOOFD NAVIGATIE ---
mode = st.radio("", ["🔎 Analyse & Nieuws", "📊 Mijn Portfolio"], horizontal=True)

# ==================================================
# 🔎 MODULE: ANALYSE
# ==================================================
if mode == "🔎 Analyse & Nieuws":
    col_search, col_period = st.columns([3, 1])
    
    with col_search:
        # --- NIEUWE ZOEKBALK ---
        search_text = st.text_input("Zoek Bedrijf:", placeholder="Typ naam (bv. Siemens, Shell, Nvidia)...")
        
        final_ticker = None
        if search_text:
            search_results = search_yahoo(search_text)
            if search_results:
                # Laat gebruiker kiezen uit resultaten
                selected_item = st.selectbox("Selecteer het juiste aandeel:", search_results)
                final_ticker = selected_item.split(" | ")[0] # Haal ticker eruit
            else:
                st.warning("Geen aandelen gevonden met deze naam.")

    with col_period:
        period = st.selectbox("Grafiek Periode", ["1mo", "3mo", "6mo", "1y", "5y", "max"], index=3)

    if final_ticker:
        try:
            stock = yf.Ticker(final_ticker)
            info = stock.info
            hist = stock.history(period=period)
            
            if hist.empty:
                st.error(f"Geen data voor {final_ticker}.")
            else:
                # Valuta
                curr = safe_get(info, 'currency', 'EUR')
                eur_rate = get_currency_rate(curr, "EUR")
                
                price_eur = hist['Close'].iloc[-1] * eur_rate
                
                # Header
                st.markdown("---")
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.title(safe_get(info, 'shortName', final_ticker))
                    st.caption(f"Ticker: {final_ticker} | Sector: {safe_get(info, 'sector', '-')} | Beurs: {safe_get(info, 'exchange', '-')}")
                with c2:
                    st.metric("Koers (in €)", f"€ {price_eur:.2f}")

                # Metrics berekenen
                peg = safe_get(info, 'pegRatio')
                pe = safe_get(info, 'trailingPE')
                growth = safe_get(info, 'earningsGrowth')
                if peg is None and pe and growth: peg = pe / (growth * 100)
                
                div_yield = safe_get(info, 'dividendYield', 0.0)
                if div_yield is None: div_yield = 0.0

                rsi_val = calculate_rsi(hist).iloc[-1]
                pb = safe_get(info, 'priceToBook', 0)
                de = safe_get(info, 'debtToEquity', 0) / 100

                # Metrics Tonen
                st.subheader("Fundamentele Analyse")
                m1, m2, m3, m4 = st.columns(4)
                
                with m1:
                    val = peg if peg else 0
                    color = "🟢" if 0 < val < 1 else ("🟡" if val < 2 else ("⚪" if val == 0 else "🟠"))
                    st.metric("PEG Ratio", f"{color} {val:.2f}", help="<1 is goedkoop t.o.v. groei")
                
                with m2:
                    roe = safe_get(info, 'returnOnEquity', 0)
                    color = "🟢" if roe > 0.15 else ("🟡" if roe > 0.10 else "🔴")
                    st.metric("ROE", f"{color} {roe:.1%}", help="Winstgevendheid. >15% is sterk.")

                with m3:
                    color = "🟢" if pb < 1.5 and pb > 0 else "⚪"
                    st.metric("P/B Ratio", f"{color} {pb:.2f}", help="Koers / Boekwaarde")

                with m4:
                    color = "🟢" if rsi_val < 30 else ("🔴" if rsi_val > 70 else "⚪")
                    st.metric("RSI (14)", f"{color} {rsi_val:.0f}", help="Timing indicator")

                m5, m6, m7, m8 = st.columns(4)
                with m5:
                    color = "🟢" if de < 1.0 else "🟠"
                    st.metric("Schuld/Eigen Verm.", f"{color} {de:.2f}")
                with m6:
                    beta = safe_get(info, 'beta')
                    st.metric("Beta", f"{beta:.2f}" if beta else "1.0")
                with m7:
                    st.metric("Dividend", f"{div_yield:.2%}")
                with m8:
                    target = safe_get(info, 'targetMeanPrice')
                    t_eur = f"€ {target * eur_rate:.2f}" if target else "N/A"
                    st.metric("Koersdoel", t_eur)

                # Grafiek
                st.markdown("### 📈 Koersverloop")
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], mode='lines', name='Koers', line=dict(color='#00CC96')))
                fig.update_layout(height=400, template="plotly_dark", margin=dict(l=0, r=0, t=30, b=0), yaxis_title=f"Prijs ({curr})")
                st.plotly_chart(fig, use_container_width=True)

                # Nieuws
                st.markdown("### 📰 Nieuws")
                news = stock.news
                if news:
                    for n in news[:5]:
                        st.markdown(f"**{n.get('title')}**")
                        st.caption(f"{n.get('publisher')} - [Lees Artikel]({n.get('link')})")
                        st.divider()
                else:
                    st.info("Geen nieuws gevonden.")

        except Exception as e:
            st.error(f"Error: {e}")

# ==================================================
# 📊 MODULE: PORTFOLIO
# ==================================================
elif mode == "📊 Mijn Portfolio":
    st.title("Mijn Aandelen (in Euro)")
    
    if not st.session_state['portfolio']:
        st.info("Je portfolio is leeg. Voeg aandelen toe via de zijbalk.")
    else:
        rows = []
        total_inv = 0
        total_val = 0
        sectors = {}
        
        bar = st.progress(0)
        for i, item in enumerate(st.session_state['portfolio']):
            ticker = item['ticker']
            amt = item['amount']
            gak = item['avg_price_eur']
            
            try:
                s = yf.Ticker(ticker)
                h = s.history(period="1d")
                if not h.empty:
                    curr_orig = h['Close'].iloc[-1]
                    inf = s.info
                    rate = get_currency_rate(safe_get(inf, 'currency', 'EUR'), "EUR")
                    
                    curr_eur = curr_orig * rate
                    val_eur = curr_eur * amt
                    cost_eur = gak * amt
                    
                    sec = safe_get(inf, 'sector', 'Overig')
                    sectors[sec] = sectors.get(sec, 0) + val_eur
                    
                    rows.append({
                        "Ticker": ticker,
                        "Bedrijf": safe_get(inf, 'shortName', ticker),
                        "Aantal": amt,
                        "GAK (€)": gak,
                        "Koers (€)": curr_eur,
                        "Waarde (€)": val_eur,
                        "W/V (€)": val_eur - cost_eur,
                        "Rend (%)": ((val_eur - cost_eur)/cost_eur)*100
                    })
                    total_inv += cost_eur
                    total_val += val_eur
            except: pass
            bar.progress((i+1)/len(st.session_state['portfolio']))
        bar.empty()

        # Totalen
        res_eur = total_val - total_inv
        res_pct = (res_eur/total_inv)*100 if total_inv > 0 else 0
        
        k1, k2, k3 = st.columns(3)
        k1.metric("Waarde", f"€ {total_val:,.2f}")
        k2.metric("Resultaat", f"€ {res_eur:,.2f}", delta=f"{res_pct:.2f}%")
        k3.metric("Geïnvesteerd", f"€ {total_inv:,.2f}")
        
        st.divider()
        
        c1, c2 = st.columns([1, 2])
        with c1:
            if sectors:
                fig = px.pie(values=list(sectors.values()), names=list(sectors.keys()), hole=0.4)
                fig.update_layout(margin=dict(t=0,b=0,l=0,r=0), height=300)
                st.plotly_chart(fig, use_container_width=True)
        with c2:
            if rows:
                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True, hide_index=True, column_config={
                    "Rend (%)": st.column_config.NumberColumn(format="%.1f%%"),
                    "Koers (€)": st.column_config.NumberColumn(format="€ %.2f"),
                    "GAK (€)": st.column_config.NumberColumn(format="€ %.2f"),
                    "Waarde (€)": st.column_config.NumberColumn(format="€ %.2f"),
                    "W/V (€)": st.column_config.NumberColumn(format="€ %.2f")
                })
                if st.button("Alles Wissen"):
                    st.session_state['portfolio'] = []
                    st.rerun()
