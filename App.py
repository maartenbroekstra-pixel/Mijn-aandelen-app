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
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        r = requests.get(url, headers=headers, timeout=5)
        data = {}
        if r.status_code == 200:
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
            opportunities.append(f"Waardering: Het aandeel is goedkoop ten opzichte van de verwachte winstgroei (PEG: {peg:.2f}).")
        elif peg < 0:
            risks.append("Winstgroei: Analisten verwachten krimpende winsten (Negatieve PEG).")
        elif peg > 2.5:
            risks.append(f"Waardering: De koers loopt ver vooruit op de daadwerkelijke winstgroei (PEG: {peg:.2f}).")
        else:
            score += 0.5
            neutral.append(f"Waardering: De PEG ratio ({peg:.2f}) is marktconform.")

    # 2. Forward P/E
    fwd_pe = metrics.get('fwd_pe')
    if fwd_pe is not None:
        total_checks += 1
        if 0 < fwd_pe < 20:
            score += 1
            opportunities.append(f"Toekomst: De verwachte K/W ({fwd_pe:.1f}x) oogt aantrekkelijk.")
        elif fwd_pe > 40:
            risks.append(f"Verwachting: De markt heeft zeer hoge verwachtingen ingeprijsd (Fwd P/E: {fwd_pe:.1f}x).")
        else:
            score += 0.5
            neutral.append(f"Toekomst: De verwachte K/W ({fwd_pe:.1f}x) is redelijk.")

    # 3. ROE
    roe = metrics.get('roe')
    if roe is not None:
        total_checks += 1
        if roe > 0.15:
            score += 1
            opportunities.append(f"Kwaliteit: Het management behaalt een sterk rendement op eigen vermogen ({roe:.1%}).")
        elif roe < 0.05:
            risks.append(f"Kwaliteit: De winstgevendheid is mager (ROE: {roe:.1%}).")
        else:
            score += 0.5
            neutral.append(f"Kwaliteit: Het rendement ({roe:.1%}) is gemiddeld.")

    # 4. Debt/Equity
    de = metrics.get('de')
    if de is not None:
        total_checks += 1
        if de < 1.0:
            score += 1
            opportunities.append("Balans: Het bedrijf is gezond gefinancierd met weinig schulden.")
        elif de > 2.0:
            risks.append(f"Risico: Het bedrijf heeft een hoge schuldenlast (D/E: {de:.2f}).")
        else:
            score += 0.5
            neutral.append("Balans: De schuldenlast is acceptabel.")

    # 5. Current Ratio
    cr = metrics.get('cr')
    if cr is not None:
        total_checks += 1
        if cr > 1.5:
            score += 1
            opportunities.append("Liquiditeit: Er is voldoende cash buffer voor korte termijn verplichtingen.")
        elif cr < 1.0:
            risks.append("Liquiditeit: Het bedrijf zit krap bij kas om rekeningen op korte termijn te betalen.")
        else:
            score += 0.5
            neutral.append("Liquiditeit: De cashpositie is voldoende.")

    # 6. P/B
    pb = metrics.get('pb')
    if pb is not None:
        total_checks += 1
        if 0 < pb < 3.0:
            score += 1
            opportunities.append("Waarde: De koers ligt relatief dicht bij de boekwaarde van de bezittingen.")
        elif pb > 10:
            risks.append("Substantie: Je betaalt een hoge premie voor 'lucht' bovenop de boekwaarde.")
        else:
            score += 0.5
            neutral.append(f"Waarde: De P/B ratio ({pb:.2f}) is normaal voor deze sector.")

    final_score = (score / total_checks) * 10 if total_checks > 0 else 5.0
    return final_score, opportunities, risks, neutral

# --- PORTFOLIO ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
def save_portfolio_json(): return json.dumps(st.session_state['portfolio'])
def load_portfolio_json(uploaded_file):
    if uploaded_file:
        try: st.session_state['portfolio'] = json.load(uploaded_file); st.success("✅ Portfolio geladen!")
        except: st.error("❌ Fout bestand.")

# --- ZIJBALK ---
st.sidebar.header("💾 Data Opslag")
st.sidebar.download_button("⬇️ Download", save_portfolio_json(), "portfolio.json", "application/json")
uploaded = st.sidebar.file_uploader("⬆️ Upload", type=['json'])
if uploaded: load_portfolio_json(uploaded)
st.sidebar.markdown("---")
st.sidebar.info("Tip: Gebruik de zoekbalk linksboven in het hoofdscherm.")

# --- TABBLADEN ---
tabs = st.tabs(["🔎 Analyse & Nieuws", "📊 Mijn Portfolio"])

# ==================================================
# TAB 1: ANALYSE
# ==================================================
with tabs[0]:
    col_search, col_period = st.columns([3, 1])
    with col_search:
        search_text = st.text_input("🔍 Zoek Aandeel:", placeholder="Bijv. ASML, Salesforce...")
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

                # --- KOERSDOEL LOGICA ---
                tgt = safe_get(info, 'targetMeanPrice')
                if tgt is None and 'target' in finviz_data: tgt = finviz_data['target']
                
                tgt_eur = tgt * eur_rate if tgt else None
                upside_txt = ""
                tgt_color = "normal"
                if tgt_eur:
                    upside = ((tgt_eur - price_eur) / price_eur) * 100
                    upside_txt = f"{'+' if upside > 0 else ''}{upside:.1f}%"
                    tgt_color = "normal" if upside < 0 else "inverse" # Groen als er upside is

                # --- HEADER ---
                st.markdown("---")
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1:
                    st.title(safe_get(info, 'shortName', selected_ticker))
                    st.caption(f"{selected_ticker} | {safe_get(info, 'exchange', '-')}")
                with c2:
                    st.metric("Huidige Koers", f"€ {price_eur:.2f}")
                with c3:
                    # Koersdoel hier tonen
                    st.metric("Koersdoel", f"€ {tgt_eur:.2f}" if tgt_eur else "N/A", delta=upside_txt if tgt_eur else None)
                with c4:
                    with st.popover("➕ Toevoegen"):
                        with st.form("add"):
                            amt = st.number_input("Aantal", min_value=0.0, value=1.0)
                            price = st.number_input("GAK (€)", value=float(round(price_eur, 2)))
                            if st.form_submit_button("Opslaan"):
                                st.session_state['portfolio'].append({"ticker": selected_ticker, "amount": amt, "avg_price_eur": price})
                                st.toast("Opgeslagen!", icon="✅")

                # --- METRICS VERZAMELEN ---
                pe = safe_get(info, 'trailingPE')
                fwd_pe = safe_get(info, 'forwardPE')
                
                peg = safe_get(info, 'pegRatio')
                peg_source = "Yahoo"
                if peg is None and 'peg' in finviz_data:
                    peg = finviz_data['peg']; peg_source = "Finviz"
                if peg is None and pe:
                    growth = safe_get(info, 'earningsGrowth') 
                    if growth: peg = pe / (growth * 100); peg_source = "Schatting"
                
                div_yield = safe_get(info, 'dividendYield')
                if div_yield is None:
                    div_rate = safe_get(info, 'dividendRate')
                    if div_rate: div_yield = div_rate / hist['Close'].iloc[-1]
                if div_yield and div_yield > 0.5: div_yield = div_yield / 100
                
                pb = safe_get(info, 'priceToBook')
                if pb is None and 'pb' in finviz_data: pb = finviz_data['pb']
                
                de_ratio = safe_get(info, 'debtToEquity')
                if de_ratio: de_ratio = de_ratio / 100
                
                cr = safe_get(info, 'currentRatio')
                
                # RSI
                delta = hist['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50
                
                beta = safe_get(info, 'beta')

                # --- FUNDAMENTELE ANALYSE & AI ---
                metrics_dict = {'peg': peg, 'roe': safe_get(info, 'returnOnEquity'), 'de': de_ratio, 'cr': cr, 'fwd_pe': fwd_pe, 'pb': pb}
                score, opps, risks, neutral = calculate_score(metrics_dict)
                
                st.markdown("### 🧬 Fundamentele Analyse & AI Oordeel")
                
                # Container voor nette styling
                with st.container(border=True):
                    col_score, col_txt = st.columns([1, 4])
                    
                    with col_score:
                        color = "green" if score >= 7 else ("orange" if score >= 5.5 else "red")
                        # Flexbox centrering voor de cirkel
                        st.markdown(f"""
                        <div style="display: flex; justify-content: center; align-items: center; height: 100%;">
                            <div style="display: flex; justify-content: center; align-items: center; border: 6px solid {color}; width: 120px; height: 120px; border-radius: 50%;">
                                <h1 style="margin:0; padding:0; color:{color}; font-size: 3rem;">{score:.1f}</h1>
                            </div>
                        </div>
                        <p style="text-align:center; margin-top: 10px;"><b>AI Score</b></p>
                        """, unsafe_allow_html=True)
                    
                    with col_txt:
                        if not opps and not risks and not neutral:
                            st.info("Onvoldoende data beschikbaar voor een AI oordeel.")
                        else:
                            # Samenvatting in kolommen
                            c_plus, c_min, c_neut = st.columns(3)
                            with c_plus:
                                if opps:
                                    st.success("**✅ Sterke Punten**")
                                    for o in opps: st.write(f"- {o}")
                                else: st.write("Geen uitgesproken sterke punten.")
                            with c_min:
                                if risks:
                                    st.error("**⚠️ Aandachtspunten**")
                                    for r in risks: st.write(f"- {r}")
                                else: st.write("Geen grote risico's gedetecteerd.")
                            with c_neut:
                                if neutral:
                                    st.info("**⚪ Neutraal / Normaal**")
                                    for n in neutral: st.write(f"- {n}")

                st.divider()

                # --- METRICS GRID (Met Tooltips & Kleuren) ---
                st.subheader("📊 Financiële Ratio's")
                
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                
                with m1:
                    val = peg if peg else 0
                    c = "🟢" if 0 < val < 1.5 else ("🟠" if val < 0 else "🟡")
                    st.metric("PEG Ratio", f"{c} {val:.2f}" if peg else "N/A", help=f"Bron: {peg_source}.\n\nPrijs t.o.v. Groei. < 1.5 is vaak koopwaardig voor groeibedrijven. Negatief betekent krimp.")
                with m2:
                    val = fwd_pe if fwd_pe else 0
                    st.metric("Forward P/E", f"{'🟢' if 0<val<20 else '🟡'} {val:.1f}" if fwd_pe else "N/A", help="Verwachte K/W voor komend jaar.\n\n< 20 is vaak aantrekkelijk. > 30 is duur (hoge verwachtingen).")
                with m3:
                    val = cr if cr else 0
                    st.metric("Current Ratio", f"{'🟢' if val>1.5 else '🟠'} {val:.2f}" if cr else "N/A", help="Liquiditeit.\n\n> 1.5 betekent dat er genoeg geld is om korte termijn rekeningen te betalen.")
                with m4:
                    val = de_ratio if de_ratio else 0
                    st.metric("Debt/Equity", f"{'🟢' if val<1 else '🟠'} {val:.2f}" if de_ratio else "N/A", help="Schuldgraad.\n\n< 1.0 is gezond. > 2.0 betekent veel schulden.")
                with m5:
                    val = pb if pb else 0
                    st.metric("P/B Ratio", f"{'🟢' if val<3 else '⚪'} {val:.2f}" if pb else "N/A", help="Prijs / Boekwaarde.\n\n< 1.0 is goedkoop (onder boekwaarde). Bij tech/software vaak hoger.")
                with m6: 
                    roe = safe_get(info, 'returnOnEquity', 0)
                    st.metric("ROE", f"{'🟢' if roe>0.15 else '🟡'} {roe:.1%}", help="Rendement op Eigen Vermogen.\n\n> 15% duidt op een sterk bedrijf met competitief voordeel.")

                st.write("")
                x1, x2, x3, x4 = st.columns(4)
                
                with x1:
                    dy = div_yield if div_yield else 0
                    # Kleurcode Dividend
                    if dy > 0.03: c = "🟢" # Hoog
                    elif dy > 0.015: c = "🔵" # Gemiddeld
                    else: c = "⚪" # Laag/Geen
                    st.metric("Dividend", f"{c} {dy:.2%}", help="Dividend Rendement (Jaarlijks).\n\n> 3% is aantrekkelijk voor inkomen. Laag is normaal voor snelle groeiers.")
                
                with x2: 
                    st.metric("RSI (14)", f"{'🟢' if rsi_val<30 else '⚪'} {rsi_val:.0f}", help="Momentum Indicator.\n\n< 30: Oversold (Potentieel bodem/koopkans).\n> 70: Overbought (Misschien te duur).")
                
                with x3: 
                    b = beta if beta else 1.0
                    # Kleurcode Beta
                    if b < 0.8: c = "🟢" # Defensief
                    elif b < 1.2: c = "⚪" # Markt
                    elif b < 1.8: c = "🟠" # Volatiel
                    else: c = "🔴" # Zeer volatiel
                    st.metric("Beta", f"{c} {b:.2f}", help="Volatiliteit t.o.v. de markt.\n\n1.0 = Beweegt met markt mee.\n< 1.0 = Stabiel/Defensief.\n> 1.5 = Wild/Risicovol.")
                
                with x4: 
                     st.metric("Winstmarge", f"{safe_get(info, 'profitMargins', 0):.1%}", help="Nettowinst als percentage van de omzet.")

                st.markdown("### 📈 Koersverloop")
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
                        st.success(f"Gevonden: {comp} Analysis")
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
