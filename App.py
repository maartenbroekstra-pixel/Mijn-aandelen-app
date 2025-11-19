import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

# --- JOUW PORTEFEUILLE (PAS DIT AAN!) ---
# Format: 'TICKER': {'shares': AANTAL, 'avg_price': AANKOOPPRIJS}
my_portfolio = {
    "ASML.AS": {"shares": 10, "avg_price": 650.00},
    "MSFT":    {"shares": 5,  "avg_price": 310.50},
    "SHEL.AS": {"shares": 100,"avg_price": 26.40},
    "TSLA":    {"shares": 8,  "avg_price": 210.00}
}

st.set_page_config(page_title="Mijn Aandelen App", layout="wide")

# --- Navigatie ---
mode = st.sidebar.radio("Menu", ["📊 Mijn Portefeuille", "🔎 Analyse Tool"])

def get_stock_info(ticker):
    try:
        stock = yf.Ticker(ticker)
        return stock.info, stock.history(period="1y")
    except:
        return None, None

# ==========================================
# PAGINA 1: MIJN PORTEFEUILLE
# ==========================================
if mode == "📊 Mijn Portefeuille":
    st.title(f"Mijn Portefeuille")
    
    total_value = 0
    total_invested = 0
    portfolio_data = []

    # Loop door je aandelen heen
    for ticker, data in my_portfolio.items():
        stock = yf.Ticker(ticker)
        history = stock.history(period="1d")
        
        if not history.empty:
            current_price = history['Close'].iloc[-1]
            shares = data['shares']
            avg_price = data['avg_price']
            
            current_val = current_price * shares
            invested_val = avg_price * shares
            profit = current_val - invested_val
            profit_pct = (profit / invested_val) * 100
            
            total_value += current_val
            total_invested += invested_val
            
            portfolio_data.append({
                "Ticker": ticker,
                "Aantal": shares,
                "Prijs": current_price,
                "Waarde": current_val,
                "Winst €": profit,
                "Winst %": profit_pct
            })
    
    # 1. De Grote Cijfers bovenaan
    total_profit = total_value - total_invested
    total_profit_pct = (total_profit / total_invested) * 100 if total_invested > 0 else 0

    k1, k2, k3 = st.columns(3)
    k1.metric("Totale Waarde", f"€ {total_value:,.2f}")
    k2.metric("Totaal Winst/Verlies", f"€ {total_profit:,.2f}", delta=f"{total_profit_pct:.1f}%")
    k3.metric("Geïnvesteerd", f"€ {total_invested:,.2f}")
    
    st.markdown("---")

    # 2. Visueel Overzicht (Taartdiagram)
    if portfolio_data:
        df = pd.DataFrame(portfolio_data)
        
        col_chart, col_table = st.columns([1, 2])
        
        with col_chart:
            st.subheader("Verdeling")
            fig = px.pie(df, values='Waarde', names='Ticker', hole=0.4)
            fig.update_layout(margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(fig, use_container_width=True)
            
        with col_table:
            st.subheader("Details per aandeel")
            # Mooie tabel met styling
            st.dataframe(
                df.style.format({
                    "Prijs": "€ {:.2f}",
                    "Waarde": "€ {:.2f}",
                    "Winst €": "€ {:.2f}",
                    "Winst %": "{:.1f}%"
                }).background_gradient(subset=["Winst %"], cmap="RdYlGn", vmin=-20, vmax=20),
                use_container_width=True,
                hide_index=True
            )

# ==========================================
# PAGINA 2: ANALYSE TOOL (Je eerdere dashboard)
# ==========================================
elif mode == "🔎 Analyse Tool":
    st.sidebar.header("Zoek Nieuw Aandeel")
    ticker_input = st.sidebar.text_input("Ticker:", value="AAPL")
    
    if ticker_input:
        info, history = get_stock_info(ticker_input)
        
        if info:
            st.title(f"{info.get('longName', ticker_input)}")
            current_price = info.get('currentPrice')
            currency = info.get('currency', 'USD')
            st.metric("Prijs", f"{currency} {current_price}")
            
            # --- JOUW STOPLICHT LOGICA ---
            st.subheader("🚦 Fundamentele Check")
            c1, c2, c3, c4 = st.columns(4)
            
            # PEG Ratio
            peg = info.get('pegRatio')
            with c1:
                if peg and 0 < peg < 1: color = "🟢"
                elif peg and peg < 2: color = "🟡"
                else: color = "🔴"
                st.metric("PEG (Groei)", f"{color} {peg}" if peg else "N/A")

            # ROE
            roe = info.get('returnOnEquity')
            with c2:
                if roe and roe > 0.15: color = "🟢"
                elif roe and roe > 0.10: color = "🟡"
                else: color = "🔴"
                st.metric("ROE (Kwaliteit)", f"{color} {roe:.1%}" if roe else "N/A")
                
            # Debt/Equity
            de = info.get('debtToEquity')
            with c3:
                if de and de < 100: color = "🟢" # < 1.0
                elif de and de < 200: color = "🟡"
                else: color = "🔴"
                st.metric("Schuld", f"{color} {de/100:.1f}" if de else "N/A")
                
            # RSI (Simplified calculation)
            delta = history['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs)).iloc[-1]
            
            with c4:
                if rsi < 30: color = "🟢 (Koopje?)"
                elif rsi > 70: color = "🔴 (Duur?)"
                else: color = "⚪ (Neutraal)"
                st.metric("RSI (Timing)", f"{rsi:.0f}", delta=color)

            # Grafiek
            st.markdown("---")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=history.index, y=history['Close'], mode='lines', name='Koers'))
            fig.update_layout(title="Koersverloop (1 Jaar)", yaxis_title="Prijs")
            st.plotly_chart(fig, use_container_width=True)

        else:
            st.error("Geen data gevonden.")
