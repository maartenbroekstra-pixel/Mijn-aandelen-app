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

# --- STATE INITIALISATIE ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []
if 'search_query' not in st.session_state: st.session_state['search_query'] = ""

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

def truncate_text(text, max_len=25):
    if text and len(text) > max_len: return text[:max_len] + "..."
    return text

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

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    try:
        search_query = f"{query} stock analysis {datetime.now().year}"
        url = f"https://www.youtube.com/results?search_query={search_query.replace(' ', '+')}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=5)
        video_ids = re.findall(r"watch\?v=(\S{11})", response.text)
        if video_ids: return video_ids[0]
        return None
    except: return None

@st.cache_data(ttl=3600)
def get_finviz_data(ticker):
    """Haalt data van Finviz (Back-up voor Yahoo)"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        r = requests.get(url, headers=headers, timeout=5)
        data = {}
        if r.status_code == 200:
            # Flexibele regex voor als er spaties zijn
            if 'PEG' in r.text:
                try: data['peg'] = float(re.search(r'>PEG<.*?<b>\s*([\d\.]+)\s*</b>', r.text, re.DOTALL).group(1))
                except: pass
            if 'P/B' in r.text:
                try: data['pb'] = float(re.search(r'>P/B<.*?<b>\s*([\d\.]+)\s*</b>', r.text, re.DOTALL).group(1))
                except: pass
            if 'Target Price' in r.text:
                try: data['target'] = float(re.search(r'>Target Price<.*?<b>\s*([\d\.]+)\s*</b>', r.text, re.DOTALL).group(1))
                except: pass
            if 'Forward P/E' in r.text:
                try: data['fwd_pe'] = float(re.search(r'>Forward P/E<.*?<b>\s*([\d\.]+)\s*</b>', r.text, re.DOTALL).group(1))
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
            try:
                pubDate = item.find('pubDate').text
                dt = datetime.strptime(pubDate[:16], '%a, %d %b %Y')
                date_str = dt.strftime('%d %b %Y')
            except: date_str = ""
            news_items.append({"title": title, "link": link, "date": date_str})
        return news_items
    except: return []

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
            opportunities.append(f"Waardering: Aantrekkelijke PEG ratio ({peg:.2f}).")
        elif peg < 0:
            risks.append("Groei: Winst krimpt (Negatieve PEG).")
        elif peg > 2.5:
            risks.append(f"Waardering: Koers loopt voor op groei (PEG: {peg:.2f}).")
        else:
            score += 0.5
            neutral.append(f"Waardering: PEG ({peg:.2f}) is marktconform.")

    # 2. Forward P/E
    fwd_pe = metrics.get('fwd_pe')
    if fwd_pe is not None:
        total_checks += 1
        if 0 < fwd_pe < 20:
            score += 1
            opportunities.append(f"Verwachting: Lage verwachte K/W ({fwd_pe:.1f}x).")
        elif fwd_pe > 40:
            risks.append(f"Verwachting: Hoge verwachte K/W ({fwd_pe:.1f}x).")
        else:
            score += 0.5
            neutral.append(f"Verwachting: Forward P/E ({fwd_pe:.1f}x) is redelijk.")

    # 3. ROE
    roe = metrics.get('roe')
    if roe is not None:
        total_checks += 1
        if roe > 0.15:
            score += 1
            opportunities.append(f"Kwaliteit: Sterke winstgevendheid (ROE: {roe:.1%}).")
        elif roe < 0.05:
            risks.append(f"Kwaliteit: Zwakke winstgevendheid (ROE: {roe:.1%}).")
        else:
            score += 0.5
            neutral.append(f"Kwaliteit: ROE ({roe:.1%}) is gemiddeld.")

    # 4. Debt/Equity
    de = metrics.get('de')
    if de is not None:
        total_checks += 1
        if de < 1.0:
            score += 1
            opportunities.append("Balans: Gezond schuldenniveau.")
        elif de > 2.0:
            risks.append(f"Risico: Hoge schuldenlast (D/E: {de:.2f}).")
        else:
            score += 0.5
            neutral.append("Balans: Schulden zijn acceptabel.")

    # 5. Current Ratio
    cr = metrics.get('cr')
    if cr is not None:
        total_checks += 1
        if cr > 1.5:
            score += 1
            opportunities.append("Liquiditeit: Voldoende cash buffer.")
        elif cr < 1.0:
            risks.append("Liquiditeit: Krap bij kas (CR < 1).")
        else:
            score += 0.5
            neutral.append("Liquiditeit: Voldoende.")

    # 6. P/B
    pb = metrics.get('pb')
    if pb is not None:
        total_checks += 1
        if 0 < pb < 3.0:
            score += 1
            opportunities.append("Substantie: Koers dichtbij boekwaarde.")
        elif pb > 10:
            risks.append(f"Waardering: Hoge premie boven boekwaarde (P/B: {pb:.2f}).")
        else:
            score += 0.5
            neutral.append(f"Substantie: P/B ({pb:.2f}) is normaal.")

    final_score = (score / total_checks) * 10 if total_checks > 0 else 5.0
    return final_score, opportunities, risks, neutral

# --- PORTFOLIO BEHEER ---
def save_data():
    data = {
        'portfolio': st.session_state['portfolio'],
        'wishlist': st.session_state['wishlist']
    }
    return json.dumps(data)

def load_data(uploaded_file):
    if uploaded_file:
        try:
            data = json.load(uploaded_file)
            if isinstance(data, list): # Oude structuur
                st.session_state['portfolio'] = data
                st.session_state['wishlist'] = []
            else: # Nieuwe structuur
                st.session_state['portfolio'] = data.get('portfolio', [])
                st.session_state['wishlist'] = data.get('wishlist', [])
            st.success("✅ Data geladen!")
        except: st.error("❌ Bestand niet leesbaar.")

# --- ZIJBALK ---
st.sidebar.header("💾 Opslag")
st.sidebar.download_button("⬇️ Download Data (Alles)", save_data(), "aandelen_data.json", "application/json")
uploaded = st.sidebar.file_uploader("⬆️ Upload Data", type=['json'])
if uploaded: load_data(uploaded)
st.sidebar.markdown("---")
st.sidebar.info("Tip: Gebruik de zoekbalk voor analyse.")

# --- TABBLADEN ---
tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist"])

# ==================================================
# TAB 1: ANALYSE
# ==================================================
with tabs[0]:
    col_search, col_period = st.columns([3, 1])
    with col_search:
        default_search = st.session_state.get('search_query', "")
        search_text = st.text_input("🔍 Zoek Aandeel:", value=default_search, placeholder="Bijv. PDD, Salesforce...", key="search_input")
        
        selected_ticker = None
        if search_text and "." in search_text and " " not in search_text: 
             selected_ticker = search_text
        elif search_text:
            results = search_yahoo(search_text)
            if results:
                choice = st.selectbox("Kies bedrijf:", results)
                selected_ticker = choice.split(" | ")[0]
            else: 
                selected_ticker = search_text.upper()
    
    with col_period:
        period = st.selectbox("Periode:", ["1mo", "6mo", "1y", "3y", "5y"], index=2)

    if selected_ticker:
        try:
            stock = yf.Ticker(selected_ticker)
            info = stock.info
            hist = stock.history(period=period)
            
            if hist.empty:
                st.error("Geen data gevonden. Check de ticker.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                eur_rate = get_currency_rate(curr, "EUR")
                price_eur = hist['Close'].iloc[-1] * eur_rate
                
                # Haal Finviz data op (Back-up)
                finviz_data = get_finviz_data(selected_ticker)

                # Koersdoel
                tgt = safe_get(info, 'targetMeanPrice')
                if tgt is None and 'target' in finviz_data: tgt = finviz_data['target']
                tgt_eur = tgt * eur_rate if tgt else None
                upside_txt = ""
                if tgt_eur:
                    upside = ((tgt_eur - price_eur) / price_eur) * 100
                    upside_txt = f"{'+' if upside > 0 else ''}{upside:.1f}%"

                # HEADER
                st.markdown("---")
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1:
                    full_name = safe_get(info, 'shortName', selected_ticker)
                    st.title(truncate_text(full_name, 25))
                    st.caption(f"{selected_ticker} | {safe_get(info, 'exchange', '-')}")
                with c2:
                    st.metric("Koers", f"€ {price_eur:.2f}")
                with c3:
                    st.metric("Koersdoel", f"€ {tgt_eur:.2f}" if tgt_eur else "N/A", delta=upside_txt if tgt_eur else None)
                with c4:
                    with st.popover("➕ Toevoegen"):
                        tab_p, tab_w = st.tabs(["Portfolio", "Wishlist"])
                        with tab_p:
                            with st.form("add_port"):
                                amt = st.number_input("Aantal", min_value=0.0, value=1.0)
                                price = st.number_input("GAK (€)", value=float(round(price_eur, 2)))
                                if st.form_submit_button("In Portfolio"):
                                    st.session_state['portfolio'].append({"ticker": selected_ticker, "amount": amt, "avg_price_eur": price})
                                    st.toast("Toegevoegd aan Portfolio!", icon="✅")
                        with tab_w:
                            if st.button("Zet op Wishlist"):
                                if selected_ticker not in [x['ticker'] for x in st.session_state['wishlist']]:
                                    st.session_state['wishlist'].append({"ticker": selected_ticker, "name": full_name})
                                    st.toast("Toegevoegd aan Wishlist!", icon="❤️")
                                else: st.toast("Staat al op wishlist", icon="⚠️")

                # METRICS LOGICA (Met sterke fallbacks voor PDD e.d.)
                pe = safe_get(info, 'trailingPE')
                fwd_pe = safe_get(info, 'forwardPE')
                if fwd_pe is None and 'fwd_pe' in finviz_data: fwd_pe = finviz_data['fwd_pe']
                
                # PEG
                peg = safe_get(info, 'pegRatio')
                peg_src = "Yahoo"
                if peg is None and 'peg' in finviz_data: 
                    peg = finviz_data['peg']
                    peg_src = "Finviz"
                if peg is None:
                    # Fallback: Gebruik Fwd PE als Trailing PE mist
                    base_pe = pe if pe else fwd_pe
                    growth = safe_get(info, 'earningsGrowth')
                    if base_pe and growth: 
                        peg = base_pe / (growth * 100)
                        peg_src = "Schatting"

                # Dividend
                div_yield = safe_get(info, 'dividendYield')
                if div_yield is None:
                    div_rate = safe_get(info, 'dividendRate')
                    if div_rate: div_yield = div_rate / hist['Close'].iloc[-1]
                if div_yield and div_yield > 0.5: div_yield = div_yield / 100
                
                # P/B
                pb = safe_get(info, 'priceToBook')
                if pb is None and 'pb' in finviz_data: pb = finviz_data['pb']
                
                # Debt/Equity
                de_ratio = safe_get(info, 'debtToEquity')
                if de_ratio: de_ratio = de_ratio / 100
                
                cr = safe_get(info, 'currentRatio')
                beta = safe_get(info, 'beta')
                margin = safe_get(info, 'profitMargins')

                # RSI
                delta = hist['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50
                
                # AI SCORE
                metrics_dict = {'peg': peg, 'roe': safe_get(info, 'returnOnEquity'), 'de': de_ratio, 'cr': cr, 'fwd_pe': fwd_pe, 'pb': pb}
                score, opps, risks, neutral = calculate_score(metrics_dict)
                
                st.markdown("### 🧬 Fundamentele Analyse")
                with st.container(border=True):
                    col_score, col_txt = st.columns([1, 4])
                    with col_score:
                        color = "green" if score >= 7 else ("orange" if score >= 5.5 else "red")
                        # FIX: GEEN LINK ICOON DOOR SPAN TE GEBRUIKEN
                        st.markdown(f"""
                        <div style="display: flex; justify-content: center; align-items: center; width: 100%; height: 100%;">
                            <div style="
                                display: flex; 
                                justify-content: center; 
                                align-items: center; 
                                border: 6px solid {color}; 
                                width: 110px; 
                                height: 110px; 
                                border-radius: 50%;
                                background-color: transparent;
                                box-sizing: border-box;">
                                <span style="margin: 0; padding: 0; color:{color}; font-size: 2.8rem; font-weight: bold; line-height: 1;">{score:.1f}</span>
                            </div>
                        </div>
                        <p style="text-align:center; margin-top:5px; font-weight:bold;">AI Score</p>
                        """, unsafe_allow_html=True)
                    
                    with col_txt:
                        if not opps and not risks and not neutral:
                            st.info("Onvoldoende data voor analyse.")
                        else:
                            c_plus, c_min, c_neut = st.columns(3)
                            with c_plus:
                                if opps:
                                    st.success("**✅ Kansen**")
                                    for o in opps: st.write(f"- {o}")
                                else: st.write("-")
                            with c_min:
                                if risks:
                                    st.error("**⚠️ Risico's**")
                                    for r in risks: st.write(f"- {r}")
                                else: st.write("-")
                            with c_neut:
                                if neutral:
                                    st.info("**⚪ Neutraal**")
                                    for n in neutral: st.write(f"- {n}")

                st.divider()
                st.subheader("📊 Ratio's")
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                with m1:
                    val = peg if peg else 0
                    c = "🟢" if 0 < val < 1.5 else ("🟠" if val < 0 else "🟡")
                    st.metric("PEG Ratio", f"{c} {val:.2f}" if peg else "N/A", help=f"Bron: {peg_src}\n\nPrijs t.o.v. Groei.\n< 1.5: Goed\nNegatief: Krimp")
                with m2:
                    val = fwd_pe if fwd_pe else 0
                    st.metric("Forward P/E", f"{'🟢' if 0<val<20 else '🟡'} {val:.1f}" if fwd_pe else "N/A", help="Verwachte K/W voor komend jaar.\n< 20: Aantrekkelijk")
                with m3:
                    val = cr if cr else 0
                    st.metric("Current Ratio", f"{'🟢' if val>1.5 else '🟠'} {val:.2f}" if cr else "N/A", help="Liquiditeit.\n> 1.5: Veilig")
                with m4:
                    val = de_ratio if de_ratio else 0
                    st.metric("Debt/Equity", f"{'🟢' if val<1 else '🟠'} {val:.2f}" if de_ratio else "N/A", help="Schuldgraad.\n< 1.0: Gezond")
                with m5:
                    val = pb if pb else 0
                    st.metric("P/B Ratio", f"{'🟢' if val<3 else '⚪'} {val:.2f}" if pb else "N/A", help="Prijs / Boekwaarde.\n< 3.0: Redelijk")
                with m6:
                    roe = safe_get(info, 'returnOnEquity', 0)
                    st.metric("ROE", f"{'🟢' if roe>0.15 else '🟡'} {roe:.1%}", help="Winstgevendheid.\n> 15%: Sterk")

                st.write("")
                x1, x2, x3, x4 = st.columns(4)
                with x1:
                    dy = div_yield if div_yield else 0
                    c = "🟢" if dy > 0.03 else ("🔵" if dy > 0.015 else "⚪")
                    st.metric("Dividend", f"{c} {dy:.2%}", help="Jaarlijks dividend.\n> 3%: Hoog\n< 1.5%: Laag")
                with x2:
                    st.metric("RSI (14)", f"{'🟢' if rsi_val<30 else '⚪'} {rsi_val:.0f}", help="Momentum.\n< 30: Koopkans?\n> 70: Duur?")
                with x3:
                    b = beta if beta else 1.0
                    c = "🟢" if b < 0.8 else ("🟠" if b > 1.5 else "⚪")
                    st.metric("Beta", f"{c} {b:.2f}", help="Volatiliteit.\n1.0: Markt\n< 1.0: Stabiel\n> 1.5: Wild")
                with x4:
                    m = margin if margin else 0
                    c = "🟢" if m > 0.20 else ("🔵" if m > 0.10 else "🟠")
                    st.metric("Winstmarge", f"{c} {m:.1%}", help="Netto winst als % van omzet.\n> 20%: Zeer winstgevend")

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
                        st.video(f"https://www.youtube.com/watch?v={video_id}")
                    
                    yt_url = f"https://www.youtube.com/results?search_query={comp}+stock+analysis+{datetime.now().year}"
                    st.markdown(f"👉 **[Zoek handmatig op YouTube]({yt_url})**")

        except Exception as e: st.error(f"Error: {e}")

# ==================================================
# TAB 2: PORTFOLIO
# ==================================================
with tabs[1]:
    st.title("Mijn Portfolio")
    if not st.session_state['portfolio']:
        st.info("Nog leeg.")
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

# ==================================================
# TAB 3: WISHLIST
# ==================================================
with tabs[2]:
    st.title("❤️ Mijn Wishlist")
    
    if not st.session_state['wishlist']:
        st.info("Je wishlist is leeg. Zoek een aandeel bij 'Analyse' en voeg het toe!")
    else:
        for item in st.session_state['wishlist']:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.subheader(f"{item['name']} ({item['ticker']})")
                with c2:
                    if st.button(f"🔍 Analyseer", key=f"btn_{item['ticker']}"):
                        st.session_state['search_query'] = item['ticker']
                        st.toast(f"Geselecteerd! Ga naar het tabblad 'Analyse'.", icon="🚀")
                        st.rerun()
        
        if st.button("Wishlist Legen"):
            st.session_state['wishlist'] = []
            st.rerun()
