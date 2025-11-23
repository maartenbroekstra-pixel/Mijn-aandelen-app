import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import requests
import json
import re
from datetime import datetime

# ==========================================
# 🔑 JOUW FMP KEY (Reeds ingevuld)
# ==========================================
JOUW_FMP_KEY = "nhXfJ0SDaXmUEVKy5icfcwzQOHqOn5QE"
# ==========================================

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- STATE ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []
if 'search_query' not in st.session_state: st.session_state['search_query'] = ""

# --- CONSTANTEN ---
HEADERS = {'User-Agent': 'Mozilla/5.0'}

EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD", "RDSA.AS": "SHEL",
    "SHELL.AS": "SHEL", "WKL.AS": "WTKWY", "ASM.AS": "ASMIY", "ADYEN.AS": "ADYEY"
}

TAX_DEFAULTS = {
    2023: {"invest": 6.17, "tax": 32},
    2024: {"invest": 6.04, "tax": 36},
    2025: {"invest": 5.88, "tax": 36}
}

TOOLTIPS = {
    "peg": "PEG Ratio.\n🟢 < 1.5: Goedkoop t.o.v. groei.\n🟠 > 2.5: Duur.",
    "fwd_pe": "Forward P/E.\n🟢 < 20: Aantrekkelijk.\n🔴 > 35: Premie waardering.",
    "roe": "Return on Equity.\n🟢 > 15%: Sterk management.",
    "de": "Debt/Equity.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio.\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Price/Book.\n🟢 < 3.0: Redelijk.",
    "div_yield": "Dividend.\n🟢 > 2.5%: Goed inkomen.",
    "profit_margin": "Winstmarge.\n🟢 > 15%: Sterk bedrijf.",
    "eps": "Earnings Per Share (Winst per aandeel).\n🟢 > 0: Winstgevend.\n🔴 < 0: Verlieslatend."
}

# --- HULPFUNCTIES ---
def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=25):
    if text and len(text) > max_len: return text[:max_len] + ".."
    return text

def smart_normalize(val):
    if val is None: return None
    try:
        f = float(val)
        # Algemene check voor percentages die als geheel getal komen (bijv 15.5 ipv 0.155)
        if f > 0.8: return f / 100.0 
        return f
    except: return None

def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty: return data['Close'].iloc[-1]
        return 1.0
    except: return 1.0

# --- FMP API ---
@st.cache_data(ttl=3600)
def get_fmp_data(ticker, api_key):
    clean_key = api_key.strip() 
    if not clean_key: return {}, "Geen Key"
    
    data = {}
    try:
        # Endpoint 1: Ratios (ANNUAL)
        url = f"https://financialmodelingprep.com/api/v3/ratios/{ticker}?period=annual&limit=1&apikey={clean_key}"
        r = requests.get(url, timeout=5)
        
        if r.status_code == 200:
            js = r.json()
            if isinstance(js, list) and len(js) > 0:
                d = js[0]
                data['peg'] = d.get('pegRatio')
                data['pe'] = d.get('priceEarningsRatio')
                data['pb'] = d.get('priceToBookRatio')
                data['roe'] = smart_normalize(d.get('returnOnEquity'))
                data['de'] = d.get('debtEquityRatio')
                data['cr'] = d.get('currentRatio')
                data['profit_margin'] = smart_normalize(d.get('netProfitMargin'))
                data['div_yield'] = smart_normalize(d.get('dividendYield'))
            else:
                # Fallback TTM
                url_ttm = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{ticker}?apikey={clean_key}"
                r_ttm = requests.get(url_ttm, timeout=3)
                if r_ttm.status_code == 200:
                    js_ttm = r_ttm.json()
                    if isinstance(js_ttm, list) and len(js_ttm) > 0:
                         d = js_ttm[0]
                         data['peg'] = d.get('pegRatioTTM')
                         data['pe'] = d.get('priceEarningsRatioTTM')
                
                if not data: return {}, f"FMP: Geen data voor {ticker}"

        elif r.status_code == 403:
            return {}, "FMP: Invalid Key of Limiet bereikt"
        
        # 2. Quote Endpoint (EPS)
        url2 = f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={clean_key}"
        r2 = requests.get(url2, timeout=5)
        if r2.status_code == 200:
            js2 = r2.json()
            if isinstance(js2, list) and len(js2) > 0:
                d2 = js2[0]
                data['eps'] = d2.get('eps')
                if not data.get('pe'): data['pe'] = d2.get('pe')
                
    except Exception as e:
        return {}, f"Fout: {str(e)}"
    
    return data, "OK"

# --- COMBINEREN ---
def get_combined_data(ticker, us_ticker, yf_info, fmp_key):
    fmp_target = us_ticker if us_ticker else ticker
    fmp_data, fmp_status = get_fmp_data(fmp_target, fmp_key)
    
    yahoo_data = {
        'peg': safe_get(yf_info, 'pegRatio'),
        'fwd_pe': safe_get(yf_info, 'forwardPE'),
        'pe': safe_get(yf_info, 'trailingPE'),
        'pb': safe_get(yf_info, 'priceToBook'),
        'roe': smart_normalize(safe_get(yf_info, 'returnOnEquity')),
        'de': safe_get(yf_info, 'debtToEquity'),
        'cr': safe_get(yf_info, 'currentRatio'),
        'profit_margin': smart_normalize(safe_get(yf_info, 'profitMargins')),
        'div_yield': smart_normalize(safe_get(yf_info, 'dividendYield')),
        'target': safe_get(yf_info, 'targetMeanPrice'),
        'eps': safe_get(yf_info, 'trailingEps')
    }
    
    if yahoo_data['de'] and yahoo_data['de'] > 10: yahoo_data['de'] /= 100

    keys = ['peg', 'fwd_pe', 'pb', 'roe', 'de', 'cr', 'profit_margin', 'div_yield', 'target', 'eps']
    final = {}
    sources = {}
    debug = {k: {} for k in keys}
    debug["_FMP_STATUS"] = fmp_status

    for k in keys:
        vals = []
        src_names = []

        # Yahoo
        y_val = yahoo_data.get(k)
        if k == 'fwd_pe' and y_val is None: y_val = yahoo_data.get('pe')
        if y_val is not None:
            vals.append(y_val)
            src_names.append("Yahoo")
            debug[k]["Yahoo"] = y_val

        # FMP
        f_val = fmp_data.get(k)
        if k == 'fwd_pe' and f_val is None: f_val = fmp_data.get('pe')
        if f_val is not None:
            vals.append(f_val)
            src_names.append("FMP")
            debug[k]["FMP"] = f_val

        if vals:
            final[k] = sum(vals) / len(vals)
            sources[k] = " + ".join(src_names)
        else:
            final[k] = None
            sources[k] = "Geen data"

    return final, sources, debug

# --- SEARCH ---
def search_yahoo_strict(query):
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10"
        r = requests.get(url, headers=HEADERS, timeout=3)
        return [f"{q['symbol']} | {q.get('shortname','')}" for q in r.json().get('quotes', [])]
    except: return []

# --- AI CHECK ---
def ai_check(metrics):
    score, count = 0, 0
    pos, neg = [], []
    
    checks = [
        ('peg', 0, 1.5, 2.5, "PEG uitstekend", "PEG te hoog"),
        ('fwd_pe', 0, 20, 35, "K/W gunstig", "K/W hoog"),
        ('roe', 0.15, 999, 0.05, "Management top (ROE)", "Zwak rendement (ROE)"),
        ('de', 0, 1.0, 2.0, "Gezonde balans", "Hoge schulden"),
        ('profit_margin', 0.15, 999, 0.0, "Sterke winstmarge", "Dunne marges"),
        ('cr', 1.5, 999, 1.0, "Goede liquiditeit", "Krap bij kas"),
        ('eps', 0.01, 999, -0.01, "Winstgevend (EPS)", "Verlieslatend (EPS)")
    ]
    
    for k, g_min, g_max, b_thresh, t_good, t_bad in checks:
        val = metrics.get(k)
        if val is not None:
            count += 1
            if k in ['roe', 'profit_margin', 'eps', 'div_yield']:
                if val >= g_min: score+=1; pos.append(t_good)
                elif val <= b_thresh: neg.append(t_bad)
                else: score+=0.5
            else:
                if g_min < val <= g_max: score+=1; pos.append(t_good)
                elif val > b_thresh: neg.append(t_bad)
                else: score+=0.5

    final = (score / count * 10) if count > 0 else 5.0
    
    eps = metrics.get('eps')
    if eps is not None and eps < 0:
        final -= 3.0
        final = max(1.0, final)
        if "Verlieslatend (EPS)" not in neg: neg.insert(0, "⚠️ Bedrijf maakt verlies!")
    
    return round(final, 1), pos, neg

# --- FILE MANAGER ---
def handle_upload():
    f = st.session_state.get('uploader')
    if f:
        try: d = json.load(f); st.session_state['portfolio'] = d.get('portfolio', []); st.session_state['wishlist'] = d.get('wishlist', [])
        except: st.error("Fout bestand.")
def get_json(): return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- UI START ---
st.sidebar.header("📂 Menu")

# Key Check UI
if JOUW_FMP_KEY:
    st.sidebar.success(f"✅ Key actief: {JOUW_FMP_KEY[:4]}...")
else:
    st.sidebar.error("❌ Geen Key ingevuld")

st.sidebar.download_button("⬇️ Backup", get_json(), "data.json", "application/json")
st.sidebar.file_uploader("⬆️ Restore", type=['json'], key='uploader', on_change=handle_upload)
if st.sidebar.button("🧹 Reset"): st.cache_data.clear(); st.rerun()

tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist", "🇳🇱 Belasting"])

# 1. ANALYSE
with tabs[0]:
    c1, c2 = st.columns([3, 1])
    dq = st.session_state.get('search_query', "")
    txt = c1.text_input("Zoek aandeel:", value=dq, placeholder="Bijv: ASML, AAPL", key="s")
    if txt:
        try:
            sel = txt
            if "." not in txt:
                res = search_yahoo_strict(txt)
                sel = st.selectbox("Resultaat:", res).split(" | ")[0] if res else txt.upper()
            
            st.markdown("---")
            tick = yf.Ticker(sel)
            info = tick.info
            
            # --- AANPASSING 1: 5 JAAR GRAFIEK ---
            hist = tick.history(period="5y")
            
            if hist.empty: st.error("Geen koersdata.")
            else:
                rate = get_currency_rate(safe_get(info, 'currency', 'EUR'), "EUR")
                price = hist['Close'].iloc[-1] * rate
                
                us_mt = sel
                if sel in EU_TO_US_MAP: us_mt = EU_TO_US_MAP[sel]
                elif "." in sel:
                    sn = safe_get(info, 'shortName', '').split(' ')[0]
                    found = search_yahoo_strict(sn)
                    if found and "." not in found[0].split(" | ")[0]: us_mt = found[0].split(" | ")[0]

                with st.spinner("Data ophalen..."):
                    data, srcs, debug = get_combined_data(sel, us_mt if us_mt != sel else None, info, JOUW_FMP_KEY)
                
                score, plus, minn = ai_check(data)

                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                c1.title(truncate_text(safe_get(info, 'shortName', sel)))
                c1.caption(f"{sel} | US Data: {us_mt}")
                c2.metric("Prijs", f"€ {price:.2f}")
                
                tgt = data.get('target')
                if tgt:
                    tgt_e = tgt * get_currency_rate("USD", "EUR")
                    ups = ((tgt_e - price)/price)*100
                    c3.metric("Doel", f"€ {tgt_e:.2f}", f"{ups:+.1f}%", help=srcs.get('target'))
                else: c3.metric("Doel", "-")

                with c4.popover("➕ Actie"):
                    with st.form("add"):
                        n = st.number_input("Aantal", 1.0)
                        p = st.number_input("GAK", value=float(round(price, 2)))
                        if st.form_submit_button("Portfolio"):
                            st.session_state['portfolio'].append({'ticker': sel, 'amount': n, 'avg_price_eur': p})
                            st.rerun()
                    if st.button("Wishlist"):
                        st.session_state['wishlist'].append({'ticker': sel, 'name': safe_get(info, 'shortName')})
                        st.rerun()

                col_s = "#4CAF50" if score > 7 else "#FF9800" if score >= 5.5 else "#F44336"
                st.markdown(f"""
                <div style="border:1px solid #444; padding:15px; border-radius:10px; margin:20px 0;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <h2 style="color:{col_s}; margin:0">Score: {score}/10</h2>
                        <div style="font-size:30px">{"🚀" if score>7 else "⚖️" if score>5.5 else "🐻"}</div>
                    </div>
                    <div style="margin-top:10px; font-size:0.9em;">
                        <b>✅ Plus:</b> {', '.join(plus) if plus else '-'}<br>
                        <b>⚠️ Min:</b> {', '.join(minn) if minn else '-'}
                    </div>
                </div>""", unsafe_allow_html=True)

                def get_c(k, v):
                    if v is None: return ""
                    if k=='peg': return "🟢" if v<1.5 else "🟠"
                    if k=='fwd_pe': return "🟢" if v<20 else "🔴" if v>35 else "🟠"
                    if k in ['roe', 'profit_margin', 'div_yield']: return "🟢" if v>0.15 else "⚪"
                    if k=='eps': return "🟢" if v>0 else "🔴"
                    if k=='cr': return "🟢" if v>1.5 else "🔴"
                    return "⚪"

                cols = st.columns(5)
                ks = ['peg', 'fwd_pe', 'roe', 'div_yield', 'eps', 'cr', 'de', 'pb', 'profit_margin', 'target']
                ls = ['PEG', 'P/E', 'ROE', 'Yield', 'EPS', 'Current', 'Debt/Eq', 'P/B', 'Marge', 'Target']
                
                for i, (k, l) in enumerate(zip(ks, ls)):
                    v = data.get(k)
                    
                    # --- AANPASSING 2: YIELD DISPLAY FIX ---
                    # Als yield > 0.5 is (dus 50%), is het waarschijnlijk ruwe data die nog door 100 moet.
                    if k == 'div_yield' and v is not None and v > 0.5:
                        v = v / 100.0

                    clr = get_c(k, v)
                    disp = "-"
                    if v is not None:
                        if k in ['roe', 'div_yield', 'profit_margin']: disp = f"{v*100:.2f}%"
                        else: disp = f"{v:.2f}"
                    
                    if k != 'target':
                        with cols[i%5]:
                            st.metric(l, f"{clr} {disp}", help=f"{TOOLTIPS.get(k)}\nBron: {srcs.get(k)}")

                with st.expander("🛠️ Debug (Yahoo vs FMP)"):
                    st.json(debug)

                st.subheader(f"Koersverloop (5 Jaar)")
                st.plotly_chart(px.line(hist, y='Close'))

                # --- AANPASSING 3: NIEUWS & YOUTUBE ---
                st.markdown("---")
                col_news, col_video = st.columns(2)
                
                with col_news:
                    st.subheader("Laatste Nieuws")
                    # Link naar Google News search
                    news_url = f"https://news.google.com/search?q={sel}+stock"
                    st.markdown(f"📰 [Klik hier voor nieuws over **{sel}** op Google News]({news_url})")
                    
                    # Optioneel ook Yahoo Finance news link
                    yf_news = f"https://finance.yahoo.com/quote/{sel}/news"
                    st.markdown(f"📈 [Yahoo Finance nieuws over **{sel}**]({yf_news})")

                with col_video:
                    st.subheader("Video Analyse")
                    # Link naar YouTube search
                    yt_url = f"https://www.youtube.com/results?search_query={sel}+stock+analysis"
                    st.markdown(f"📺 [Zoek video analyses over **{sel}** op YouTube]({yt_url})")
                    
                    # Embedded video speler (zoekt automatisch de ticker)
                    st.video(f"https://www.youtube.com/embed?listType=search&list={sel}+stock+analysis")

        except Exception as e: st.error(f"Fout: {e}")

# 2. PORTFOLIO
with tabs[1]:
    if st.session_state['portfolio']:
        rows, tv, ti, sd, dc = [], 0, 0, {}, []
        for i, item in enumerate(st.session_state['portfolio']):
            try:
                t = item['ticker']
                tk = yf.Ticker(t)
                h = tk.history(period="1d")
                rate = get_currency_rate(tk.info.get('currency','EUR'), "EUR")
                curr = h['Close'].iloc[-1] * rate if not h.empty else item['avg_price_eur']
                v = curr * item['amount']; c = item['avg_price_eur'] * item['amount']
                tv += v; ti += c
                sd[tk.info.get('sector','Overig')] = sd.get(tk.info.get('sector','Overig'), 0) + v
                dr = tk.info.get('dividendRate')
                if dr: dc.append({"Aandeel": t, "Bedrag": dr * rate * item['amount']})
                rows.append({"Naam": t, "Aantal": item['amount'], "Prijs": f"€{curr:.2f}", "Waarde": f"€{v:.2f}", "Winst": f"€{v-c:.2f}"})
            except: pass
        c1, c2 = st.columns(2)
        c1.metric("Waarde", f"€ {tv:,.2f}"); c2.metric("Winst", f"€ {tv-ti:,.2f}", f"{((tv-ti)/ti)*100:.1f}%" if ti else "0%")
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        if sd: st.plotly_chart(px.pie(values=list(sd.values()), names=list(sd.keys()), hole=0.4))
        if st.button("Leegmaken"): st.session_state['portfolio'] = []; st.rerun()
    else: st.info("Leeg")

# 3. WISHLIST
with tabs[2]:
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            t = item['ticker']
            tk = yf.Ticker(t)
            h = tk.history(period="2d")
            if not h.empty:
                rate = get_currency_rate(tk.info.get('currency','EUR'), "EUR")
                cp = h['Close'].iloc[-1] * rate
                ch = ((cp - h['Close'].iloc[0]*rate)/(h['Close'].iloc[0]*rate))*100
                c1, c2 = st.columns([3,1])
                c1.markdown(f"**{item['name']}** ({t})")
                c2.markdown(f"€{cp:.2f} <span style='color:{'green' if ch>=0 else 'red'}'>({ch:+.1f}%)</span>", unsafe_allow_html=True)
                st.divider()
        if st.button("Leegmaken", key="wl"): st.session_state['wishlist'] = []; st.rerun()
    else: st.info("Leeg")

# 4. BELASTING
with tabs[3]:
    st.header("Box 3")
    c1, c2 = st.columns(2)
    yr = c1.selectbox("Jaar", [2023, 2024, 2025])
    
    defaults = TAX_DEFAULTS.get(yr, {"invest": 6.04, "tax": 36})
    
    fr = c1.number_input("Fictief %", value=defaults["invest"])
    tr = c1.number_input("Tax %", value=float(defaults["tax"]))
    
    sv = c2.number_input("Start (1 jan)", 0.0)
    ev = c2.number_input("Eind (31 dec)", 0.0)
    inl = c2.number_input("Netto Inleg", 0.0)
    
    rend = (sv * fr / 100)
    tax = rend * tr / 100
    st.metric("Belasting", f"€ {tax:.2f}")
