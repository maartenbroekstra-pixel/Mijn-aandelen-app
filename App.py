import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import requests
import json
import re
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

# --- NIEUW: YOUTUBE SCRAPER & PLAYER ---
@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    """Probeert een video ID te vinden zonder API key"""
    try:
        # Zoek naar 'stock analysis' video's
        search_query = f"{query} stock analysis {datetime.now().year}"
        url = f"https://www.youtube.com/results?search_query={search_query.replace(' ', '+')}"
        
        # We moeten een user-agent meegeven anders blokkeert YT
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=5)
        
        # Regex om video ID's te vinden (watch?v=XXXXXXXXXXX)
        video_ids = re.findall(r"watch\?v=(\S{11})", response.text)
        
        if video_ids:
            return video_ids[0] # Retourneer de eerste hit
        return None
    except:
        return None

# --- FINVIZ SCRAPER (Verbeterd) ---
@st.cache_data(ttl=3600)
def get_finviz_data(ticker):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        r = requests.get(url, headers=headers, timeout=5)
        data = {}
        if r.status_code == 200:
            # Robuustere parsing
            if 'PEG' in r.text:
                try: data['peg'] = float(re.search(r'>PEG<.*?<b>([\d\.]+)</b>', r.text, re.DOTALL).group(1))
                except: pass
            if 'P/B' in r.text:
                try: data['pb'] = float(re.search(r'>P/B<.*?<b>([\d\.]+)</b>', r.text, re.DOTALL).group(1))
                except: pass
            if 'Target Price' in r.text:
                try: data['target'] = float(re.search(r'>Target Price<.*?<b>([\d\.]+)</b>', r.text, re.DOTALL).group(1))
                except: pass
        return data
    except: return {}

def get_google_news(query):
    try:
        search_q = f"{query} stock finance news"
        url = f"https://news.google.com/rss/search?q={search_q}&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, timeout=5)
        root = ET.fromstring(r.content)
        news_items = []
        for item in root.findall('./channel/item')[:5]:
            title = item.find('title').text
            link = item.find('link').text
            pubDate = item.find('pubDate').text
            try:
                dt = datetime.strptime(pubDate[:16], '%a, %d %b %Y')
                date_str = dt.strftime('%d %b %Y')
            except: date_str = ""
            news_items.append({"title": title, "link": link, "date": date_str})
        return news_items
    except: return []

# --- NIEUWE SCORE BEREKENING (Met Neutraal) ---
def calculate_score(metrics):
    score = 0
    total_checks = 0
    opportunities = []
    risks = []
    neutral = []

    # 1. PEG
    peg = metrics.get('peg')
    if peg is not None:
        total_checks += 1
        if 0 < peg < 1.5:
            score += 1
            opportunities.append(f"PEG ({peg:.2f}) wijst op onderwaardering t.o.v. groei.")
        elif peg > 2.5 or peg < 0:
            risks.append(f"PEG ({peg:.2f}) is ongunstig (te duur of krimp).")
        else:
            score += 0.5
            neutral.append(f"PEG ({peg:.2f}) is marktconform.")

    # 2. Forward P/E
    fwd_pe = metrics.get('fwd_pe')
    if fwd_pe is not None:
        total_checks += 1
        if 0 < fwd_pe < 20:
            score += 1
            opportunities.append(f"Forward P/E ({fwd_pe:.1f}x) is aantrekkelijk.")
        elif fwd_pe > 40:
            risks.append(f"Forward P/E ({fwd_pe:.1f}x) is erg hoog.")
        else:
            score += 0.5
            neutral.append(f"Forward P/E ({fwd_pe:.1f}x) is redelijk.")

    # 3. ROE
    roe = metrics.get('roe')
    if roe is not None:
        total_checks += 1
        if roe > 0.15:
            score += 1
            opportunities.append(f"Sterke ROE ({roe:.1%}).")
        elif roe < 0.05:
            risks.append(f"Zwakke ROE ({roe:.1%}).")
        else:
            score += 0.5
            neutral.append(f"ROE ({roe:.1%}) is gemiddeld.")

    # 4. Debt/Equity
    de = metrics.get('de')
    if de is not None:
        total_checks += 1
        if de < 1.0:
            score += 1
            opportunities.append("Lage schuldenlast.")
        elif de > 2.0:
            risks.append(f"Hoge schulden (D/E: {de:.2f}).")
        else:
            score += 0.5
            neutral.append(f"Schuldenlast (D/E: {de:.2f}) is acceptabel.")

    # 5. Current Ratio
    cr = metrics.get('cr')
    if cr is not None:
        total_checks += 1
        if cr > 1.5:
            score += 1
            opportunities.append("Goede liquiditeit.")
        elif cr < 1.0:
            risks.append("Krap bij kas (Current Ratio < 1).")
        else:
            score += 0.5
            neutral.append("Liquiditeit is voldoende.")

    # 6. P/B
    pb = metrics.get('pb')
    if pb is not None:
        total_checks += 1
        if 0 < pb < 3.0:
            score += 1
            opportunities.append("Redelijke prijs t.o.v. boekwaarde.")
        elif pb > 10:
            risks.append(f"Hoge P/B ({pb:.2f}).")
        else:
            score += 0.5
            neutral.append(f"P/B ({pb:.2f}) is normaal voor de sector.")

    final_score = (score / total_checks) * 10 if total_checks > 0 else 5.0
    return final_score, opportunities, risks, neutral

# --- PORTFOLIO ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
def save_portfolio_json(): return json.dumps(st.session_state['portfolio'])
def load_portfolio_json(uploaded_file):
    if uploaded_file:
        try:
            st.session_state['portfolio'] = json.load(uploaded_file)
            st.success("✅ Portfolio geladen!")
        except: st.error("❌ Fout bestand.")

# --- ZIJBALK ---
st.sidebar.header("💾 Data Opslag")
json_data = save_portfolio_json()
st.sidebar.download_button("⬇️ Download", json_data, "portfolio.json", "application/json")
uploaded_file = st.sidebar.file_uploader("⬆️ Upload", type=['json'])
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
        search_text = st.text_input("🔍 Zoek Aandeel:", placeholder="Bijv. Salesforce, Shell...")
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
                st.error("Geen data.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                eur_rate = get_currency_rate(curr, "EUR")
                price_eur = hist['Close'].iloc[-1] * eur_rate
                
                finviz_data = get_finviz_data(selected_ticker)

                st.markdown("---")
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    st.title(safe_get(info, 'shortName', selected_ticker))
                    st.caption(f"{selected_ticker} | {safe_get(info, 'exchange', '-')}")
                with c2:
                    st.metric("Koers (EUR)", f"€ {price_eur:.2f}")
                with c3:
                    with st.popover("➕ Toevoegen"):
                        with st.form("add"):
                            amt = st.number_input("Aantal", min_value=0.0, value=1.0)
                            price = st.number_input("GAK (€)", value=float(round(price_eur, 2)))
                            if st.form_submit_button("Opslaan"):
                                st.session_state['portfolio'].append({"ticker": selected_ticker, "amount": amt, "avg_price_eur": price})
                                st.toast("Opgeslagen!", icon="✅")

                # METRICS
                pe = safe_get(info, 'trailingPE')
                fwd_pe = safe_get(info, 'forwardPE')
                
                # PEG LOGICA (Yahoo -> Finviz -> Schatting)
                peg = safe_get(info, 'pegRatio')
                peg_source = "Yahoo"
                if peg is None and 'peg' in finviz_data:
                    peg = finviz_data['peg']
                    peg_source = "Finviz"
                if peg is None and pe:
                    growth = safe_get(info, 'earningsGrowth') 
                    if growth:
                        peg = pe / (growth * 100)
                        peg_source = "Schatting (PE/Growth)"
                
                div_yield = safe_get(info, 'dividendYield')
                if div_yield is None:
                    div_rate = safe_get(info, 'dividendRate')
                    orig_price = hist['Close'].iloc[-1]
                    if div_rate and orig_price > 0: div_yield = div_rate / orig_price
                    else: div_yield = 0.0
                if div_yield > 0.5: div_yield = div_yield / 100

                pb = safe_get(info, 'priceToBook')
                if pb is None and 'pb' in finviz_data: pb = finviz_data['pb']
                
                de = safe_get(info, 'debtToEquity')
                de_ratio = de / 100 if de else None
                cr = safe_get(info, 'currentRatio')
                
                tgt = safe_get(info, 'targetMeanPrice')
                if tgt is None and 'target' in finviz_data: tgt = finviz_data['target']
                
                delta = hist['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50
                
                # SCORE & ADVIES
                metrics_dict = {'peg': peg, 'roe': safe_get(info, 'returnOnEquity'), 'de': de_ratio, 'cr': cr, 'fwd_pe': fwd_pe, 'pb': pb}
                score, opps, risks, neutral = calculate_score(metrics_dict)
                
                st.markdown("### 🤖 AI Oordeel")
                sc_c1, sc_c2 = st.columns([1, 3])
                with sc_c1:
                    color = "green" if score >= 7 else ("orange" if score >= 5.5 else "red")
                    # CSS Flexbox fix voor perfecte centrering
                    st.markdown(f"""
                    <div style="display: flex; justify-content: center; align-items: center; height: 100%;">
                        <div style="display: flex; justify-content: center; align-items: center; border: 5px solid {color}; width: 100px; height: 100px; border-radius: 50%;">
                            <h1 style="margin:0; padding:0; color:{color}; font-size: 2.5rem;">{score:.1f}</h1>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                
                with sc_c2:
                    c_k, c_r, c_n = st.columns(3)
                    with c_k:
                        if opps:
                            st.success("**✅ Kansen**")
                            for o in opps: st.write(f"- {o}")
                        else: st.write("Geen sterke uitschieters.")
                    with c_r:
                        if risks:
                            st.error("**⚠️ Risico's**")
                            for r in risks: st.write(f"- {r}")
                        else: st.write("Geen zware risico's.")
                    with c_n:
                        if neutral:
                            st.info("**⚪ Neutraal**")
                            for n in neutral: st.write(f"- {n}")

                st.divider()
                st.subheader("📊 Ratio's")
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                with m1:
                    val = peg if peg else 0
                    color = "🟢" if 0 < val < 1.5 else ("🟠" if val < 0 else "🟡")
                    st.metric("PEG Ratio", f"{color} {val:.2f}" if peg else "N/A", help=f"Bron: {peg_source}")
                with m2: st.metric("Forward P/E", f"{'🟢' if fwd_pe and fwd_pe<20 else '🟡'} {fwd_pe:.1f}" if fwd_pe else "N/A")
                with m3: st.metric("Current Ratio", f"{'🟢' if cr and cr>1.2 else '🟠'} {cr:.2f}" if cr else "N/A")
                with m4: st.metric("Debt/Equity", f"{'🟢' if de_ratio and de_ratio<1.2 else '🟠'} {de_ratio:.2f}" if de_ratio else "N/A")
                with m5: st.metric("P/B Ratio", f"{'🟢' if pb and pb<3 else '⚪'} {pb:.2f}" if pb else "N/A")
                with m6: 
                    roe = safe_get(info, 'returnOnEquity', 0)
                    st.metric("ROE", f"{'🟢' if roe>0.15 else '🟡'} {roe:.1%}")

                st.write("")
                x1, x2, x3, x4 = st.columns(4)
                with x1: st.metric("Dividend", f"{div_yield:.2%}")
                with x2: st.metric("RSI (14)", f"{'🟢' if rsi_val<30 else '⚪'} {rsi_val:.0f}")
                with x3: st.metric("Beta", f"{safe_get(info, 'beta', 1.0):.2f}")
                with x4: st.metric("Koersdoel", f"€ {tgt*eur_rate:.2f}" if tgt else "N/A")

                st.markdown("### 📈 Koers")
                fig = go.Figure()
                col = '#00C805' if hist['Close'].iloc[-1] >= hist['Close'].iloc[0] else '#FF5000'
                fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], mode='lines', line=dict(color=col)))
                fig.update_layout(template="plotly_dark", margin=dict(t=10,b=0,l=0,r=0), height=350)
                st.plotly_chart(fig, use_container_width=True)

                c_news, c_vid = st.columns(2)
                with c_news:
                    st.markdown("### 📰 Nieuws")
                    news = get_google_news(safe_get(info, 'shortName', selected_ticker))
                    if news:
                        for n in news:
                            st.markdown(f"**[{n['title']}]({n['link']})**")
                            st.caption(f"{n['date']}")
                            st.divider()
                    else: st.info("Geen nieuws.")
                
                with c_vid:
                    st.markdown("### 📺 Video")
                    comp = safe_get(info, 'shortName', selected_ticker)
                    video_id = get_youtube_video_id(comp)
                    
                    if video_id:
                        st.success(f"Video gevonden: {comp} Analysis")
                        st.video(f"https://www.youtube.com/watch?v={video_id}")
                    else:
                        st.warning("Geen directe video gevonden.")
                        yt_url = f"https://www.youtube.com/results?search_query={comp}+stock+analysis"
                        st.markdown(f"[👉 Zoek handmatig op YouTube]({yt_url})")

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
