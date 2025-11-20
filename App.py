import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import requests
import json
from datetime import datetime

# --- CONFIGURATIE ---
st.set_page_config(page_title="Mijn Aandelen App", layout="wide", page_icon="📈")

# --- HULPFUNCTIES ---

@st.cache_data(ttl=3600)
def get_currency_rate(from_currency, to_currency="EUR"):
    """Haalt wisselkoers op en cached deze voor 1 uur."""
    if from_currency == to_currency: return 1.0
    if not from_currency or from_currency == 'nan': return 1.0
    
    try:
        # Yahoo tickers voor wisselkoersen
        ticker_map = {
            "USD": "EUR=X",
            "GBP": "EURGBP=X",
            "CNY": "CNYEUR=X",
            "HKD": "HKDEUR=X"
        }
        ticker = ticker_map.get(from_currency, f"{from_currency}{to_currency}=X")
        data = yf.Ticker(ticker).history(period="1d")
        
        if not data.empty:
            return data['Close'].iloc[-1]
        return 1.0 # Fallback
    except:
        return 1.0

def safe_get(info_dict, key, default=None):
    """Haal waarde veilig op uit dictionary."""
    if not info_dict: return default
    val = info_dict.get(key)
    return val if val is not None else default

def search_yahoo(query):
    """Zoekt op bedrijfsnaam en geeft tickers terug."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=5"
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        
        results = []
        if 'quotes' in data:
            for q in data['quotes']:
                symbol = q.get('symbol')
                name = q.get('shortname', q.get('longname', symbol))
                exch = q.get('exchange', 'N/A')
                # Filter optie: sluit indices uit als je wilt
                results.append(f"{symbol} | {name} ({exch})")
        return results
    except:
        return []

# --- PORTFOLIO FUNCTIES ---
if 'portfolio' not in st.session_state:
    st.session_state['portfolio'] = []

def save_portfolio_json():
    return json.dumps(st.session_state['portfolio'])

def load_portfolio_json(uploaded_file):
    if uploaded_file:
        try:
            st.session_state['portfolio'] = json.load(uploaded_file)
            st.success("✅ Portfolio geladen!")
        except:
            st.error("❌ Kon bestand niet lezen.")

# --- ZIJBALK: ALLEEN BESTANDSBEHEER ---
st.sidebar.header("💾 Data Opslag")
st.sidebar.caption("Streamlit onthoudt niks na afsluiten. Download je data hier:")

json_data = save_portfolio_json()
st.sidebar.download_button("⬇️ Download Portfolio", json_data, "portfolio.json", "application/json")

uploaded_file = st.sidebar.file_uploader("⬆️ Upload Portfolio", type=['json'])
if uploaded_file:
    load_portfolio_json(uploaded_file)

st.sidebar.markdown("---")
st.sidebar.info("💡 **Tip:** Zoek aandelen in het hoofdscherm. Daar kun je ze analyseren én direct toevoegen.")

# --- HOOFD NAVIGATIE ---
tabs = st.tabs(["🔎 Analyse & Toevoegen", "📊 Mijn Portfolio Overzicht"])

# ==================================================
# TAB 1: ANALYSE & TOEVOEGEN
# ==================================================
with tabs[0]:
    col_search, col_period = st.columns([3, 1])
    
    with col_search:
        search_text = st.text_input("🔍 Zoek Aandeel (Naam of Ticker):", placeholder="Bijv. JD.com, Shell, Volkswagen...")
        
        selected_ticker = None
        if search_text:
            results = search_yahoo(search_text)
            if results:
                choice = st.selectbox("Selecteer het juiste bedrijf:", results)
                selected_ticker = choice.split(" | ")[0]
            else:
                st.warning("Geen resultaten gevonden. Probeer de exacte ticker.")
    
    with col_period:
        period = st.selectbox("Grafiek:", ["1mo", "6mo", "1y", "3y", "5y"], index=2)

    # --- DATA OPHALEN ---
    if selected_ticker:
        try:
            stock = yf.Ticker(selected_ticker)
            info = stock.info
            hist = stock.history(period=period)
            
            if hist.empty:
                st.error("Geen data beschikbaar voor deze ticker.")
            else:
                # Valuta Check
                currency = safe_get(info, 'currency', 'EUR')
                eur_rate = get_currency_rate(currency, "EUR")
                current_price_orig = hist['Close'].iloc[-1]
                current_price_eur = current_price_orig * eur_rate

                # --- HEADER ---
                st.markdown("---")
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    st.title(safe_get(info, 'shortName', selected_ticker))
                    st.caption(f"Ticker: {selected_ticker} | Beurs: {safe_get(info, 'exchange', '-')}")
                with c2:
                    st.metric("Huidige Koers (EUR)", f"€ {current_price_eur:.2f}")
                with c3:
                    # KNOP: TOEVOEGEN AAN PORTFOLIO (Hier direct in beeld!)
                    with st.popover("➕ Voeg toe aan Portfolio"):
                        with st.form("add_form"):
                            st.write(f"**{selected_ticker} toevoegen**")
                            amount = st.number_input("Aantal stuks", min_value=0.0, value=1.0)
                            price_inp = st.number_input("Jouw GAK (in Euro's!)", min_value=0.0, value=float(round(current_price_eur, 2)))
                            if st.form_submit_button("Bevestig"):
                                st.session_state['portfolio'].append({
                                    "ticker": selected_ticker,
                                    "amount": amount,
                                    "avg_price_eur": price_inp
                                })
                                st.toast(f"{selected_ticker} toegevoegd!", icon="✅")

                # --- METRICS BEREKENEN ---
                
                # 1. PEG Ratio (met fallback)
                pe = safe_get(info, 'trailingPE')
                fwd_pe = safe_get(info, 'forwardPE')
                growth = safe_get(info, 'earningsGrowth') 
                peg = safe_get(info, 'pegRatio')
                
                if peg is None and pe and growth:
                    # PEG = PE / (Growth Rate * 100)
                    peg = pe / (growth * 100)
                
                # 2. Dividend (Robuustere Check)
                div_yield = safe_get(info, 'dividendYield')
                if div_yield is None:
                    # Fallback: Probeer dividendRate / huidige prijs
                    div_rate = safe_get(info, 'dividendRate')
                    if div_rate and current_price_orig > 0:
                        div_yield = div_rate / current_price_orig
                    else:
                        div_yield = 0.0
                
                # 3. Overige Metrics
                pb = safe_get(info, 'priceToBook')
                de = safe_get(info, 'debtToEquity')
                de_ratio = de / 100 if de else None
                beta = safe_get(info, 'beta')

                # 4. RSI (Handmatig)
                delta = hist['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50
                
                # 5. Koersdoel
                target_price = safe_get(info, 'targetMeanPrice')
                target_eur = target_price * eur_rate if target_price else None

                # --- METRICS DASHBOARD (2 RIJEN) ---
                st.subheader("📊 Fundamentele Cijfers")
                
                # RIJ 1
                m1, m2, m3, m4, m5 = st.columns(5)
                
                with m1:
                    val = peg if peg else 0
                    color = "🟢" if 0 < val < 1 else ("🟠" if val < 0 else ("🟡" if val < 2 else "🔴"))
                    st.metric("PEG Ratio", f"{color} {val:.2f}" if peg else "N/A", help="Negatief = Krimpende winst. 0-1 = Ondergewaardeerd.")
                
                with m2:
                    val = fwd_pe if fwd_pe else 0
                    color = "🟢" if 0 < val < 15 else "🟡"
                    st.metric("Forward P/E", f"{color} {val:.1f}" if fwd_pe else "N/A", help="Verwachte K/W voor komend jaar.")

                with m3:
                    val = roe = safe_get(info, 'returnOnEquity', 0)
                    color = "🟢" if roe > 0.15 else ("🟡" if roe > 0.10 else "🔴")
                    st.metric("ROE", f"{color} {roe:.1%}", help="Return on Equity. >15% is sterk.")
                
                with m4:
                    val = de_ratio if de_ratio else 0
                    color = "🟢" if val < 1.0 else "🟠"
                    st.metric("Debt/Equity", f"{color} {val:.2f}" if de_ratio else "N/A", help="< 1.0 is gezond.")
                
                with m5:
                    val = rsi_val
                    color = "🟢" if val < 30 else ("🔴" if val > 70 else "⚪")
                    st.metric("RSI (Timing)", f"{color} {val:.0f}", help="<30 Oversold (Koopkans?), >70 Overbought.")
                
                # RIJ 2
                st.write("") # Witregel
                m6, m7, m8, m9, m10 = st.columns(5)
                
                with m6:
                    # DIVIDEND
                    st.metric("Dividend", f"{div_yield:.2%}", help="Jaarlijks dividend rendement.")
                    
                with m7:
                    # BETA
                    st.metric("Beta", f"{beta:.2f}" if beta else "1.00", help="Volatiliteit t.o.v. markt (1.0)")
                    
                with m8:
                    # P/B RATIO
                    color = "🟢" if pb and pb < 1.5 else "⚪"
                    st.metric("P/B Ratio", f"{color} {pb:.2f}" if pb else "N/A", help="Prijs / Boekwaarde")
                    
                with m9:
                    # KOERSDOEL
                    st.metric("Analisten Doel", f"€ {target_eur:.2f}" if target_eur else "N/A")
                    
                with m10:
                    # RUIMTE OPVULLING of EVENTUEEL SECTOR
                    st.caption(f"Sector:\n{safe_get(info, 'sector', '-')}")

                # --- GRAFIEK ---
                st.markdown("### 📈 Koersverloop")
                fig = go.Figure()
                color_line = '#00C805' if hist['Close'].iloc[-1] >= hist['Close'].iloc[0] else '#FF5000'
                fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], mode='lines', name=selected_ticker, line=dict(color=color_line)))
                fig.update_layout(template="plotly_dark", margin=dict(l=0,r=0,t=10,b=0), height=400, yaxis_title=currency)
                st.plotly_chart(fig, use_container_width=True)

                # --- NIEUWS (Fallback Methode) ---
                st.markdown("### 📰 Laatste Nieuws")
                news_items = getattr(stock, 'news', [])
                
                if news_items:
                    for n in news_items[:3]:
                        title = n.get('title', 'Geen titel')
                        link = n.get('link')
                        pub = n.get('publisher', 'Onbekend')
                        if link:
                            st.markdown(f"**[{title}]({link})**")
                            st.caption(f"Bron: {pub}")
                            st.markdown("---")
                else:
                    st.info("Geen direct nieuws geladen via de API.")
                    search_url = f"https://finance.yahoo.com/quote/{selected_ticker}/news"
                    st.markdown(f"[👉 Klik hier voor het laatste nieuws op Yahoo Finance]({search_url})")

        except Exception as e:
            st.error(f"Er ging iets mis bij het ophalen van data: {e}")


# ==================================================
# TAB 2: PORTFOLIO
# ==================================================
with tabs[1]:
    st.title("Mijn Aandelen")
    
    if not st.session_state['portfolio']:
        st.info("Je portfolio is leeg. Ga naar het tabblad 'Analyse & Toevoegen' om aandelen te zoeken en toe te voegen!")
    else:
        portfolio_rows = []
        total_invested = 0
        total_value = 0
        sector_dist = {}

        # Laadbaalkje tonen
        bar = st.progress(0)
        total_items = len(st.session_state['portfolio'])

        for i, item in enumerate(st.session_state['portfolio']):
            ticker = item['ticker']
            amt = item['amount']
            gak = item['avg_price_eur']
            
            try:
                # Live data
                s = yf.Ticker(ticker)
                # Snelste manier: pak 'fast_info' of laatste history
                price_data = s.history(period="1d")
                
                if not price_data.empty:
                    current_raw = price_data['Close'].iloc[-1]
                    inf = s.info
                    
                    # Valuta conversie
                    cur_code = safe_get(inf, 'currency', 'EUR')
                    rate = get_currency_rate(cur_code, "EUR")
                    current_eur = current_raw * rate
                    
                    # Berekeningen
                    val_eur = current_eur * amt
                    cost_eur = gak * amt
                    profit_eur = val_eur - cost_eur
                    profit_pct = (profit_eur / cost_eur) * 100 if cost_eur > 0 else 0
                    
                    sec = safe_get(inf, 'sector', 'Onbekend')
                    sector_dist[sec] = sector_dist.get(sec, 0) + val_eur
                    
                    portfolio_rows.append({
                        "Ticker": ticker,
                        "Bedrijf": safe_get(inf, 'shortName', ticker),
                        "Aantal": amt,
                        "GAK (€)": gak,
                        "Koers (€)": current_eur,
                        "Waarde (€)": val_eur,
                        "Winst (€)": profit_eur,
                        "Rendement (%)": profit_pct
                    })
                    
                    total_value += val_eur
                    total_invested += cost_eur
            
            except:
                st.warning(f"Kon data voor {ticker} niet updaten.")
            
            bar.progress((i + 1) / total_items)
        
        bar.empty()

        # --- TOTALEN ---
        tot_res_eur = total_value - total_invested
        tot_res_pct = (tot_res_eur / total_invested) * 100 if total_invested > 0 else 0
        
        k1, k2, k3 = st.columns(3)
        k1.metric("Totale Waarde", f"€ {total_value:,.2f}")
        k2.metric("Totaal Resultaat", f"€ {tot_res_eur:,.2f}", delta=f"{tot_res_pct:.1f}%")
        k3.metric("Geïnvesteerd", f"€ {total_invested:,.2f}")
        
        st.divider()
        
        # --- TABEL & GRAFIEK ---
        c1, c2 = st.columns([1, 2])
        
        with c1:
            if sector_dist:
                st.subheader("Sectoren")
                fig = px.pie(values=list(sector_dist.values()), names=list(sector_dist.keys()), hole=0.4)
                fig.update_layout(margin=dict(t=0,b=0,l=0,r=0), height=250)
                st.plotly_chart(fig, use_container_width=True)
        
        with c2:
            st.subheader("Details")
            if portfolio_rows:
                df = pd.DataFrame(portfolio_rows)
                st.dataframe(
                    df, 
                    use_container_width=True, 
                    hide_index=True,
                    column_config={
                        "Koers (€)": st.column_config.NumberColumn(format="€ %.2f"),
                        "GAK (€)": st.column_config.NumberColumn(format="€ %.2f"),
                        "Waarde (€)": st.column_config.NumberColumn(format="€ %.2f"),
                        "Winst (€)": st.column_config.NumberColumn(format="€ %.2f"),
                        "Rendement (%)": st.column_config.NumberColumn(format="%.1f%%")
                    }
                )
                
                if st.button("🗑️ Portfolio Wissen"):
                    st.session_state['portfolio'] = []
                    st.rerun()
