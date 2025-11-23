import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- API KEYS & CONSTANTEN ---
FMP_KEY = "nhXfJ0SDaXmUEVKy5icfcwzQOHqOn5QE"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Mapping: EU Ticker -> US Ticker (FMP optimalisatie)
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD"
}

TOOLTIPS = {
    "peg": "Price/Earnings to Growth.\n🟢 < 1.5: Ondergewaardeerd.\n🔴 > 2.5: Overgewaardeerd.",
    "pe": "Koers/Winst verhouding.\n🟢 < 20: Koopje.\n🟠 > 30: Premie.",
    "roe": "Rendement op Eigen Vermogen.\n🟢 > 15%: Sterk management.",
    "div": "Dividend Rendement.\n🟢 > 3%: Interessant.",
}

# --- STATE MANAGEMENT ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []

# --- HELPERS ---

@st.cache_data(ttl=600)
def get_finviz_data(ticker):
    """Scrapet Finviz voor extra data (vooral PEG en Targets)"""
    d = {}
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        r = requests.get(url, headers=HEADERS, timeout=4)
        if r.status_code == 200:
            def p(label):
                m = re.search(rf'>{label}</td>.*?<b>\s*([\d\.-]+)\s*</b>', r.text, re.DOTALL)
                return float(m.group(1)) if m and m.group(1) != '-' else None
            
            d['peg'] = p('PEG')
            d['target'] = p('Target Price')
            d['eps'] = p('EPS \(ttm\)')
            d['pe'] = p('P/E')
    except: pass
    return d

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
            if from_curr == "USD" and rate > 1.10: return 1/rate # Sanity check (USD/EUR is meestal < 1)
            return rate
        return 1.0
    except: return 1.0

def search_yahoo_strict(query):
    """Zoekt aandelen en sorteert EU/DE bovenaan."""
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=DE&quotesCount=15"
        r = requests.get(url, headers=HEADERS, timeout=3)
        d = r.json()
        res = []
        raw_quotes = d.get('quotes', [])
        
        def sort_key(q):
            s = q.get('symbol', '')
            if s.endswith('.AS'): return 0
            if s.endswith('.DE') or s.endswith('.F'): return 1
            if any(s.endswith(x) for x in ['.PA', '.BR', '.MI', '.MC', '.LS']): return 2
            if '.' not in s: return 10 
            return 20 

        raw_quotes.sort(key=sort_key)
        for q in raw_quotes:
            s = q.get('symbol')
            n = q.get('shortname') or q.get('longname') or s
            res.append(f"{s} | {n}")
        return res
    except: return []

def get_google_news(query):
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={query}+stock&hl=nl&gl=NL&ceid=NL:nl", timeout=4)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:3]:
            d = i.find('pubDate').text[:16]
            items.append({"title": i.find('title').text, "link": i.find('link').text, "date": d})
        return items
    except: return []

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    try:
        url = f"https://www.youtube.com/results?search_query={query.replace(' ','+')}+stock+analysis+{datetime.now().year}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except: return None

# --- DATA ENGINE ---

class DataEngine:
    def __init__(self, ticker, original_ticker):
        self.ticker = ticker
        self.original_ticker = original_ticker
        self.yf_obj = yf.Ticker(original_ticker)
        self.yf_info = self.yf_obj.info
        self.fmp_ratios = {}
        self.fmp_quote = {}
        self.finviz_data = {}
        
        self.fetch_fmp()
        self.fetch_finviz()

    def fetch_fmp(self):
        try:
            url_ratios = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{self.ticker}?apikey={FMP_KEY}"
            r = requests.get(url_ratios, timeout=2).json()
            if isinstance(r, list) and r: self.fmp_ratios = r[0]

            url_quote = f"https://financialmodelingprep.com/api/v3/quote/{self.ticker}?apikey={FMP_KEY}"
            q = requests.get(url_quote, timeout=2).json()
            if isinstance(q, list) and q: self.fmp_quote = q[0]
        except: pass

    def fetch_finviz(self):
        if "." not in self.ticker:
            self.finviz_data = get_finviz_data(self.ticker)

    def get_combined_metric(self, key_yf, key_fmp_ratio, key_fmp_quote=None, key_finviz=None, is_percent=False):
        values = []
        sources = []
        
        v_yf = self.yf_info.get(key_yf)
        if v_yf is not None: values.append(float(v_yf)); sources.append("Yahoo")
        
        v_fmp = self.fmp_ratios.get(key_fmp_ratio)
        if v_fmp is None and key_fmp_quote: v_fmp = self.fmp_quote.get(key_fmp_quote)
        if v_fmp is not None: values.append(float(v_fmp)); sources.append("FMP")
        
        if key_finviz:
            v_fin = self.finviz_data.get(key_finviz)
            if v_fin is not None: values.append(float(v_fin)); sources.append("Finviz")

        if not values: return None, "N/A"
        
        avg_val = sum(values) / len(values)
        if is_percent and avg_val < 0.90: avg_val *= 100
        
        return avg_val, " & ".join(sources)

# --- AI LOGICA ---
def calculate_ai_score(data):
    score, count = 0, 0
    reasons = []
    
    rules = [
        (data['peg'][0], "PEG", lambda x: 0<x<1.5, lambda x: x>2.5, "Goedkoop (PEG)", "Te duur (PEG)"),
        (data['pe'][0], "P/E", lambda x: 0<x<20, lambda x: x>40, "Goede P/E", "Hoge P/E"),
        (data['roe'][0], "ROE", lambda x: x>15, lambda x: x<5, "Sterke ROE", "Zwakke ROE"),
        (data['de'][0], "D/E", lambda x: x<1.0, lambda x: x>2.0, "Veilige balans", "Veel schuld"),
        (data['cr'][0], "Current", lambda x: x>1.5, lambda x: x<0.9, "Liquide", "Krap bij kas"),
        (data['margin'][0], "Marge", lambda x: x>15, lambda x: x<2, "Hoge winstmarge", "Lage winstmarge"),
        (data['eps'][0], "EPS", lambda x: x>0, lambda x: x<0, "Maakt winst", "Maakt verlies")
    ]
    
    for val, name, good, bad, g_msg, b_msg in rules:
        if val is not None:
            count += 1
            if good(val): score += 1; reasons.append(g_msg)
            elif bad(val): reasons.append(b_msg)
            else: score += 0.5 
            
    final = (score/count)*10 if count > 0 else 5.0
    summary = "Het bedrijf scoort gemiddeld."
    if final >= 7.5: summary = f"Sterk aandeel! ({', '.join(reasons[:2]).lower()})"
    elif final <= 4.5: summary = f"Risicovol. ({', '.join(reasons[:2]).lower()})"
    elif reasons: summary = f"Gemengd beeld: {reasons[0].lower()} maar {reasons[-1].lower()}."
    return round(final, 1), summary

# --- UI START ---
st.sidebar.header("📂 Data")
if st.sidebar.button("🧹 Reset Cache"): st.cache_data.clear(); st.rerun()

tabs = st.tabs(["🔎 Analyse", "📊 Portfolio Pro", "❤️ Wishlist", "🇳🇱 Belasting"])

# ==================================================
# 1. ANALYSE
# ==================================================
with tabs[0]:
    c_search, c_per = st.columns([3, 1])
    with c_search:
        dq = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek aandeel:", value=dq, key="search_main")
        sel = None
        if txt:
            if "." in txt and " " not in txt: sel = txt
            else:
                res = search_yahoo_strict(txt)
                sel = st.selectbox("Resultaten:", res).split(" | ")[0] if res else txt.upper()
    with c_per:
        per = st.selectbox("Periode", ["1mo", "6mo", "1y", "5y", "max"], index=2)

    if sel:
        try:
            us_ticker = EU_TO_US_MAP.get(sel, sel.split('.')[0] if '.' in sel else sel)
            engine = DataEngine(us_ticker, sel)
            info = engine.yf_info
            
            d = {}
            d['peg'] = engine.get_combined_metric('pegRatio', 'pegRatioTTM', None, 'peg')
            d['pe'] = engine.get_combined_metric('trailingPE', 'peRatioTTM', 'pe', 'pe')
            d['roe'] = engine.get_combined_metric('returnOnEquity', 'returnOnEquityTTM', None, None, True)
            d['de'] = engine.get_combined_metric('debtToEquity', 'debtEquityRatioTTM')
            d['cr'] = engine.get_combined_metric('currentRatio', 'currentRatioTTM')
            d['pb'] = engine.get_combined_metric('priceToBook', 'priceToBookRatioTTM')
            d['margin'] = engine.get_combined_metric('profitMargins', 'netProfitMarginTTM', None, None, True)
            d['eps'] = engine.get_combined_metric('trailingEps', 'netIncomePerShareTTM', 'eps', 'eps')
            d['div'] = engine.get_combined_metric('dividendYield', 'dividendYielTTM', None, None, True)
            
            curr_sym = info.get('currencySymbol', '€')
            curr_code = info.get('currency', 'EUR')
            cur_price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
            
            tgt_val = info.get('targetMeanPrice')
            tgt_src = "Yahoo"
            if not tgt_val:
                tgt_val = engine.finviz_data.get('target')
                tgt_src = "Finviz"
            
            score, summary = calculate_ai_score(d)

            with st.container(border=True):
                h1, h2, h3 = st.columns([2, 1, 1])
                with h1:
                    st.title(info.get('shortName', sel))
                    st.caption(f"Ticker: {sel} | Valuta: {curr_code}")
                with h2:
                    st.metric("Prijs", f"{curr_sym} {cur_price:.2f}" if cur_price else "-")
                with h3:
                    if tgt_val and cur_price:
                        delta = (tgt_val - cur_price) / cur_price
                        st.metric("Koersdoel", f"{curr_sym} {tgt_val:.2f}", f"{delta:.1%}", help=f"Bron: {tgt_src}")
                    else: st.metric("Koersdoel", "N/A")
                
                st.divider()
                a1, a2 = st.columns([1, 4])
                with a1:
                    col = "green" if score >= 7 else "orange" if score >= 5 else "red"
                    st.markdown(f"<h1 style='color:{col};text-align:center'>{score}</h1>", unsafe_allow_html=True)
                with a2:
                    st.subheader("AI Oordeel")
                    st.write(summary)

            st.subheader("📊 Fundamentals")
            def m_col(label, key, good_cond, bad_cond, fmt="{:.2f}", is_pct=False):
                val, src = d[key]
                if val is None: return st.metric(label, "N/A", help=f"Bron: {src}")
                
                delta_val = "✅" if good_cond(val) else "⚠️" if bad_cond(val) else "⚪"
                display = f"{val:.1f}%" if is_pct else fmt.format(val)
                st.metric(label, display, delta=delta_val, delta_color="off", help=f"Waarde: {val}\n{TOOLTIPS.get(key,'')}")

            with st.container(border=True):
                c1, c2, c3, c4 = st.columns(4)
                with c1: m_col("P/E Ratio", 'pe', lambda x: x<20, lambda x: x>35)
                with c2: m_col("PEG Ratio", 'peg', lambda x: x<1.5, lambda x: x>2.5)
                with c3: m_col("EPS", 'eps', lambda x: x>0, lambda x: x<0)
                with c4: m_col("Dividend", 'div', lambda x: x>3, lambda x: x<1, is_pct=True)
                
                c5, c6, c7, c8 = st.columns(4)
                with c5: m_col("ROE", 'roe', lambda x: x>15, lambda x: x<5, is_pct=True)
                with c6: m_col("Marge", 'margin', lambda x: x>15, lambda x: x<2, is_pct=True)
                with c7: m_col("Debt/Eq", 'de', lambda x: x<1, lambda x: x>2)
                with c8: m_col("Current", 'cr', lambda x: x>1.5, lambda x: x<1)

            c_g, c_m = st.columns([2, 1])
            with c_g:
                hist = engine.yf_obj.history(period=per)
                if not hist.empty:
                    fig = px.area(hist, x=hist.index, y='Close', title=f"Koers ({per})")
                    st.plotly_chart(fig, use_container_width=True)
            with c_m:
                st.markdown("##### 📰 Nieuws")
                for n in get_google_news(info.get('shortName', sel)):
                    st.markdown(f"[{n['title']}]({n['link']})\n*{n['date']}*")

            # --- SMART PORTFOLIO ADDER ---
            st.divider()
            with st.popover("➕ Toevoegen aan Portfolio (Smart)", use_container_width=True):
                st.subheader("Positie toevoegen")
                with st.form("add_pf"):
                    amount = st.number_input("Aantal", min_value=1.0, value=10.0)
                    
                    # COMPLEXE LOGICA: VALUTA KEUZE
                    st.write(f"Het aandeel staat genoteerd in **{curr_code}**.")
                    val_opties = [f"Invoer in {curr_code} (Origineel)", "Invoer in EUR (€)"]
                    val_choice = st.radio("Hoe wil je de aankoopprijs opgeven?", val_opties)
                    
                    input_price = st.number_input("Prijs per stuk", value=float(cur_price) if cur_price else 0.0)
                    
                    if st.form_submit_button("Opslaan"):
                        final_avg_price = input_price
                        
                        # Als gebruiker EUR kiest, maar aandeel is USD -> Omrekenen naar USD voor opslag
                        if "EUR" in val_choice and curr_code != "EUR":
                            # We moeten weten hoeveel 1 EUR in USD is
                            # Yahoo geeft EURUSD=X (1 EUR = x USD)
                            rate_eur_usd = get_currency_rate("EUR", curr_code) 
                            final_avg_price = input_price * rate_eur_usd
                            st.toast(f"Omgerekend: €{input_price} = {final_avg_price:.2f} {curr_code}")
                        
                        st.session_state['portfolio'].append({
                            'ticker': sel, 
                            'amount': amount, 
                            'avg_price': final_avg_price, # Altijd native opslaan
                            'currency': curr_code,
                            'symbol': curr_sym
                        })
                        st.success("Toegevoegd!")
                        st.rerun()

            if st.button("❤️ Zet op Wishlist"):
                st.session_state['wishlist'].append({'ticker': sel, 'name': info.get('shortName', sel)})
                st.toast("Toegevoegd aan Wishlist!")

        except Exception as e: st.error(f"Fout: {e}")

# ==================================================
# 2. PORTFOLIO PRO (UITGEBREID)
# ==================================================
with tabs[1]:
    if st.session_state['portfolio']:
        p_data = []
        tot_val_eur = 0
        tot_div_eur = 0
        sectors = {}
        
        # Voortgangsbalk voor API calls
        prog_text = st.empty()
        prog_bar = st.progress(0)
        
        for i, item in enumerate(st.session_state['portfolio']):
            t = item['ticker']
            curr = item.get('currency', 'EUR')
            sym = item.get('symbol', '€')
            prog_text.text(f"Data ophalen voor {t}...")
            
            try:
                # Live data ophalen voor portfolio
                ptk = yf.Ticker(t)
                live_price = ptk.fast_info['last_price']
                p_info = ptk.info
                
                # Extra data voor visuals
                sec = p_info.get('sector', 'Onbekend')
                div_rate = p_info.get('dividendRate', 0)
                if not div_rate: # Fallback yield
                    yld = p_info.get('dividendYield', 0)
                    if yld: div_rate = live_price * yld
                
            except: 
                live_price = item['avg_price']
                sec = "Onbekend"
                div_rate = 0
            
            # Waarden berekenen (Native)
            val_native = live_price * item['amount']
            cost_native = item['avg_price'] * item['amount']
            profit_native = val_native - cost_native
            div_total_native = (div_rate or 0) * item['amount']
            
            # Omrekenen naar EUR voor totalen
            rate_to_eur = get_currency_rate(curr, "EUR")
            
            tot_val_eur += val_native * rate_to_eur
            tot_div_eur += div_total_native * rate_to_eur
            
            # Sector aggregatie
            sectors[sec] = sectors.get(sec, 0) + (val_native * rate_to_eur)
            
            p_data.append({
                "Ticker": t,
                "Aantal": item['amount'],
                "Prijs": f"{sym} {live_price:.2f}",
                "GAK": f"{sym} {item['avg_price']:.2f}",
                "Waarde": f"{sym} {val_native:,.2f}",
                "Winst": profit_native * rate_to_eur, # Voor sorting/color (in EUR waarde)
                "Winst Label": f"{sym} {profit_native:,.2f}",
                "Div/Jaar (Est)": f"€ {div_total_native * rate_to_eur:,.2f}",
                "Sector": sec
            })
            prog_bar.progress((i+1)/len(st.session_state['portfolio']))
            
        prog_bar.empty()
        prog_text.empty()
        
        # --- DASHBOARD METRICS ---
        st.markdown("### 🚀 Portfolio Dashboard")
        k1, k2, k3 = st.columns(3)
        k1.metric("Totale Waarde (EUR)", f"€ {tot_val_eur:,.2f}")
        k2.metric("Geschat Dividend/Jaar", f"€ {tot_div_eur:,.2f}", delta="Passief Inkomen")
        if tot_val_eur > 0:
            k3.metric("Portfolio Yield", f"{(tot_div_eur/tot_val_eur)*100:.2f}%")
        
        st.divider()
        
        # --- VISUALS (COMPLEX) ---
        c_vis1, c_vis2 = st.columns(2)
        with c_vis1:
            st.subheader("Sectoren")
            if sectors:
                fig_pie = px.pie(names=list(sectors.keys()), values=list(sectors.values()), hole=0.4)
                st.plotly_chart(fig_pie, use_container_width=True)
        
        with c_vis2:
            st.subheader("Dividend Voorspelling")
            if p_data:
                df_div = pd.DataFrame(p_data)
                # Cleaning currency string to float for plotting if needed, but we have 'Div/Jaar (Est)' string
                # We need raw numbers for chart
                df_div['Div_Raw'] = df_div['Div/Jaar (Est)'].apply(lambda x: float(x.replace('€','').replace(',','')))
                fig_bar = px.bar(df_div, x='Ticker', y='Div_Raw', title="Dividend per positie (€)", color='Sector')
                st.plotly_chart(fig_bar, use_container_width=True)

        # --- DATA TABEL MET STYLING ---
        st.subheader("Posities")
        df = pd.DataFrame(p_data)
        
        def color_profit(val):
            color = 'rgba(144, 238, 144, 0.3)' if val >= 0 else 'rgba(255, 99, 71, 0.3)'
            return f'background-color: {color}'

        st.dataframe(
            df[['Ticker', 'Aantal', 'Prijs', 'GAK', 'Waarde', 'Winst Label', 'Div/Jaar (Est)', 'Sector']]
            .style.applymap(color_profit, subset=['Winst Label']), # Kleurt de winst kolom
            use_container_width=True, hide_index=True
        )

        # --- RENTE OP RENTE CALCULATOR ---
        st.markdown("---")
        with st.expander("🧮 Rente-op-Rente (Compound Interest) Calculator", expanded=True):
            [attachment_0](attachment)
            col_calc1, col_calc2 = st.columns([1, 2])
            
            with col_calc1:
                st.write("**Scenario Planning**")
                init_val = st.number_input("Startbedrag (Huidige Portfolio)", value=float(round(tot_val_eur, 2)))
                monthly_add = st.number_input("Maandelijkse Inleg (€)", value=500)
                years_grow = st.slider("Jaren", 5, 40, 20)
                expected_return = st.slider("Verwacht Rendement (%)", 1, 15, 7) / 100
                
            with col_calc2:
                # Berekening loop
                values = []
                invested = []
                curr = init_val
                tot_inv = init_val
                
                for m in range(years_grow * 12):
                    curr = curr * (1 + expected_return/12) + monthly_add
                    tot_inv += monthly_add
                    if (m+1) % 12 == 0:
                        values.append(curr)
                        invested.append(tot_inv)
                
                final_amount = curr
                interest_earned = final_amount - tot_inv
                
                m1, m2 = st.columns(2)
                m1.metric("Eindbedrag", f"€ {final_amount:,.0f}")
                m2.metric("Rente Ontvangen", f"€ {interest_earned:,.0f}", delta="Gratis Geld")
                
                # Grafiek
                df_calc = pd.DataFrame({
                    "Jaar": range(1, years_grow+1),
                    "Portfolio Waarde": values,
                    "Eigen Inleg": invested
                })
                
                fig_calc = px.line(df_calc, x="Jaar", y=["Portfolio Waarde", "Eigen Inleg"], 
                                   title="Vermogensgroei Projectie", markers=True)
                st.plotly_chart(fig_calc, use_container_width=True)

        if st.button("Portfolio Wissen"):
            st.session_state['portfolio'] = []
            st.rerun()
    else:
        st.info("Je portfolio is nog leeg.")

# ==================================================
# 3. WISHLIST
# ==================================================
with tabs[2]:
    if st.session_state['wishlist']:
        for w in st.session_state['wishlist']:
            c1, c2 = st.columns([4, 1])
            with c1: st.write(f"**{w['name']}** ({w['ticker']})")
            with c2: 
                if st.button("🔎", key=f"wl_{w['ticker']}"):
                    st.session_state['search_query'] = w['ticker']
                    st.rerun()
            st.divider()
    else: st.info("Leeg")

# ==================================================
# 4. BELASTING
# ==================================================
with tabs[3]:
    st.header("Box 3 Calculator")
    verm = st.number_input("Vermogen", value=50000)
    vrij = st.number_input("Vrijstelling", value=57000)
    if verm > vrij:
        st.write(f"Belastbaar: € {verm-vrij}")
        st.write(f"Geschatte belasting: € {(verm-vrij)*0.0604*0.36:.2f}")
    else:
        st.success("Geen belasting verschuldigd.")
