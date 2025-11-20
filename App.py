import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- HULPFUNCTIES ---

@st.cache_data(ttl=3600)
def get_currency_rate(from_currency, to_currency="EUR"):
    if from_currency == to_currency: return 1.0
    if not from_currency or from_currency == 'nan': return 1.0
    try:
        ticker_map = {"USD": "EUR=X", "GBP": "EURGBP=X", "CNY": "CNYEUR=X", "HKD": "HKDEUR=X"}
        ticker = ticker_map.get(from_currency, f"{from_currency}{to_currency}=X")
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty: return data['Close'].iloc[-1]
        return 1.0
    except: return 1.0

def safe_get(info_dict, key, default=None):
    if not info_dict: return default
    val = info_dict.get(key)
    return val if val is not None else default

def search_yahoo(query):
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
                results.append(f"{symbol} | {name} ({exch})")
        return results
    except: return []

# --- NIEUWE FUNCTIE: GOOGLE NEWS RSS ---
def get_google_news(query):
    """Haalt nieuws op via Google RSS feed (betrouwbaarder dan Yahoo)"""
    try:
        # Zoekquery optimaliseren voor financieel nieuws
        search_q = f"{query} stock finance news"
        url = f"https://news.google.com/rss/search?q={search_q}&hl=en-US&gl=US&ceid=US:en"
        
        r = requests.get(url, timeout=5)
        root = ET.fromstring(r.content)
        
        news_items = []
        for item in root.findall('./channel/item')[:5]: # Max 5 items
            title = item.find('title').text
            link = item.find('link').text
            pubDate = item.find('pubDate').text
            # Datum opschonen (Fri, 21 Nov 2025... -> 21 Nov)
            try:
                dt = datetime.strptime(pubDate[:16], '%a, %d %b %Y')
                date_str = dt.strftime('%d %b %Y')
            except:
                date_str = ""
            
            news_items.append({"title": title, "link": link, "date": date_str})
        return news_items
    except:
        return []

# --- PORTFOLIO MANAGER ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []

def save_portfolio_json(): return json.dumps(st.session_state['portfolio'])
def load_portfolio_json(uploaded_file):
    if uploaded_file:
        try:
            st.session_state['portfolio'] = json.load(uploaded_file)
            st.success("✅ Portfolio geladen!")
        except: st.error("❌ Kon bestand niet lezen.")

# --- ZIJBALK ---
st.sidebar.header("💾 Data Opslag")
json_data = save_portfolio_json()
st.sidebar.download_button("⬇️ Download Portfolio", json_data, "portfolio.json", "application/json")
uploaded_file = st.sidebar.file_uploader("⬆️ Upload Portfolio", type=['json'])
if uploaded_file: load_portfolio_json(uploaded_file)
st.sidebar.markdown("---")
st.sidebar.info("Tip: Zoek aandelen in het hoofdscherm.")

# --- TABBLADEN ---
tabs = st.tabs(["🔎 Analyse & Nieuws", "📊 Mijn Portfolio"])

# ==================================================
# TAB 1: ANALYSE
# ==================================================
with tabs[0]:
    col_search, col_period = st.columns([3, 1])
    
    with col_search:
        search_text = st.text_input("🔍 Zoek Aandeel:", placeholder="Bijv. JD.com, Shell...")
        selected_ticker = None
        if search_text:
            results = search_yahoo(search_text)
            if results:
                choice = st.selectbox("Kies bedrijf:", results)
                selected_ticker = choice.split(" | ")[0]
            else: st.warning("Geen resultaten.")
    
    with col_period:
        period = st.selectbox("Periode:", ["1mo", "6mo", "1y", "3y", "5y"], index=2)

    if selected_ticker:
        try:
            stock = yf.Ticker(selected_ticker)
            info = stock.info
            hist = stock.history(period=period)
            
            if hist.empty:
                st.error("Geen koersdata beschikbaar.")
            else:
                # Valuta
                curr = safe_get(info, 'currency', 'EUR')
                eur_rate = get_currency_rate(curr, "EUR")
                price_eur = hist['Close'].iloc[-1] * eur_rate
                
                # HEADER
                st.markdown("---")
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    st.title(safe_get(info, 'shortName', selected_ticker))
                    st.caption(f"{selected_ticker} | {safe_get(info, 'exchange', '-')}")
                with c2:
                    st.metric("Koers (EUR)", f"€ {price_eur:.2f}")
                with c3:
                    with st.popover("➕ Aan Portfolio Toevoegen"):
                        with st.form("add"):
                            amt = st.number_input("Aantal", min_value=0.0, value=1.0)
                            price = st.number_input("Jouw GAK (€)", value=float(round(price_eur, 2)))
                            if st.form_submit_button("Opslaan"):
                                st.session_state['portfolio'].append({"ticker": selected_ticker, "amount": amt, "avg_price_eur": price})
                                st.toast("Opgeslagen!", icon="✅")

                # METRICS
                pe = safe_get(info, 'trailingPE')
                fwd_pe = safe_get(info, 'forwardPE')
                peg = safe_get(info, 'pegRatio')
                
                # Dividend Fix: 346% -> 3.46% logic
                div_yield = safe_get(info, 'dividendYield')
                if div_yield is None:
                    div_rate = safe_get(info, 'dividendRate')
                    orig_price = hist['Close'].iloc[-1]
                    if div_rate and orig_price > 0: div_yield = div_rate / orig_price
                    else: div_yield = 0.0
                
                # Sanity Check: Als dividend > 0.5 (50%), is het waarschijnlijk foutief geschaald (bv 3.46 ipv 0.0346)
                if div_yield > 0.5:
                    div_yield = div_yield / 100

                pb = safe_get(info, 'priceToBook')
                de = safe_get(info, 'debtToEquity')
                de_ratio = de / 100 if de else None
                
                # RSI
                delta = hist['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50
                
                # VISUALS
                st.subheader("Fundamentele Analyse")
                m1, m2, m3, m4, m5 = st.columns(5)
                with m1:
                    val = peg if peg else 0
                    color = "🟢" if 0 < val < 1 else ("🟠" if val < 0 else "🟡")
                    st.metric("PEG Ratio", f"{color} {val:.2f}" if peg else "N/A")
                with m2:
                    val = fwd_pe if fwd_pe else 0
                    color = "🟢" if 0 < val < 15 else "🟡"
                    st.metric("Forward P/E", f"{color} {val:.1f}" if fwd_pe else "N/A")
                with m3:
                    roe = safe_get(info, 'returnOnEquity', 0)
                    st.metric("ROE", f"{'🟢' if roe>0.15 else '🟡'} {roe:.1%}")
                with m4:
                    val = de_ratio if de_ratio else 0
                    st.metric("Debt/Equity", f"{'🟢' if val<1 else '🟠'} {val:.2f}" if de_ratio else "N/A")
                with m5:
                    st.metric("Dividend", f"{div_yield:.2%}")

                # GRAFIEK
                st.markdown("### 📈 Koers")
                fig = go.Figure()
                col = '#00C805' if hist['Close'].iloc[-1] >= hist['Close'].iloc[0] else '#FF5000'
                fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], mode='lines', line=dict(color=col)))
                fig.update_layout(template="plotly_dark", margin=dict(t=10,b=0,l=0,r=0), height=350)
                st.plotly_chart(fig, use_container_width=True)

                # --- NIEUWS & VIDEO SECTIE (NAAST ELKAAR) ---
                col_news, col_video = st.columns([1, 1])
                
                with col_news:
                    st.markdown("### 📰 Google Nieuws")
                    news_items = get_google_news(f"{safe_get(info, 'shortName', selected_ticker)}")
                    
                    if news_items:
                        for item in news_items:
                            st.markdown(f"**[{item['title']}]({item['link']})**")
                            st.caption(f"📅 {item['date']}")
                            st.divider()
                    else:
                        st.info("Geen nieuws gevonden.")

                with col_video:
                    st.markdown("### 📺 YouTube Analyse")
                    st.info("Directe links naar analyses voor dit aandeel:")
                    
                    # Slimme zoeklinks genereren
                    comp_name = safe_get(info, 'shortName', selected_ticker)
                    yt_query = f"{comp_name} stock analysis {datetime.now().year}"
                    yt_url = f"https://www.youtube.com/results?search_query={yt_query.replace(' ', '+')}"
                    
                    yt_query_nl = f"{comp_name} aandeel analyse"
                    yt_url_nl = f"https://www.youtube.com/results?search_query={yt_query_nl.replace(' ', '+')}"
                    
                    st.markdown(f"🇺🇸 **[Zoek Engelse Analyse op YouTube]({yt_url})**")
                    st.markdown(f"🇳🇱 **[Zoek Nederlandse Analyse op YouTube]({yt_url_nl})**")
                    
                    st.markdown("---")
                    st.markdown("**Of zoek specifiek naar Dividend info:**")
                    div_url = f"https://www.youtube.com/results?search_query={comp_name}+dividend+history"
                    st.markdown(f"💰 **[Bekijk Dividend Video's]({div_url})**")

        except Exception as e: st.error(f"Error: {e}")

# ==================================================
# TAB 2: PORTFOLIO
# ==================================================
with tabs[1]:
    st.title("Mijn Aandelen")
    if not st.session_state['portfolio']:
        st.info("Nog leeg. Voeg aandelen toe in tabblad 1!")
    else:
        rows = []
        tot_inv = 0
        tot_val = 0
        bar = st.progress(0)
        
        for i, item in enumerate(st.session_state['portfolio']):
            try:
                t = item['ticker']
                s = yf.Ticker(t)
                h = s.history(period="1d")
                if not h.empty:
                    price = h['Close'].iloc[-1]
                    rate = get_currency_rate(safe_get(s.info, 'currency', 'EUR'), "EUR")
                    p_eur = price * rate
                    val = p_eur * item['amount']
                    cost = item['avg_price_eur'] * item['amount']
                    
                    rows.append({
                        "Ticker": t,
                        "Aantal": item['amount'],
                        "Koers": p_eur,
                        "Waarde": val,
                        "Winst": val - cost,
                        "Rendement": ((val-cost)/cost)*100 if cost>0 else 0
                    })
                    tot_val += val
                    tot_inv += cost
            except: pass
            bar.progress((i+1)/len(st.session_state['portfolio']))
        bar.empty()

        res = tot_val - tot_inv
        k1, k2, k3 = st.columns(3)
        k1.metric("Waarde", f"€ {tot_val:,.2f}")
        k2.metric("Winst", f"€ {res:,.2f}", delta=f"{(res/tot_inv)*100:.1f}%" if tot_inv>0 else "0%")
        k3.metric("Inleg", f"€ {tot_inv:,.2f}")
        
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, column_config={
            "Koers": st.column_config.NumberColumn(format="€ %.2f"),
            "Waarde": st.column_config.NumberColumn(format="€ %.2f"),
            "Winst": st.column_config.NumberColumn(format="€ %.2f"),
            "Rendement": st.column_config.NumberColumn(format="%.1f%%")
        })
        if st.button("Alles Wissen"):
            st.session_state['portfolio'] = []
            st.rerun()
