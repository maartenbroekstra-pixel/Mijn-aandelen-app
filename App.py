import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import json
import re
from datetime import datetime

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- API KEYS & CONSTANTEN ---
FMP_KEY = "nhXfJ0SDaXmUEVKy5icfcwzQOHqOn5QE"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

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
    "de": "Schuldratio (Debt/Equity).\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "div": "Dividend Rendement.\n🟢 > 3%: Interessant.",
    "margin": "Nettowinstmarge.\n🟢 > 20%: Hoge marge.",
    "eps": "Winst per aandeel.\n🟢 > 0: Winstgevend.",
}

# --- STATE MANAGEMENT ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []

# --- HULPFUNCTIES ---

@st.cache_data(ttl=600)
def get_finviz_data(ticker):
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
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD": return 1/rate 
            return rate
        return 1.0
    except: return 1.0

def search_yahoo_strict(query):
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
        import xml.etree.ElementTree as ET
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
            # FIX 2: Headers toegevoegd aan requests
            url_ratios = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{self.ticker}?apikey={FMP_KEY}"
            r = requests.get(url_ratios, headers=HEADERS, timeout=3).json()
            if isinstance(r, list) and r: self.fmp_ratios = r[0]

            url_quote = f"https://financialmodelingprep.com/api/v3/quote/{self.ticker}?apikey={FMP_KEY}"
            q = requests.get(url_quote, headers=HEADERS, timeout=3).json()
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
        
        # FIX 4: Betere percentage detectie
        if is_percent:
            # Als het getal heel klein is (bv 0.0077) -> x100 -> 0.77%
            if avg_val < 0.90: 
                avg_val *= 100
            # Als het getal normaal is (bv 5.5) -> blijft 5.5%
        
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
st.sidebar.header("📂 Data Beheer")

# FIX 5: Download & Upload functionaliteit hersteld
json_data = json.dumps({'p': st.session_state['portfolio'], 'w': st.session_state['wishlist']})
st.sidebar.download_button("⬇️ Download Backup", json_data, "aandelen_backup.json", "application/json")

uploaded_file = st.sidebar.file_uploader("⬆️ Upload Backup", type=['json'])
if uploaded_file:
    try:
        d = json.load(uploaded_file)
        # Zorg dat zowel portfolio als wishlist geladen worden
        if 'p' in d: st.session_state['portfolio'] = d['p']
        if 'w' in d: st.session_state['wishlist'] = d['w']
        st.toast("Succesvol geladen!", icon="✅")
    except Exception as e:
        st.error(f"Fout bij laden: {e}")

if st.sidebar.button("🧹 Cache Legen"): st.cache_data.clear(); st.rerun()

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

            # --- HEADER ---
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
                    st.markdown(f"<div style='text-align:center;color:{col};font-size:40px;font-weight:bold;border:3px solid {col};border-radius:50%;width:100px;height:100px;line-height:90px;margin:auto'>{score}</div>", unsafe_allow_html=True)
                with a2:
                    st.subheader("AI Oordeel")
                    st.write(summary)

            # --- METRICS ---
            st.subheader("📊 Fundamentals")
            def m_col(label, key, good_cond, bad_cond, fmt="{:.2f}", is_pct=False):
                val, src = d[key]
                if val is None: return st.metric(label, "N/A", help=f"Bron: {src}")
                
                delta_val = "✅" if good_cond(val) else "⚠️" if bad_cond(val) else "⚪"
                display = f"{val:.1f}%" if is_pct else fmt.format(val) # Toont nu netjes 0.7% als de waarde 0.7 is
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

            # --- CHART ---
            c_g, c_m = st.columns([2, 1])
            with c_g:
                hist = engine.yf_obj.history(period=per)
                if not hist.empty:
                    fig = px.area(hist, x=hist.index, y='Close', title=f"Koers ({per})")
                    fig.update_layout(yaxis_title=f"Prijs ({curr_sym})")
                    st.plotly_chart(fig, use_container_width=True)
            with c_m:
                st.markdown("##### 📰 Nieuws")
                for n in get_google_news(info.get('shortName', sel)):
                    st.markdown(f"🔹 [{n['title']}]({n['link']})\n*{n['date']}*")

            # --- TOEVOEGEN (FIX 3: GEEN CALCULATOR MEER) ---
            st.divider()
            with st.popover("➕ Toevoegen aan Portfolio", use_container_width=True):
                st.subheader("Positie Toevoegen")
                with st.form("add_pf"):
                    amount = st.number_input("Aantal stuks", min_value=0.0001, value=10.0, step=1.0)
                    price_input = st.number_input(f"Aankoopprijs ({curr_code})", value=float(cur_price) if cur_price else 0.0)
                    
                    if st.form_submit_button("Opslaan"):
                        st.session_state['portfolio'].append({
                            'ticker': sel, 
                            'amount': amount, 
                            'avg_price': price_input, 
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
# 2. PORTFOLIO PRO
# ==================================================
with tabs[1]:
    if st.session_state['portfolio']:
        st.markdown("### 🛠️ Portfolio Beheer")
        
        # EDIT MODE
        raw_df = pd.DataFrame(st.session_state['portfolio'])
        edit_df_view = raw_df[['ticker', 'amount', 'avg_price', 'currency']].rename(
            columns={'ticker': 'Aandeel', 'amount': 'Aantal', 'avg_price': 'GAK', 'currency': 'Valuta'}
        )
        
        edited_df = st.data_editor(
            edit_df_view, 
            column_config={
                "Aandeel": st.column_config.TextColumn(disabled=True),
                "Valuta": st.column_config.TextColumn(disabled=True),
                "Aantal": st.column_config.NumberColumn(min_value=0.0001, required=True),
                "GAK": st.column_config.NumberColumn(min_value=0.0, required=True, format="%.2f")
            },
            num_rows="dynamic",
            use_container_width=True,
            key="portfolio_editor"
        )
        
        updated_portfolio = []
        for idx, row in edited_df.iterrows():
            original_item = st.session_state['portfolio'][idx] if idx < len(st.session_state['portfolio']) else {}
            updated_portfolio.append({
                'ticker': row['Aandeel'],
                'amount': row['Aantal'],
                'avg_price': row['GAK'],
                'currency': row['Valuta'],
                'symbol': original_item.get('symbol', '€')
            })
        st.session_state['portfolio'] = updated_portfolio

        # LIVE CALCULATIES
        p_data = []
        tot_val_eur = 0
        tot_cost_eur = 0
        tot_div_eur = 0
        sectors = {}
        
        st.markdown("---")
        
        for i, item in enumerate(st.session_state['portfolio']):
            t = item['ticker']
            curr = item.get('currency', 'EUR')
            sym = item.get('symbol', '€')
            
            try:
                tk = yf.Ticker(t)
                live_price = tk.fast_info['last_price']
                inf = tk.info
                sec = inf.get('sector', 'Onbekend')
                
                div_amount = 0
                if 'dividendRate' in inf and inf['dividendRate'] is not None:
                    div_amount = inf['dividendRate']
                elif 'dividendYield' in inf and inf['dividendYield'] is not None:
                    div_amount = live_price * inf['dividendYield']
            except:
                live_price = item['avg_price']
                sec = "Onbekend"
                div_amount = 0
            
            val_native = live_price * item['amount']
            cost_native = item['avg_price'] * item['amount']
            prof_native = val_native - cost_native
            div_total_native = div_amount * item['amount']
            
            rate_to_eur = get_currency_rate(curr, "EUR")
            
            val_eur = val_native * rate_to_eur
            cost_eur = cost_native * rate_to_eur
            div_eur = div_total_native * rate_to_eur
            
            tot_val_eur += val_eur
            tot_cost_eur += cost_eur
            tot_div_eur += div_eur
            sectors[sec] = sectors.get(sec, 0) + val_eur

            p_data.append({
                "Ticker": t,
                "Aantal": item['amount'],
                "Prijs": live_price,
                "GAK": item['avg_price'],
                "Waarde": val_native,
                "Winst": prof_native * rate_to_eur, 
                "Div/Jaar": div_eur,
                "Sector": sec,
                "Valuta": sym
            })
        
        # DASHBOARD
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Totale Waarde", f"€ {tot_val_eur:,.2f}")
        prof_tot = tot_val_eur - tot_cost_eur
        prof_pct = (prof_tot / tot_cost_eur * 100) if tot_cost_eur else 0
        k2.metric("Totaal Winst/Verlies", f"€ {prof_tot:,.2f}", delta=f"{prof_pct:.1f}%")
        k3.metric("Geschat Dividend/Jaar", f"€ {tot_div_eur:,.2f}", delta="Passief Inkomen")
        yield_tot = (tot_div_eur / tot_val_eur * 100) if tot_val_eur else 0
        k4.metric("Portfolio Yield", f"{yield_tot:.2f}%")
        
        # VISUALS
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            st.subheader("Sectoren")
            if sectors:
                fig_pie = px.pie(names=list(sectors.keys()), values=list(sectors.values()), hole=0.4)
                st.plotly_chart(fig_pie, use_container_width=True)
        with col_v2:
            st.subheader("Dividend Projectie")
            if p_data:
                df_chart = pd.DataFrame(p_data)
                fig_bar = px.bar(df_chart, x='Ticker', y='Div/Jaar', color='Sector', title="Dividend per Aandeel (€)")
                st.plotly_chart(fig_bar, use_container_width=True)

        # TABEL - FIX 1: Gebruik column_config ipv style.applymap
        st.subheader("Detail Overzicht")
        df_display = pd.DataFrame(p_data)
        
        st.dataframe(
            df_display,
            column_order=("Ticker", "Aantal", "Prijs", "GAK", "Waarde", "Winst", "Div/Jaar", "Sector"),
            column_config={
                "Prijs": st.column_config.NumberColumn(format="%.2f"),
                "GAK": st.column_config.NumberColumn(format="%.2f"),
                "Waarde": st.column_config.NumberColumn(format="%.2f"),
                "Div/Jaar": st.column_config.NumberColumn(format="€ %.2f"),
                "Winst": st.column_config.NumberColumn(format="€ %.2f") # Kleuren doet Streamlit automatisch bij winst/verlies in NumberColumn niet zo fel, maar dit voorkomt de crash.
            },
            hide_index=True,
            use_container_width=True
        )

        # COMPOUND CALCULATOR
        st.markdown("---")
        with st.expander("🧮 Rente-op-Rente (Compound Interest) Calculator", expanded=True):
            [attachment_0](attachment)
            c_cal1, c_cal2 = st.columns([1, 2])
            with c_cal1:
                st.write("**Scenario Instellingen**")
                start_cap = st.number_input("Startkapitaal (Huidig Portfolio)", value=float(round(tot_val_eur, 2)))
                mnd_inleg = st.number_input("Maandelijkse Inleg (€)", value=500)
                jaren = st.slider("Aantal Jaren", 1, 40, 20)
                rendement = st.slider("Verwacht Rendement (%)", 1, 15, 7) / 100
            with c_cal2:
                dates = range(1, jaren + 1)
                vals = []
                inleg_lijst = []
                curr = start_cap
                total_inleg = start_cap
                for _ in dates:
                    for _ in range(12):
                        curr = curr * (1 + rendement/12) + mnd_inleg
                        total_inleg += mnd_inleg
                    vals.append(curr)
                    inleg_lijst.append(total_inleg)
                winst_rente = curr - total_inleg
                m1, m2 = st.columns(2)
                m1.metric(f"Waarde over {jaren} jaar", f"€ {curr:,.0f}")
                m2.metric("Rente Winst", f"€ {winst_rente:,.0f}", delta="Gratis Geld")
                df_calc = pd.DataFrame({"Jaar": dates, "Waarde": vals, "Eigen Inleg": inleg_lijst})
                fig_calc = px.line(df_calc, x="Jaar", y=["Waarde", "Eigen Inleg"], title="Vermogensgroei")
                st.plotly_chart(fig_calc, use_container_width=True)

        if st.button("Portfolio Wissen"):
            st.session_state['portfolio'] = []
            st.rerun()
    else:
        st.info("Portfolio is leeg. Voeg aandelen toe via de Analyse tab.")

# ==================================================
# 3. WISHLIST (FIX 6: COMPACTE LIJST)
# ==================================================
with tabs[2]:
    st.subheader("Mijn Wishlist")
    if st.session_state['wishlist']:
        # Simpele loop voor lijstweergave
        for i, w in enumerate(st.session_state['wishlist']):
            t = w['ticker']
            try:
                tk = yf.Ticker(t)
                fast = tk.fast_info
                price = fast['last_price']
                prev = fast['previous_close']
                pct = ((price - prev) / prev) * 100
                cur = tk.info.get('currencySymbol', '€')
                
                col_c = "green" if pct >= 0 else "red"
                
                # Compacte rij
                c1, c2, c3, c4 = st.columns([1.5, 3, 2, 1])
                c1.write(f"**{t}**")
                c2.write(w['name'])
                c3.markdown(f"{cur} {price:.2f} (<span style='color:{col_c}'>{pct:+.2f}%</span>)", unsafe_allow_html=True)
                if c4.button("🗑️", key=f"del_{i}"):
                    st.session_state['wishlist'].pop(i)
                    st.rerun()
            except:
                st.error(f"Fout bij laden {t}")
    else:
        st.info("Nog geen aandelen op je wishlist.")

# ==================================================
# 4. BELASTING (BOX 3)
# ==================================================
with tabs[3]:
    st.header("🧮 Werkelijk Rendement & Box 3 Belasting")
    col_in1, col_in2 = st.columns(2)
    with col_in1:
        st.subheader("1. Vermogen")
        start_vermogen = st.number_input("Vermogen 1 januari (€)", value=50000.0, step=1000.0)
        eind_vermogen = st.number_input("Vermogen 31 december (€)", value=55000.0, step=1000.0)
        netto_inleg = st.number_input("Netto Inleg dit jaar (€)", value=0.0)
        heffingsvrij = st.number_input("Heffingsvrij Vermogen (€)", value=57000.0)
    with col_in2:
        st.subheader("2. Tarieven")
        forfaitair_pct = st.number_input("Forfaitair Rendement (%)", value=6.04, step=0.01)
        belasting_pct = st.number_input("Belastingtarief Box 3 (%)", value=36.0, step=0.5)
    
    st.divider()
    
    werkelijk_rendement_euro = (eind_vermogen - start_vermogen) - netto_inleg
    werkelijk_rendement_pct = (werkelijk_rendement_euro / start_vermogen * 100) if start_vermogen > 0 else 0
    grondslag = max(0, start_vermogen - heffingsvrij)
    fictief_inkomen = grondslag * (forfaitair_pct / 100)
    te_betalen = fictief_inkomen * (belasting_pct / 100)
    
    c_res1, c_res2 = st.columns(2)
    with c_res1:
        with st.container(border=True):
            st.markdown("### 💰 Werkelijk Resultaat")
            if werkelijk_rendement_euro >= 0:
                st.metric("Winst in Euro's", f"€ {werkelijk_rendement_euro:,.2f}", delta="Winst")
                st.metric("Rendement %", f"{werkelijk_rendement_pct:.2f}%")
            else:
                st.metric("Verlies in Euro's", f"€ {werkelijk_rendement_euro:,.2f}", delta="Verlies", delta_color="inverse")
                st.metric("Rendement %", f"{werkelijk_rendement_pct:.2f}%")
    with c_res2:
        with st.container(border=True):
            st.markdown("### 🏛️ Belastingdienst (Box 3)")
            if grondslag > 0:
                st.write(f"Belastbare Grondslag: € {grondslag:,.2f}")
                st.write(f"Fictief Inkomen: € {fictief_inkomen:,.2f}")
                st.metric("Te Betalen Belasting", f"€ {te_betalen:,.2f}", delta="- Uitgaven", delta_color="inverse")
                netto_resultaat = werkelijk_rendement_euro - te_betalen
                st.divider()
                st.write(f"**Netto na belasting:** € {netto_resultaat:,.2f}")
            else:
                st.success("Je vermogen valt binnen het heffingsvrije vermogen. Je betaalt **€ 0,00** belasting.")
