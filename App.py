import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
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

# Mapping: EU Ticker -> US Ticker (voor metrics die FMP beter in US heeft)
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
    "de": "Debt/Equity Ratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "div": "Dividend Rendement.\n🟢 > 3%: Interessant.",
    "margin": "Nettowinstmarge.\n🟢 > 20%: Hoge marge.",
    "eps": "Winst per aandeel.\n🟢 > 0: Winstgevend.",
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
    """Haalt wisselkoers op (alleen gebruikt voor Portfolio Totaal berekening)"""
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD" and rate > 1.05: return 1/rate
            return rate
        return 1.0
    except: return 1.0

def search_yahoo_strict(query):
    """Zoekt aandelen en sorteert EU/DE bovenaan"""
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=DE&quotesCount=15"
        r = requests.get(url, headers=HEADERS, timeout=3)
        d = r.json()
        res = []
        raw_quotes = d.get('quotes', [])
        
        # Sorteer Logica: 
        # 1. Amsterdam (.AS)
        # 2. Duitsland (.DE, .F, .XETRA)
        # 3. Andere EU (.PA, .BR, .MC, .MI)
        # 4. US (geen punt of lengte < 5 vaak)
        def sort_key(q):
            s = q.get('symbol', '')
            if s.endswith('.AS'): return 0
            if s.endswith('.DE') or s.endswith('.F'): return 1
            if any(s.endswith(x) for x in ['.PA', '.BR', '.MI', '.MC', '.LS']): return 2
            if '.' not in s: return 10 # US stock
            return 20 # Overig

        # Sorteren
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
        self.ticker = ticker  # US Ticker (voor FMP/Finviz)
        self.original_ticker = original_ticker # Originele (Yahoo)
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
        # Alleen Finviz gebruiken als de input een US ticker lijkt (geen punt)
        if "." not in self.ticker:
            self.finviz_data = get_finviz_data(self.ticker)

    def get_combined_metric(self, key_yf, key_fmp_ratio, key_fmp_quote=None, key_finviz=None, is_percent=False):
        values = []
        sources = []

        # Yahoo
        v_yf = self.yf_info.get(key_yf)
        if v_yf is not None: values.append(float(v_yf)); sources.append("Yahoo")

        # FMP
        v_fmp = self.fmp_ratios.get(key_fmp_ratio)
        if v_fmp is None and key_fmp_quote: v_fmp = self.fmp_quote.get(key_fmp_quote)
        if v_fmp is not None: values.append(float(v_fmp)); sources.append("FMP")

        # Finviz
        if key_finviz:
            v_fin = self.finviz_data.get(key_finviz)
            if v_fin is not None: values.append(float(v_fin)); sources.append("Finviz")

        if not values: return None, "N/A"

        # Bereken gemiddelde
        avg_val = sum(values) / len(values)
        
        # Percentage Logica
        if is_percent:
            if avg_val < 0.90: avg_val *= 100
        
        src_str = " & ".join(sources)
        return avg_val, src_str

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
st.sidebar.download_button("⬇️ Download JSON", json.dumps({'p': st.session_state['portfolio'], 'w': st.session_state['wishlist']}), "data.json")
uploaded_file = st.sidebar.file_uploader("⬆️ Upload JSON", type=['json'])
if uploaded_file:
    d = json.load(uploaded_file)
    st.session_state['portfolio'] = d.get('p', [])
    st.session_state['wishlist'] = d.get('w', [])
    st.toast("Geladen!")
if st.sidebar.button("🧹 Reset Cache"): st.cache_data.clear(); st.rerun()

tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist", "🇳🇱 Belasting"])

# ==================================================
# 1. ANALYSE
# ==================================================
with tabs[0]:
    c_search, c_per = st.columns([3, 1])
    with c_search:
        dq = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek aandeel (Bijv: shell, asml, tsla):", value=dq, key="search_main")
        sel = None
        if txt:
            if "." in txt and " " not in txt: sel = txt
            else:
                res = search_yahoo_strict(txt)
                if res: sel = st.selectbox("Resultaten (gesorteerd op EU):", res).split(" | ")[0]
                else: sel = txt.upper()
    with c_per:
        per = st.selectbox("Periode", ["1mo", "6mo", "1y", "5y", "max"], index=2)

    if sel:
        try:
            # Mapping voor Metrics (gebruik US ticker voor FMP indien mogelijk)
            us_ticker = EU_TO_US_MAP.get(sel, sel.split('.')[0] if '.' in sel else sel)
            
            engine = DataEngine(us_ticker, sel)
            info = engine.yf_info
            
            # Data verzamelen
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
            
            # --- GEEN CONVERSIE MEER ---
            # We halen het symbool op en tonen de ruwe prijs
            curr_sym = info.get('currencySymbol', info.get('currency', ''))
            cur_price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
            
            # Target
            tgt_val = info.get('targetMeanPrice')
            tgt_src = "Yahoo"
            if not tgt_val:
                tgt_val = engine.finviz_data.get('target')
                tgt_src = "Finviz"
            
            score, summary = calculate_ai_score(d)

            # --- HEADER ---
            with st.container(border=True):
                h1, h2, h3 = st.columns([2, 1, 1])
                with h1:
                    st.title(info.get('shortName', sel))
                    st.caption(f"Ticker: {sel} | Valuta: {info.get('currency', '?')}")
                with h2:
                    st.metric("Huidige Koers", f"{curr_sym} {cur_price:.2f}" if cur_price else "-")
                with h3:
                    if tgt_val and cur_price:
                        delta = (tgt_val - cur_price) / cur_price
                        st.metric("Koersdoel", f"{curr_sym} {tgt_val:.2f}", f"{delta:.1%}", help=f"Bron: {tgt_src}")
                    else:
                        st.metric("Koersdoel", "N/A")

                st.divider()
                
                a1, a2 = st.columns([1, 4])
                with a1:
                    col = "green" if score >= 7 else "orange" if score >= 5 else "red"
                    st.markdown(f"""
                    <div style="text-align:center; border: 4px solid {col}; border-radius: 50%; width: 90px; height: 90px; line-height: 80px; margin:auto;">
                        <span style="font-size: 32px; font-weight: bold; color: {col};">{score}</span>
                    </div>
                    """, unsafe_allow_html=True)
                with a2:
                    st.subheader("AI Oordeel")
                    st.write(summary)
            
            # --- METRICS ---
            st.subheader("📊 Fundamentals")
            
            def m_col(label, key, good_cond, bad_cond, fmt="{:.2f}", is_pct=False):
                val, src = d[key]
                if val is None:
                    st.metric(label, "N/A", help=f"Geen data.\nBron: {src}")
                    return
                
                display_val = fmt.format(val)
                if is_pct: display_val = f"{val:.1f}%"
                
                delta_val = None
                delta_color = "off"
                
                if good_cond(val):
                    delta_val = "✅ Goed"
                    delta_color = "normal"
                elif bad_cond(val):
                    delta_val = "⚠️ Let op"
                    delta_color = "inverse"
                else:
                    delta_val = "⚪ Neutraal"
                    delta_color = "off"
                
                st.metric(label, display_val, delta=delta_val, delta_color=delta_color, help=f"Waarde: {val}\nBron: {src}\n{TOOLTIPS.get(key,'')}")

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

            # --- GRAFIEK ---
            c_g, c_m = st.columns([2, 1])
            with c_g:
                hist = engine.yf_obj.history(period=per)
                if not hist.empty:
                    fig = px.area(hist, x=hist.index, y='Close', title=f"Koersverloop ({per})")
                    fig.update_layout(yaxis_title=f"Prijs ({curr_sym})", showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
            
            with c_m:
                st.markdown("##### 📰 Nieuws")
                for n in get_google_news(info.get('shortName', sel)):
                    st.markdown(f"[{n['title']}]({n['link']})\n*{n['date']}*")
                
                st.markdown("##### 📺 Video")
                vid = get_youtube_video_id(sel)
                if vid: st.video(f"https://www.youtube.com/watch?v={vid}")

            # --- ACTIONS ---
            st.divider()
            ac1, ac2 = st.columns(2)
            with ac1:
                with st.popover("➕ Toevoegen aan Portfolio", use_container_width=True):
                    with st.form("add_pf"):
                        st.markdown(f"**{sel} toevoegen**")
                        amount = st.number_input("Aantal stuks", min_value=1.0, value=10.0, step=1.0)
                        # We slaan de valuta ook op in de portfolio
                        price_in = st.number_input(f"Aankoopprijs ({curr_sym})", value=float(cur_price) if cur_price else 0.0)
                        if st.form_submit_button("Opslaan"):
                            st.session_state['portfolio'].append({
                                'ticker': sel, 
                                'amount': amount, 
                                'avg_price': price_in,
                                'currency': info.get('currency', 'EUR'),
                                'symbol': curr_sym
                            })
                            st.success("Toegevoegd!")
                            st.rerun()
            with ac2:
                if st.button("❤️ Zet op Wishlist", use_container_width=True):
                    st.session_state['wishlist'].append({'ticker': sel, 'name': info.get('shortName', sel)})
                    st.toast("Toegevoegd aan Wishlist!")

        except Exception as e:
            st.error(f"Fout: {e}")

# ==================================================
# 2. PORTFOLIO
# ==================================================
with tabs[1]:
    if st.session_state['portfolio']:
        p_data = []
        tot_val_eur = 0
        tot_cost_eur = 0
        
        for item in st.session_state['portfolio']:
            t = item['ticker']
            curr = item.get('currency', 'EUR')
            sym = item.get('symbol', '€')
            
            # Live koers ophalen
            try:
                # fast_info is snel en geeft ruwe prijs in aandeel valuta
                live_price = yf.Ticker(t).fast_info['last_price']
            except: live_price = item['avg_price']
            
            val_raw = live_price * item['amount']
            cost_raw = item['avg_price'] * item['amount']
            
            # Voor de totaalberekening bovenaan MOETEN we omrekenen naar EUR
            rate_to_eur = get_currency_rate(curr, "EUR")
            tot_val_eur += val_raw * rate_to_eur
            tot_cost_eur += cost_raw * rate_to_eur
            
            p_data.append({
                "Aandeel": t,
                "Valuta": curr,
                "Aantal": item['amount'],
                "GAK": f"{sym} {item['avg_price']:.2f}",
                "Huidig": f"{sym} {live_price:.2f}",
                "Waarde": f"{sym} {val_raw:.2f}",
                "Winst": f"{sym} {val_raw - cost_raw:.2f}"
            })
            
        df = pd.DataFrame(p_data)
        
        # Totaalblok (altijd in Euro's voor overzicht)
        st.caption("Totaal portfolio wordt omgerekend naar EUR voor het totaaloverzicht.")
        k1, k2, k3 = st.columns(3)
        k1.metric("Totale Waarde (EUR)", f"€ {tot_val_eur:,.2f}")
        diff = tot_val_eur - tot_cost_eur
        k2.metric("Totaal Winst (EUR)", f"€ {diff:,.2f}", delta=f"{(diff/tot_cost_eur)*100:.1f}%" if tot_cost_eur else "0%")
        
        st.dataframe(df, use_container_width=True, hide_index=True)
        
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
