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
import numpy as np

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- STATE ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []

# --- CACHE CONTROL ---
# Knop om data te verversen (cache legen)
if st.sidebar.button("🔄 Ververs Alle Data"):
    st.cache_data.clear()
    st.rerun()

# --- HULPFUNCTIES ---

@st.cache_data(ttl=3600)
def get_currency_rate(from_currency, to_currency="EUR"):
    if from_currency == to_currency: return 1.0
    if not from_currency or from_currency == 'nan': return 1.0
    try:
        # Probeer EUR=X (USD->EUR)
        ticker = "EUR=X" if from_currency == "USD" and to_currency == "EUR" else f"{from_currency}{to_currency}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            # Sanity check: USD/EUR moet < 1.0 zijn (bv 0.95). Als > 1.05, is het wss omgekeerd.
            if from_currency == "USD" and to_currency == "EUR" and rate > 1.05:
                return 1 / rate
            return rate
        # Fallback
        if from_currency == "USD": return 0.95
        return 1.0
    except: return 1.0

def safe_get(info_dict, key, default=None):
    if not info_dict: return default
    val = info_dict.get(key)
    return val if val is not None else default

def truncate_text(text, max_len=20):
    if text and len(text) > max_len: return text[:max_len] + "..."
    return text

@st.cache_data(ttl=3600)
def get_finviz_data(ticker):
    """Haalt data van Finviz (Back-up)"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        r = requests.get(url, headers=headers, timeout=5)
        data = {}
        if r.status_code == 200:
            text = r.text
            # Regex voor metrics
            if 'PEG' in text: 
                m = re.search(r'>PEG<.*?<b>\s*([\d\.]+)\s*</b>', text, re.DOTALL)
                if m: data['peg'] = float(m.group(1))
            if 'P/B' in text:
                m = re.search(r'>P/B<.*?<b>\s*([\d\.]+)\s*</b>', text, re.DOTALL)
                if m: data['pb'] = float(m.group(1))
            if 'Forward P/E' in text:
                m = re.search(r'>Forward P/E<.*?<b>\s*([\d\.]+)\s*</b>', text, re.DOTALL)
                if m: data['fwd_pe'] = float(m.group(1))
            if 'Target Price' in text:
                m = re.search(r'>Target Price<.*?<b>\s*([\d\.]+)\s*</b>', text, re.DOTALL)
                if m: data['target'] = float(m.group(1))
        return data
    except: return {}

def search_yahoo(query):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=DE&quotesCount=6"
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        results = []
        if 'quotes' in data:
            for q in data['quotes']:
                symbol = q.get('symbol')
                name = q.get('shortname', q.get('longname', symbol))
                exch = q.get('exchange', 'N/A')
                # Markeer Duitse tickers
                label = f"{symbol} | {name} ({exch})"
                results.append(label)
        return results
    except: return []

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    try:
        q = f"{query} stock analysis {datetime.now().year}"
        url = f"https://www.youtube.com/results?search_query={q.replace(' ', '+')}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=5)
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except: return None

def get_google_news(query):
    try:
        q = f"{query} stock finance news"
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, timeout=5)
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall('./channel/item')[:5]:
            title = item.find('title').text
            link = item.find('link').text
            try:
                dt = datetime.strptime(item.find('pubDate').text[:16], '%a, %d %b %Y')
                d_str = dt.strftime('%d %b %Y')
            except: d_str = ""
            items.append({"title": title, "link": link, "date": d_str})
        return items
    except: return []

def calculate_score(metrics):
    score = 0
    total = 0
    opps, risks, neut = [], [], []
    
    # PEG
    peg = metrics.get('peg')
    if peg is not None:
        total += 1
        if 0 < peg < 1.5: score += 1; opps.append(f"PEG ({peg:.2f}) is aantrekkelijk.")
        elif peg < 0: risks.append("Negatieve PEG (Krimp).")
        elif peg > 2.5: risks.append(f"Hoge PEG ({peg:.2f}).")
        else: score += 0.5; neut.append(f"PEG ({peg:.2f}) marktconform.")
    
    # Fwd PE
    fwd = metrics.get('fwd_pe')
    if fwd is not None:
        total += 1
        if 0 < fwd < 20: score += 1; opps.append(f"Lage Forward P/E ({fwd:.1f}).")
        elif fwd > 40: risks.append(f"Hoge Forward P/E ({fwd:.1f}).")
        else: score += 0.5; neut.append(f"Forward P/E ({fwd:.1f}) redelijk.")
        
    # ROE
    roe = metrics.get('roe')
    if roe is not None:
        total += 1
        if roe > 0.15: score += 1; opps.append(f"Sterke ROE ({roe:.1%}).")
        elif roe < 0.05: risks.append(f"Zwakke ROE ({roe:.1%}).")
        else: score += 0.5; neut.append(f"ROE ({roe:.1%}) gemiddeld.")
        
    # Debt
    de = metrics.get('de')
    if de is not None:
        total += 1
        if de < 1.0: score += 1; opps.append("Gezonde balans (weinig schuld).")
        elif de > 2.0: risks.append(f"Veel schulden (D/E: {de:.2f}).")
        else: score += 0.5; neut.append("Schuldniveau acceptabel.")
        
    # Current Ratio
    cr = metrics.get('cr')
    if cr is not None:
        total += 1
        if cr > 1.5: score += 1; opps.append("Goede liquiditeit.")
        elif cr < 1.0: risks.append("Krap bij kas (<1.0).")
        else: score += 0.5; neut.append("Liquiditeit voldoende.")
        
    # P/B
    pb = metrics.get('pb')
    if pb is not None:
        total += 1
        if 0 < pb < 3.0: score += 1; opps.append("Koers dichtbij boekwaarde.")
        elif pb > 10: risks.append(f"Hoge P/B ({pb:.2f}).")
        else: score += 0.5; neut.append("P/B normaal.")

    final = (score / total) * 10 if total > 0 else 5.0
    return final, opps, risks, neut

# --- SAVE/LOAD ---
def save_data():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

def load_data(f):
    try:
        d = json.load(f)
        if isinstance(d, list): st.session_state['portfolio'] = d
        else:
            st.session_state['portfolio'] = d.get('portfolio', [])
            st.session_state['wishlist'] = d.get('wishlist', [])
        st.success("✅ Data geladen!")
    except: st.error("Fout bestand.")

# --- SIDEBAR ---
st.sidebar.header("📂 Data")
st.sidebar.download_button("⬇️ Download Alles", save_data(), "aandelen_data.json", "application/json")
up = st.sidebar.file_uploader("⬆️ Upload", type=['json'])
if up: load_data(up)
st.sidebar.divider()
st.sidebar.info("Tip: Gebruik .DE of .F tickers voor Xetra koersen!")

# --- TABS ---
tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist"])

# ==================================================
# TAB 1: ANALYSE
# ==================================================
with tabs[0]:
    c_search, c_period = st.columns([3, 1])
    with c_search:
        def_q = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek Aandeel:", value=def_q, placeholder="Bijv. PDD, ASML.AS...", key="search")
        
        sel_ticker = None
        if txt and "." in txt and " " not in txt: sel_ticker = txt
        elif txt:
            res = search_yahoo(txt)
            if res:
                ch = st.selectbox("Resultaten:", res)
                sel_ticker = ch.split(" | ")[0]
            else: sel_ticker = txt.upper()
            
    with c_period:
        per = st.selectbox("Periode", ["1mo", "6mo", "1y", "3y", "5y"], index=2)

    if sel_ticker:
        try:
            stock = yf.Ticker(sel_ticker)
            info = stock.info
            hist = stock.history(period=per)
            
            if hist.empty:
                st.error("Geen data. Probeer een andere ticker (bv. 0DD.F voor PDD op Xetra).")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price_eur = hist['Close'].iloc[-1] * rate
                
                # Data verzamelen
                fin = get_finviz_data(sel_ticker)
                
                pe = safe_get(info, 'trailingPE')
                fwd_pe = safe_get(info, 'forwardPE')
                if fwd_pe is None: fwd_pe = fin.get('fwd_pe')
                
                peg = safe_get(info, 'pegRatio')
                peg_src = "Yahoo"
                if peg is None: peg = fin.get('peg'); peg_src = "Finviz"
                if peg is None and (pe or fwd_pe):
                    base_pe = pe if pe else fwd_pe
                    g = safe_get(info, 'earningsGrowth')
                    if base_pe and g: peg = base_pe/(g*100); peg_src = "Schatting"

                dy = safe_get(info, 'dividendYield')
                if dy is None:
                    dr = safe_get(info, 'dividendRate')
                    if dr: dy = dr / hist['Close'].iloc[-1]
                if dy and dy > 0.5: dy = dy / 100
                
                pb = safe_get(info, 'priceToBook')
                if pb is None: pb = fin.get('pb')
                
                de = safe_get(info, 'debtToEquity')
                de_r = de/100 if de else None
                
                cr = safe_get(info, 'currentRatio')
                beta = safe_get(info, 'beta')
                marg = safe_get(info, 'profitMargins')
                
                delta = hist['Close'].diff()
                gain = (delta.where(delta>0, 0)).rolling(14).mean()
                loss = (-delta.where(delta<0, 0)).rolling(14).mean()
                rs = gain/loss
                rsi = 100 - (100/(1+rs))
                rsi_val = rsi.iloc[-1] if not rsi.empty else 50
                
                tgt = safe_get(info, 'targetMeanPrice')
                if tgt is None: tgt = fin.get('target')
                tgt_eur = tgt * rate if tgt else None
                upside = f"{((tgt_eur-price_eur)/price_eur)*100:+.1f}%" if tgt_eur else ""

                # UI
                st.markdown("---")
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1:
                    nm = safe_get(info, 'shortName', sel_ticker)
                    st.title(truncate_text(nm))
                    st.caption(f"{sel_ticker} | {safe_get(info, 'exchange', '-')}")
                with c2: st.metric("Koers", f"€ {price_eur:.2f}")
                with c3: st.metric("Koersdoel", f"€ {tgt_eur:.2f}" if tgt_eur else "N/A", delta=upside)
                with c4:
                    with st.popover("➕ Toevoegen"):
                        tp, tw = st.tabs(["Portfolio", "Wishlist"])
                        with tp:
                            with st.form("fp"):
                                a = st.number_input("Aantal", 1.0)
                                p = st.number_input("GAK (€)", value=float(round(price_eur, 2)))
                                if st.form_submit_button("Opslaan"):
                                    st.session_state['portfolio'].append({"ticker": sel_ticker, "amount": a, "avg_price_eur": p})
                                    st.toast("Toegevoegd!", icon="✅")
                        with tw:
                            if st.button("Zet op Wishlist"):
                                exists = any(x['ticker'] == sel_ticker for x in st.session_state['wishlist'])
                                if not exists:
                                    st.session_state['wishlist'].append({"ticker": sel_ticker, "name": nm})
                                    st.toast("Toegevoegd!", icon="❤️")

                # SCORE
                met_d = {'peg': peg, 'roe': safe_get(info, 'returnOnEquity'), 'de': de_r, 'cr': cr, 'fwd_pe': fwd_pe, 'pb': pb}
                score, opps, risks, neut = calculate_score(met_d)
                
                st.markdown("### 🧬 Fundamentele Analyse")
                with st.container(border=True):
                    sc, txt = st.columns([1, 4])
                    with sc:
                        col = "green" if score>=7 else ("orange" if score>=5.5 else "red")
                        st.markdown(f"""<div style="display:flex;justify-content:center;"><div style="border:6px solid {col};width:100px;height:100px;border-radius:50%;display:flex;align-items:center;justify-content:center;"><span style="color:{col};font-size:2.5rem;font-weight:bold;">{score:.1f}</span></div></div>""", unsafe_allow_html=True)
                    with txt:
                        c_k, c_r, c_n = st.columns(3)
                        with c_k:
                            if opps:
                                st.success("**✅ Kansen**")
                                for o in opps: st.write(f"- {o}")
                        with c_r:
                            if risks:
                                st.error("**⚠️ Risico's**")
                                for r in risks: st.write(f"- {r}")
                        with c_n:
                            if neut:
                                st.info("**⚪ Neutraal**")
                                for n in neut: st.write(f"- {n}")

                st.divider()
                st.subheader("📊 Ratio's")
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                
                with m1:
                    c = "🟢" if peg and 0<peg<1.5 else ("🟠" if peg and peg<0 else "🟡")
                    st.metric("PEG Ratio", f"{c} {peg:.2f}" if peg else "N/A", help=f"Bron: {peg_src}")
                with m2:
                    c = "🟢" if fwd_pe and 0<fwd_pe<20 else "🟡"
                    st.metric("Forward P/E", f"{c} {fwd_pe:.1f}" if fwd_pe else "N/A")
                with m3:
                    c = "🟢" if cr and cr>1.5 else "🟠"
                    st.metric("Current Ratio", f"{c} {cr:.2f}" if cr else "N/A")
                with m4:
                    c = "🟢" if de_r and de_r<1 else "🟠"
                    st.metric("Debt/Equity", f"{c} {de_r:.2f}" if de_r else "N/A")
                with m5:
                    st.metric("P/B Ratio", f"{pb:.2f}" if pb else "N/A")
                with m6:
                    r = safe_get(info, 'returnOnEquity')
                    c = "🟢" if r and r>0.15 else "🟡"
                    st.metric("ROE", f"{c} {r:.1%}" if r else "N/A")

                st.write("")
                x1, x2, x3, x4 = st.columns(4)
                with x1:
                    c = "🟢" if dy and dy>0.03 else ("🔵" if dy and dy>0.015 else "⚪")
                    st.metric("Dividend", f"{c} {dy:.2%}" if dy else "0%")
                with x2:
                    c = "🟢" if rsi_val<30 else "⚪"
                    st.metric("RSI (14)", f"{c} {rsi_val:.0f}")
                with x3:
                    c = "🟢" if beta and beta<0.8 else ("🟠" if beta and beta>1.5 else "⚪")
                    st.metric("Beta", f"{c} {beta:.2f}" if beta else "1.0")
                with x4:
                    c = "🟢" if marg and marg>0.2 else ("🔵" if marg and marg>0.1 else "🟠")
                    st.metric("Winstmarge", f"{c} {marg:.1%}" if marg else "N/A")

                st.markdown("### 📈 Koers")
                fig = go.Figure()
                lc = '#00C805' if hist['Close'].iloc[-1] >= hist['Close'].iloc[0] else '#FF5000'
                fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], mode='lines', line=dict(color=lc)))
                fig.update_layout(template="plotly_dark", margin=dict(t=10,b=0,l=0,r=0), height=350)
                st.plotly_chart(fig, use_container_width=True)

                cn, cv = st.columns(2)
                with cn:
                    st.markdown("### 📰 Nieuws")
                    for n in get_google_news(safe_get(info, 'shortName', sel_ticker)):
                        st.markdown(f"**[{n['title']}]({n['link']})**")
                        st.caption(n['date'])
                        st.divider()
                with cv:
                    st.markdown("### 📺 Video")
                    vid = get_youtube_video_id(safe_get(info, 'shortName', sel_ticker))
                    if vid: st.video(f"https://www.youtube.com/watch?v={vid}")
                    url = f"https://www.youtube.com/results?search_query={sel_ticker}+stock+analysis"
                    st.markdown(f"👉 **[Zoek op YouTube]({url})**")

        except Exception as e: st.error(f"Fout: {e}")

# ==================================================
# TAB 2: PORTFOLIO
# ==================================================
with tabs[1]:
    st.title("Mijn Portfolio")
    
    if st.session_state['portfolio']:
        # --- EDITOR MODUS ---
        with st.expander("✏️ Portfolio Bewerken / Correcties"):
            st.info("Klopt een koers niet? Wijzig hier de ticker (bijv. PDD -> 0DD.F voor Xetra).")
            
            # We tonen een data editor om direct waarden aan te passen
            df_edit = pd.DataFrame(st.session_state['portfolio'])
            edited_df = st.data_editor(
                df_edit, 
                num_rows="dynamic",
                column_config={
                    "ticker": st.column_config.TextColumn("Ticker (bv. 0DD.F)"),
                    "amount": st.column_config.NumberColumn("Aantal", min_value=0.0),
                    "avg_price_eur": st.column_config.NumberColumn("GAK (€)", min_value=0.0)
                },
                key="editor"
            )
            
            if st.button("💾 Wijzigingen Opslaan"):
                st.session_state['portfolio'] = edited_df.to_dict('records')
                st.rerun()

        # --- BEREKENINGEN ---
        rows = []
        tot_inv = 0
        tot_val = 0
        sec_dist = {}
        div_cal = []
        
        bar = st.progress(0)
        items = st.session_state['portfolio']
        for i, item in enumerate(items):
            try:
                t = item['ticker']
                s = yf.Ticker(t)
                h = s.history(period="1d")
                
                p_eur = 0
                rate = 1.0
                
                if not h.empty:
                    raw = h['Close'].iloc[-1]
                    curr = safe_get(s.info, 'currency', 'EUR')
                    rate = get_currency_rate(curr, "EUR")
                    p_eur = raw * rate
                else:
                    # Fallback als Yahoo faalt (behoudt GAK voor berekening, maar toont error)
                    p_eur = item['avg_price_eur'] 
                
                val = p_eur * item['amount']
                cost = item['avg_price_eur'] * item['amount']
                prof = val - cost
                
                sec = safe_get(s.info, 'sector', 'Overig')
                sec_dist[sec] = sec_dist.get(sec, 0) + val
                
                # Dividend
                dr = safe_get(s.info, 'dividendRate')
                if not dr:
                    dy = safe_get(s.info, 'dividendYield')
                    if dy and p_eur>0: dr = dy * (p_eur/rate)
                
                if dr:
                    dy_eur = dr * rate * item['amount']
                    ex = safe_get(s.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m-%Y') if ex else "-"
                    div_cal.append({"Ticker": t, "Ex-Date": exs, "Jaarlijks (€)": dy_eur, "Mnd (€)": dy_eur/12})
                
                rows.append({
                    "Ticker": t,
                    "Aantal": item['amount'],
                    "GAK (€)": item['avg_price_eur'],
                    "Huidige Prijs (€)": p_eur,
                    "Waarde (€)": val,
                    "Winst (€)": prof,
                    "Rendement (%)": (prof/cost)*100 if cost>0 else 0
                })
                tot_val += val
                tot_inv += cost
            except: pass
            bar.progress((i+1)/len(items))
        bar.empty()
        
        tres = tot_val - tot_inv
        
        # KPI's
        k1, k2, k3 = st.columns(3)
        k1.metric("Waarde", f"€ {tot_val:,.2f}")
        k2.metric("Winst/Verlies", f"€ {tres:,.2f}", delta=f"{(tres/tot_inv)*100:.1f}%" if tot_inv>0 else "0%")
        k3.metric("Inleg", f"€ {tot_inv:,.2f}")
        
        st.divider()
        
        # VISUALISATIE
        c_sec, c_div = st.columns(2)
        with c_sec:
            st.subheader("Sectoren")
            if sec_dist:
                fig = px.pie(values=list(sec_dist.values()), names=list(sec_dist.keys()), hole=0.4)
                fig.update_layout(margin=dict(t=0,b=0,l=0,r=0), height=250)
                st.plotly_chart(fig, use_container_width=True)
        with c_div:
            st.subheader("💰 Dividend")
            if div_cal:
                df_d = pd.DataFrame(div_cal)
                st.metric("Geschat Mnd Inkomen", f"€ {df_d['Mnd (€)'].sum():.2f}")
                st.dataframe(df_d[["Ticker", "Ex-Date", "Jaarlijks (€)"]], hide_index=True, use_container_width=True)
            else: st.info("Geen dividenddata.")

        st.subheader("Details")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, column_config={
            "Huidige Prijs (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Waarde (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Winst (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Rendement (%)": st.column_config.NumberColumn(format="%.1f%%")
        })
        
        with st.expander("🧮 Rente-op-Rente Planner"):
            cc1, cc2, cc3, cc4 = st.columns(4)
            sv = cc1.number_input("Start", value=float(round(tot_val, 0)))
            mi = cc2.number_input("Mnd Inleg", 500)
            jr = cc3.number_input("Jaren", 20)
            rr = cc4.number_input("Rendement %", 7.0)
            
            mths = np.arange(jr*12+1)
            mr = rr/100/12
            fut, inv = [sv], [sv]
            c, ci = sv, sv
            for _ in range(len(mths)-1):
                c = (c+mi)*(1+mr)
                ci += mi
                fut.append(c)
                inv.append(ci)
            
            st.metric(f"Na {jr} jaar", f"€ {fut[-1]:,.0f}", delta=f"Winst: € {fut[-1]-inv[-1]:,.0f}")
            df_c = pd.DataFrame({"Maand": mths, "Waarde": fut, "Inleg": inv})
            st.plotly_chart(px.line(df_c, x="Maand", y=["Waarde", "Inleg"]), use_container_width=True)
            
    else: st.info("Nog geen aandelen.")

# ==================================================
# TAB 3: WISHLIST
# ==================================================
with tabs[2]:
    st.title("❤️ Mijn Wishlist")
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1: st.subheader(f"{item['name']} ({item['ticker']})")
                with c2:
                    if st.button("🔍 Analyseer", key=f"w_{item['ticker']}"):
                        st.session_state['search_query'] = item['ticker']
                        st.toast("Ga naar Tabblad Analyse!", icon="🚀")
        if st.button("Legen"):
            st.session_state['wishlist'] = []
            st.rerun()
    else: st.info("Leeg.")
