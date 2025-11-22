import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import requests
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
import numpy as np

# --- CONFIGURATIE ---
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- STATE MANAGEMENT ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'wishlist' not in st.session_state: st.session_state['wishlist'] = []

# --- CONSTANTEN ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Mapping voor rijkere data (EU ticker -> US ticker voor metrics)
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD"
}

TAX_DEFAULTS = {
    2023: {"invest": 6.17, "tax": 32},
    2024: {"invest": 6.04, "tax": 36},
    2025: {"invest": 5.88, "tax": 36}
}

TOOLTIPS = {
    "peg": "Prijs/Winst gedeeld door Groei (5jr).\n🟢 < 1.5: Goedkoop.\n🔴 Negatief: Krimp.\n🟠 > 2.5: Duur.",
    "pe": "Huidige Koers/Winst verhouding.",
    "fwd_pe": "Verwachte K/W komend jaar.\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Premie.",
    "roe": "Rendement op Eigen Vermogen.\n🟢 > 15%: Sterk management.",
    "de": "Schuldratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3.0: Redelijk.",
    "div": "Dividend Rendement.\n🟢 > 3%: Hoog inkomen.",
    "margin": "Winstmarge.\n🟢 > 20%: Zeer winstgevend."
}

# --- HULPFUNCTIES ---

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=22):
    if text and len(text) > max_len: return text[:max_len] + ".."
    return text

@st.cache_data(ttl=300)
def get_currency_rate(from_curr, to_curr="EUR"):
    if from_curr == to_curr or not from_curr: return 1.0
    try:
        ticker = "EUR=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            rate = data['Close'].iloc[-1]
            if from_curr == "USD" and rate > 1.05: return 1/rate
            return rate
        return 0.95 if from_curr == "USD" else 1.0
    except: return 1.0

def clean_float(val_str):
    """Zet string met % of letters om naar float"""
    if not val_str: return None
    try:
        clean = re.sub(r'[^\d\.-]', '', val_str)
        if not clean: return None
        val = float(clean)
        if '%' in val_str: val /= 100
        # Check op T/B/M suffixes (Trillion/Billion/Million) - meestal niet nodig voor ratios, wel voor market cap
        return val
    except: return None

# --- SCRAPERS ---

@st.cache_data(ttl=600)
def scrape_yahoo_live(ticker):
    """Yahoo Finance Scraper"""
    data = {}
    try:
        # Key Statistics
        url_st = f"https://finance.yahoo.com/quote/{ticker}/key-statistics"
        r = requests.get(url_st, headers=HEADERS, timeout=4)
        if r.status_code == 200:
            txt = r.text
            def get_stat(label):
                # Zoekt naar label, skip HTML tags tot de waarde
                m = re.search(rf'{label}.*?<td[^>]*>(.*?)</td>', txt, re.DOTALL)
                if m:
                    return clean_float(re.sub(r'<.*?>', '', m.group(1)))
                return None

            data['peg'] = get_stat(r'PEG Ratio \(5 yr expected\)')
            data['fwd_pe'] = get_stat(r'Forward P/E')
            data['pe'] = get_stat(r'Trailing P/E')
            data['pb'] = get_stat(r'Price/Book')
            data['profit_margin'] = get_stat(r'Profit Margin')
            data['roe'] = get_stat(r'Return on Equity')
            data['de'] = get_stat(r'Total Debt/Equity')
            data['cr'] = get_stat(r'Current Ratio')
            
            # Dividend (staat soms elders, maar checken stats)
            div_m = re.search(r'Forward Annual Dividend Yield.*?<td[^>]*>(.*?)</td>', txt, re.DOTALL)
            if div_m: data['div_yield'] = clean_float(re.sub(r'<.*?>', '', div_m.group(1)))

        # Analysis (Targets)
        url_an = f"https://finance.yahoo.com/quote/{ticker}/analysis"
        r_an = requests.get(url_an, headers=HEADERS, timeout=4)
        if r_an.status_code == 200:
            m_tgt = re.search(r'Average Target.*?<td[^>]*>(.*?)</td>', r_an.text, re.DOTALL)
            if m_tgt: data['target'] = clean_float(re.sub(r'<.*?>', '', m_tgt.group(1)))
            
    except Exception: pass
    return data

@st.cache_data(ttl=600)
def scrape_finviz(ticker):
    """Finviz Scraper"""
    d = {}
    try:
        r = requests.get(f"https://finviz.com/quote.ashx?t={ticker}", headers=HEADERS, timeout=4)
        if r.status_code == 200:
            def p(label):
                m = re.search(rf'>{label}</td>.*?<b>\s*([\d\.-]+%?)\s*</b>', r.text, re.DOTALL)
                return clean_float(m.group(1)) if m and m.group(1)!='-' else None
            
            d['peg'] = p('PEG')
            d['fwd_pe'] = p('Forward P/E')
            d['pe'] = p('P/E')
            d['pb'] = p('P/B')
            d['roe'] = p('ROE')
            d['de'] = p('Debt/Eq')
            d['cr'] = p('Current Ratio')
            d['profit_margin'] = p('Profit Margin')
            d['div_yield'] = p('Dividend %')
            d['target'] = p('Target Price')
            d['eps'] = p('EPS \(ttm\)')
    except Exception: pass
    return d

@st.cache_data(ttl=600)
def scrape_google_finance(ticker, exchange_hint=None):
    """Google Finance Scraper"""
    data = {}
    # Google Finance werkt het best met TICKER:EXCHANGE (bv. AAPL:NASDAQ of ASML:AMS)
    # Simpele heuristiek voor ticker formaat
    search_ticker = ticker
    if "." not in ticker and not exchange_hint:
        search_ticker = f"{ticker}:NASDAQ" # Gokje voor US stocks
    elif ".AS" in ticker:
        search_ticker = f"{ticker.replace('.AS','')}:AMS"
    
    try:
        url = f"https://www.google.com/finance/quote/{search_ticker}"
        r = requests.get(url, headers=HEADERS, timeout=4)
        if r.status_code == 200:
            txt = r.text
            # Google classes zijn obfuscated, dus we zoeken op tekst labels
            def get_g_val(label):
                # Patroon: Zoek label -> zoek sluit div -> zoek volgende div met text
                # Dit is fragiel, maar werkt vaak voor de "About" sectie of stats balk
                pat = rf'{label}</div>.*?<div[^>]*>([^<]+)</div>'
                m = re.search(pat, txt, re.DOTALL)
                if m: return clean_float(m.group(1))
                return None
            
            data['pe'] = get_g_val("P/E ratio")
            data['div_yield'] = get_g_val("Dividend yield")
            # Google heeft minder data direct zichtbaar in de simpele view, 
            # maar Market cap en PE zijn meestal beschikbaar.
            # Voor high/low 52 wk etc.
            
    except Exception: pass
    return data

def calculate_average(values):
    """Berekent gemiddelde van een lijst, negeert None"""
    clean_vals = [v for v in values if v is not None]
    if not clean_vals: return None
    return sum(clean_vals) / len(clean_vals)

def get_averaged_data(ticker, us_ticker=None):
    """Haalt data op van alle bronnen en berekent gemiddelden"""
    
    # 1. Bepaal tickers per bron
    y_tick = us_ticker if us_ticker else ticker
    f_tick = us_ticker if us_ticker else ticker # Finviz is US only
    g_tick = ticker # Google snapt vaak internationale codes beter mits formatted
    
    # 2. Scrape Parallel (in theorie, hier serieel)
    data_y = scrape_yahoo_live(y_tick)
    data_f = scrape_finviz(f_tick) if f_tick == y_tick else {} # Alleen finviz als we een US ticker hebben
    data_g = scrape_google_finance(g_tick)
    
    combined = {}
    sources_used = {}
    
    # Lijst van keys die we willen middelen
    metrics = ['peg', 'fwd_pe', 'pe', 'pb', 'roe', 'de', 'cr', 'profit_margin', 'div_yield', 'target']
    
    for m in metrics:
        vals = []
        srcs = []
        
        if data_y.get(m) is not None: vals.append(data_y[m]); srcs.append("Yahoo")
        if data_f.get(m) is not None: vals.append(data_f[m]); srcs.append("Finviz")
        # Google heeft beperkte set, voeg toe indien gevonden
        if data_g.get(m) is not None: vals.append(data_g[m]); srcs.append("Google")
        
        avg = calculate_average(vals)
        combined[m] = avg
        sources_used[m] = " + ".join(srcs) if srcs else "N/A"
        
    # EPS bewaren we los uit Finviz of Yahoo voor de AI score check
    combined['eps'] = data_f.get('eps') 
    
    return combined, sources_used

# --- OVERIGE HULPFUNCTIES (News, Youtube, Search) ---

def search_yahoo_strict(query):
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10"
        r = requests.get(url, headers=HEADERS, timeout=3)
        d = r.json()
        res = []
        if 'quotes' in d:
            qs = d['quotes']
            def sort_key(q):
                s = q.get('symbol', '')
                if s.endswith('.DE'): return 0
                if s.endswith('.F'): return 1
                if s.endswith('.AS'): return 2
                if '.' not in s: return 10
                return 20
            qs.sort(key=sort_key)
            for q in qs:
                res.append(f"{q['symbol']} | {q.get('shortname','')}")
        return res
    except: return []

def get_google_news(query):
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={query}+stock&hl=en-US&gl=US&ceid=US:en", timeout=4)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:4]:
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

# --- AI LOGICA ---
def calculate_ai_score(metrics, eps=None):
    score, count = 0, 0
    opps, risks = [], []
    
    # Kill Switch
    margin = metrics.get('profit_margin', 0) or 0
    if (eps and eps < 0) or (margin and margin < 0):
        return 4.0, [], ["Verlieslatend bedrijf (Negatieve Winst/Marge)"]

    rules = [
        (metrics.get('peg'), "PEG", lambda x: 0<x<1.5, lambda x: x<0 or x>3.0),
        (metrics.get('fwd_pe'), "Fwd P/E", lambda x: 0<x<20, lambda x: x>40),
        (metrics.get('roe'), "ROE", lambda x: x>0.15, lambda x: x<0.05),
        (metrics.get('de'), "D/E", lambda x: x<1.0, lambda x: x>2.5),
        (metrics.get('cr'), "Current", lambda x: x>1.5, lambda x: x<0.9),
        (metrics.get('pb'), "P/B", lambda x: 0<x<3.0, lambda x: x>10)
    ]
    
    for val, name, good, bad in rules:
        if val is not None:
            count += 1
            if good(val): 
                score += 1; opps.append(f"{name} ({val:.2f})")
            elif bad(val):
                risks.append(f"{name} ({val:.2f})")
            else:
                score += 0.5
            
    final = (score/count)*10 if count > 0 else 5.0
    return round(final, 1), opps, risks

# --- DATA MANAGER ---
def handle_upload():
    f = st.session_state.get('uploader')
    if f:
        try:
            d = json.load(f)
            if isinstance(d, dict):
                st.session_state['portfolio'] = d.get('portfolio', [])
                st.session_state['wishlist'] = d.get('wishlist', [])
            st.toast("Geladen!", icon="💾")
        except: st.error("Fout bestand.")

def get_json():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- UI SETUP ---
st.sidebar.header("📂 Data")
st.sidebar.download_button(f"⬇️ Download", get_json(), "data.json", "application/json")
st.sidebar.file_uploader("⬆️ Upload", type=['json'], key='uploader', on_change=handle_upload)
st.sidebar.divider()
if st.sidebar.button("🧹 Reset"): st.cache_data.clear(); st.rerun()

tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist", "🇳🇱 Belasting"])

# ==================================================
# 1. ANALYSE
# ==================================================
with tabs[0]:
    c1, c2 = st.columns([3, 1])
    with c1:
        dq = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek:", value=dq, placeholder="Naam of Ticker...", key="search_main")
        sel = None
        if txt:
            if "." in txt and " " not in txt: sel = txt
            else:
                res = search_yahoo_strict(txt)
                if res: sel = st.selectbox("Resultaat:", res).split(" | ")[0]
                else: sel = txt.upper()
    with c2:
        per = st.selectbox("Grafiek", ["1mo", "6mo", "1y", "5y"], index=2)

    if sel:
        try:
            st.markdown("---")
            # We gebruiken yfinance nog wel voor de prijsgrafiek en basis info (naam, valuta)
            tick = yf.Ticker(sel)
            info = tick.info
            hist = tick.history(period=per)
            
            if hist.empty: 
                st.error("Geen koersdata gevonden.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price = hist['Close'].iloc[-1] * rate
                
                # Bepaal US ticker voor Finviz scraping
                us_mt = sel
                if sel in EU_TO_US_MAP: us_mt = EU_TO_US_MAP[sel]
                elif "." in sel:
                    # Poging om ticker om te zetten als er geen map is
                    sn = safe_get(info, 'shortName', '')
                    base = re.split(r' (Inc|Corp|AG|SE|NV)', sn)[0].strip()
                    try:
                        us_res = search_yahoo_strict(base) 
                        for r in us_res:
                            s = r.split(" | ")[0]
                            if "." not in s: us_mt = s; break
                    except: pass

                # --- HIER HALEN WE DE GEMIDDELDE DATA OP ---
                with st.spinner(f"Live data scrapen van Yahoo, Finviz & Google..."):
                    avg_data, sources = get_averaged_data(sel, us_mt if us_mt != sel else None)
                
                # Variabelen klaarzetten voor display
                peg = avg_data.get('peg')
                fwd = avg_data.get('fwd_pe')
                tgt = avg_data.get('target')
                pb = avg_data.get('pb')
                de = avg_data.get('de')
                cr = avg_data.get('cr')
                roe = avg_data.get('roe')
                dy = avg_data.get('div_yield')
                marg = avg_data.get('profit_margin')
                eps = avg_data.get('eps')
                
                # Upside berekening op basis van average target
                if tgt:
                    # Targets zijn vaak in de originele currency van de bron (meestal USD bij Finviz)
                    # We nemen aan dat target in de valuta van het aandeel is of US map
                    # Voor de zekerheid rekenen we om als de scraping via US ticker ging
                    tgt_curr = "USD" if us_mt != sel else curr
                    tgt_eur = tgt * get_currency_rate(tgt_curr, "EUR")
                    upside = f"{((tgt_eur-price)/price)*100:+.1f}%"
                    tgt_disp = tgt_eur
                else: 
                    upside = ""
                    tgt_disp = None

                score, opps_list, risks_list = calculate_ai_score(avg_data, eps)

                # --- UI BOUWEN ---
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1: 
                    st.title(truncate_text(safe_get(info, 'shortName', sel)))
                    st.caption(f"{sel} | Metrics via: {us_mt if us_mt!=sel else sel}")
                with c2: st.metric("Prijs", f"€ {price:.2f}")
                with c3: st.metric("Doel (Avg)", f"€ {tgt_disp:.2f}" if tgt_disp else "-", delta=upside, help=f"Gemiddelde van: {sources.get('target')}")
                with c4:
                    with st.popover("➕"):
                        with st.form("add"):
                            n = st.number_input("Aantal", 1.0)
                            p = st.number_input("GAK", value=float(round(price, 2)))
                            if st.form_submit_button("Portfolio"):
                                st.session_state['portfolio'].append({'ticker': sel, 'amount': n, 'avg_price_eur': p})
                                st.rerun()
                        if st.button("Wishlist"):
                            st.session_state['wishlist'].append({'ticker': sel, 'name': safe_get(info, 'shortName')})
                            st.rerun()

                # SCORE DISPLAY
                col_score = "green" if score>=7 else "orange" if score>=5.5 else "red"
                st.markdown(f"""
                <div style="display:flex; align-items:center; gap: 15px; margin: 10px 0;">
                    <div style="min-width:80px; height:80px; border-radius:50%; border:5px solid {col_score}; display:flex; align-items:center; justify-content:center;">
                        <span style="font-size:24px; font-weight:bold; color:{col_score}">{score:.1f}</span>
                    </div>
                    <div><small>✅ {', '.join(opps_list) if opps_list else '-'}<br>⚠️ {', '.join(risks_list) if risks_list else '-'}</small></div>
                </div>""", unsafe_allow_html=True)

                # METRICS ROW 1
                cols = st.columns(4)
                def dm(i, label, val, key, fmt="{:.2f}"):
                    src = sources.get(key, "N/A")
                    c = "⚪"
                    if val:
                        if key=="peg": c="🟢" if 0<val<1.5 else "🟠"
                        if key=="fwd_pe": c="🟢" if 0<val<20 else "🟠"
                        if key=="roe": c="🟢" if val>0.15 else "🟠"
                        if key=="div_yield": c="🟢" if val>0.03 else "🔵"
                    
                    with cols[i]: 
                        st.metric(
                            label, 
                            f"{c} {fmt.format(val)}" if val is not None else "-", 
                            help=f"Bronnen: {src}\n{TOOLTIPS.get(key,'')}"
                        )
                
                dm(0, "PEG (5yr)", peg, 'peg')
                dm(1, "Fwd P/E", fwd, 'fwd_pe')
                dm(2, "ROE", roe, 'roe', "{:.1%}")
                dm(3, "Dividend", dy, 'div_yield', "{:.2%}")
                
                # METRICS ROW 2
                cols2 = st.columns(4)
                def dm2(i, label, val, key, fmt="{:.2f}"):
                    src = sources.get(key, "N/A")
                    c = "⚪"
                    if val:
                        if key=="cr": c="🟢" if val>1.5 else "🟠"
                        if key=="de": c="🟢" if val<1 else "🟠"
                        if key=="pb": c="🟢" if 0<val<3 else "🟠"
                        if key=="profit_margin": c="🟢" if val>0.2 else "🟠"
                    with cols2[i]: 
                        st.metric(
                            label, 
                            f"{c} {fmt.format(val)}" if val is not None else "-", 
                            help=f"Bronnen: {src}\n{TOOLTIPS.get(key,'')}"
                        )

                dm2(0, "Current Ratio", cr, 'cr')
                dm2(1, "Debt/Eq", de, 'de')
                dm2(2, "P/B", pb, 'pb')
                dm2(3, "Marge", marg, 'profit_margin', "{:.1%}")

                st.plotly_chart(px.line(x=hist.index, y=hist['Close']), use_container_width=True)
                
                # Nieuws & Video
                cn, cv = st.columns(2)
                with cn:
                    st.markdown("##### 📰 Nieuws")
                    for n in get_google_news(safe_get(info, 'shortName', sel)):
                        st.markdown(f"[{n['title']}]({n['link']})")
                        st.divider()
                with cv:
                    st.markdown("##### 📺 Video")
                    vid = get_youtube_video_id(safe_get(info, 'shortName', sel))
                    if vid: st.video(f"https://www.youtube.com/watch?v={vid}")
                    
        except Exception as e: 
            st.error(f"Fout bij ophalen data: {e}")
            # Optioneel: print stacktrace voor debugging
            # import traceback; st.text(traceback.format_exc())

# ==================================================
# 2. PORTFOLIO
# ==================================================
with tabs[1]:
    if st.session_state['portfolio']:
        rows = []
        tv = 0
        ti = 0
        sd = {}
        dc = []
        
        bar = st.progress(0)
        for i, item in enumerate(st.session_state['portfolio']):
            try:
                t = item['ticker']
                tk = yf.Ticker(t)
                h = tk.history(period="1d")
                if not h.empty:
                    r = get_currency_rate(safe_get(tk.info, 'currency', 'EUR'), "EUR")
                    cur = h['Close'].iloc[-1] * r
                else: cur = item['avg_price_eur']
                
                v = cur * item['amount']
                c = item['avg_price_eur'] * item['amount']
                tv += v
                ti += c
                
                s = safe_get(tk.info, 'sector', 'Overig')
                sd[s] = sd.get(s, 0) + v

                # Dividend schatting (simpel via info, want real-time scrape is zwaar voor hele lijst)
                dr = safe_get(tk.info, 'dividendRate')
                if dr:
                    dye = dr * r * item['amount']
                    ex = safe_get(tk.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m') if ex else "-"
                    dc.append({"Aandeel": t, "Datum": exs, "Bedrag": dye})

                rows.append({"Naam": t, "Aantal": item['amount'], "Prijs": f"€{cur:.2f}", "Waarde": f"€{v:.2f}", "Winst": f"€{v-c:.2f}"})
            except: pass
            bar.progress((i+1)/len(st.session_state['portfolio']))
        bar.empty()
        
        k1, k2 = st.columns(2)
        k1.metric("Waarde", f"€ {tv:,.2f}")
        k2.metric("Winst", f"€ {tv-ti:,.2f}", delta=f"{((tv-ti)/ti)*100:.1f}%" if ti else "0%")
        
        c1, c2 = st.columns(2)
        with c1:
            if sd and tv > 0: st.plotly_chart(px.pie(values=list(sd.values()), names=list(sd.keys()), hole=0.4), use_container_width=True)
        with c2:
            if dc:
                dfd = pd.DataFrame(dc)
                st.metric("Mnd Inkomen (Schatting)", f"€ {dfd['Bedrag'].sum()/12:.2f}")
                st.dataframe(dfd, hide_index=True)
        
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if st.button("Wissen"): st.session_state['portfolio'] = []; st.rerun()
        
    else: st.info("Je portfolio is nog leeg.")

# ==================================================
# 3. WISHLIST
# ==================================================
with tabs[2]:
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            t = item['ticker']
            try:
                tk = yf.Ticker(t)
                h = tk.history(period="2d")
                if not h.empty:
                    r = get_currency_rate(safe_get(tk.info, 'currency', 'EUR'), "EUR")
                    cp = h['Close'].iloc[-1] * r
                    ch = ((cp - h['Close'].iloc[0] * r) / (h['Close'].iloc[0] * r)) * 100
                    col = "green" if ch >= 0 else "red"
                    
                    st.markdown(f"""
                    <div style="border-bottom:1px solid #444; padding:8px; display:flex; justify-content:space-between; align-items:center;">
                        <div><b>{item['name']}</b><br><span style='color:gray;font-size:0.8em'>{t}</span></div>
                        <div style="text-align:right;"><b>€{cp:.2f}</b><br><span style='color:{col};font-size:0.9em'>{ch:+.1f}%</span></div>
                    </div>""", unsafe_allow_html=True)
                    
                    if st.button("🔎 Analyseer", key=f"wl_{t}"):
                        st.session_state['search_query'] = t
                        st.rerun()
            except: pass
        if st.button("Legen", key="wl_clr"): st.session_state['wishlist'] = []; st.rerun()
    else: st.info("Nog geen aandelen op je verlanglijst.")

# ==================================================
# 4. BELASTING
# ==================================================
with tabs[3]:
    st.header("Box 3 Calculator")
    c1, c2 = st.columns(2)
    with c1:
        yr = st.selectbox("Belastingjaar", [2023, 2024, 2025])
        d = TAX_DEFAULTS.get(yr, {"invest": 6.04, "tax": 36})
        fr = st.number_input("Fictief Rendement (%)", value=d["invest"])
        tr = st.number_input("Belastingtarief (%)", value=float(d["tax"]))
    with c2:
        sv = st.number_input("Startwaarde (1 jan)", 0.0)
        ev = st.number_input("Eindwaarde (31 dec)", 0.0)
        inl = st.number_input("Netto Inleg gedurende jaar", 0.0)
    
    real_return = (ev - sv) - inl
    tax_calc = (sv * fr / 100) * (tr / 100)
    
    st.divider()
    c3, c4 = st.columns(2)
    with c3: st.metric("Werkelijk Rendement", f"€ {real_return:.2f}")
    with c4: st.metric("Te Betalen Belasting", f"€ {tax_calc:.2f}", help=f"Gebaseerd op startwaarde €{sv} * {fr}% * {tr}%")
