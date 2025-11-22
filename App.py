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

# --- MAPPING (EU -> US) ---
EU_TO_US_MAP = {
    "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
    "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
    "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
    "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
    "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
    "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "IMCD.AS": "IMCDY", "0DD.F": "PDD"
}

# --- TOOLTIPS ---
TOOLTIPS = {
    "peg": "Prijs/Winst gedeeld door Groei.\n🟢 < 1.5: Goedkoop.\n🔴 Negatief: Krimp.",
    "fwd_pe": "Verwachte K/W.\n🟢 < 20: Aantrekkelijk.\n🟠 > 30: Duur.",
    "roe": "Rendement op Eigen Vermogen.\n🟢 > 15%: Sterk management.",
    "de": "Schuldratio.\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
    "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
    "pb": "Prijs / Boekwaarde.\n🟢 < 3.0: Redelijk.",
    "div": "Dividend Rendement.\n🟢 > 3%: Hoog inkomen.",
    "beta": "Volatiliteit.\n🟢 < 0.8: Stabiel.",
    "rsi": "Momentum.\n🟢 < 30: Koopkans?.",
    "margin": "Winstmarge.\n🟢 > 20%: Zeer winstgevend.\n🔴 < 0%: Verlieslatend."
}

# --- HULPFUNCTIES ---
@st.cache_data(ttl=3600)
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

def safe_get(d, k, default=None):
    return d.get(k) if d and d.get(k) is not None else default

def truncate_text(text, max_len=25):
    if text and len(text) > max_len: return text[:max_len] + "..."
    return text

# --- SCRAPERS ---

@st.cache_data(ttl=3600)
def scrape_google_finance(ticker):
    """Probeert data van Google Finance te halen (Experimental)"""
    d = {}
    try:
        # Google Finance gebruikt eigen ticker format (bv NASDAQ:PYPL)
        # We gokken op simpele search query
        url = f"https://www.google.com/finance/quote/{ticker}:NASDAQ" 
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=h, timeout=3)
        if r.status_code == 200:
            txt = r.text
            # P/E ratio scraping (class namen veranderen vaak, dit is een gok op structuur)
            # Zoek naar "P/E ratio" en pak getal erna
            if "P/E ratio" in txt:
                m = re.search(r'P/E ratio</div>.*?<div class=".*?">([\d\.]+)</div>', txt, re.DOTALL)
                if m: d['pe'] = float(m.group(1))
            if "Market cap" in txt:
                # Google geeft bv 10.5B, lastig te parsen, slaan we even over
                pass
    except: pass
    return d

@st.cache_data(ttl=3600)
def scrape_yahoo_stats(ticker):
    d = {}
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://finance.yahoo.com/quote/{ticker}/key-statistics", headers=h, timeout=4)
        if r.status_code == 200:
            txt = r.text
            m = re.search(r'Forward P/E.*?<td.*?>(.*?)</td>', txt, re.DOTALL)
            if m: d['fwd_pe'] = float(re.sub(r'[^\d\.]', '', m.group(1)))
            m = re.search(r'PEG Ratio \(5 yr expected\).*?<td.*?>(.*?)</td>', txt, re.DOTALL)
            if m: d['peg'] = float(re.sub(r'[^\d\.]', '', m.group(1)))
    except: pass
    return d

@st.cache_data(ttl=3600)
def get_finviz_data(ticker):
    d = {}
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://finviz.com/quote.ashx?t={ticker}", headers=h, timeout=4)
        if r.status_code == 200:
            txt = r.text
            def p(lbl):
                m = re.search(rf'>{lbl}</td>.*?<b>\s*([\d\.]+)\s*</b>', txt, re.DOTALL)
                return float(m.group(1)) if m else None
            d['peg'] = p('PEG')
            d['pb'] = p('P/B')
            d['fwd_pe'] = p('Forward P/E')
            d['target'] = p('Target Price')
    except: pass
    return d

# PRIORITY FINDER
def get_best_metric(metric_key, yahoo_info, finviz_data, yahoo_scrape, google_data, calc_func=None):
    """
    Volgorde:
    1. Finviz (Vaak accuraat voor US stocks)
    2. Yahoo Web Scraper
    3. Google Finance (Als toegevoegd)
    4. Yahoo API
    5. Zelf berekenen
    """
    fv_map = {'pegRatio': 'peg', 'forwardPE': 'fwd_pe', 'priceToBook': 'pb', 'targetMeanPrice': 'target'}
    ys_map = {'pegRatio': 'peg', 'forwardPE': 'fwd_pe'}
    gf_map = {'trailingPE': 'pe'} # Google heeft beperkte data in scraper

    # 1. Finviz
    if fv_map.get(metric_key) and finviz_data.get(fv_map[metric_key]): 
        return finviz_data[fv_map[metric_key]], "Finviz"
    
    # 2. Yahoo Scraper
    if ys_map.get(metric_key) and yahoo_scrape.get(ys_map[metric_key]): 
        return yahoo_scrape[ys_map[metric_key]], "Yahoo Web"

    # 3. Google Finance
    if gf_map.get(metric_key) and google_data.get(gf_map[metric_key]):
        return google_data[gf_map[metric_key]], "Google Fin"

    # 4. Yahoo API
    y_val = safe_get(yahoo_info, metric_key)
    if y_val: return y_val, "Yahoo API"

    # 5. Calculator
    if calc_func:
        try:
            res = calc_func()
            if res and not np.isnan(res) and not np.isinf(res): return res, "Berekend"
        except: pass
    
    return None, "N/A"

def search_yahoo(query):
    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&region=US&quotesCount=10", headers=h, timeout=3)
        d = r.json()
        res = []
        if 'quotes' in d:
            qs = d['quotes']
            # SORTERING: EU EERST!
            def sort_key(x):
                s = x.get('symbol', '')
                if s.endswith(('.DE', '.F', '.AS', '.CO', '.PA', '.LS')): return 0
                return 1
            qs.sort(key=sort_key)
            
            for q in qs: res.append(f"{q['symbol']} | {q.get('shortname','')}")
        return res
    except: return []

def get_google_news(query):
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={query}+stock+finance&hl=en-US&gl=US&ceid=US:en", timeout=5)
        root = ET.fromstring(r.content)
        items = []
        for i in root.findall('./channel/item')[:5]:
            try: d = datetime.strptime(i.find('pubDate').text[:16], '%a, %d %b %Y').strftime('%d %b')
            except: d = ""
            items.append({"title": i.find('title').text, "link": i.find('link').text, "date": d})
        return items
    except: return []

@st.cache_data(ttl=3600)
def get_youtube_video_id(query):
    try:
        url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}+stock+analysis+{datetime.now().year}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        vids = re.findall(r"watch\?v=(\S{11})", r.text)
        return vids[0] if vids else None
    except: return None

# --- STRICT AI SCORE ---
def calculate_ai_score(metrics):
    score, count = 0, 0
    opps, risks = [], []
    
    # KILL SWITCHES: Strafpunten voor slechte fundamentals
    margin = metrics.get('margin', 0) or 0
    
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
                score += 1
                opps.append(f"{name} ({val:.2f})")
            elif bad(val):
                risks.append(f"{name} ({val:.2f})")
            else:
                score += 0.5
    
    # Basis Score
    final = (score/count)*10 if count > 0 else 5.0
    
    # PENALTIES (Realistisch maken voor verlieslatende bedrijven)
    if margin < 0:
        final = min(final, 5.5) # Maximaal 5.5 als je verlies maakt
        risks.insert(0, f"Verlieslatend (Marge {margin:.1%})")
    
    if metrics.get('de') and metrics.get('de') > 4.0:
        final = min(final, 4.0) # Maximaal 4 als je verdrinkt in schuld
        risks.insert(0, "Extreem hoge schulden")

    return final, opps, risks

# --- DATA BEHEER ---
def handle_upload():
    uploaded_file = st.session_state.get('uploader')
    if uploaded_file:
        try:
            uploaded_file.seek(0)
            data = json.load(uploaded_file)
            if isinstance(data, dict):
                st.session_state['portfolio'] = data.get('portfolio', [])
                st.session_state['wishlist'] = data.get('wishlist', [])
            st.toast("✅ Data geladen!", icon="💾")
        except: st.error("Fout bestand.")

def get_json_data():
    return json.dumps({'portfolio': st.session_state['portfolio'], 'wishlist': st.session_state['wishlist']})

# --- SIDEBAR ---
st.sidebar.header("📂 Data")
st.sidebar.download_button(f"⬇️ Download", get_json_data(), "data.json", "application/json")
st.sidebar.file_uploader("⬆️ Upload", type=['json'], key='uploader', on_change=handle_upload)
st.sidebar.divider()
if st.sidebar.button("🧹 Cache Legen"): st.cache_data.clear(); st.rerun()

# --- TABS ---
tabs = st.tabs(["🔎 Analyse", "📊 Portfolio", "❤️ Wishlist", "🇳🇱 Belasting"])

# ==================================================
# 1. ANALYSE
# ==================================================
with tabs[0]:
    c1, c2 = st.columns([3, 1])
    with c1:
        dq = st.session_state.get('search_query', "")
        txt = st.text_input("Zoek:", value=dq, placeholder="Naam...", key="search_main")
        sel = None
        if txt:
            if "." in txt and " " not in txt: sel = txt
            else:
                res = search_yahoo(txt)
                if res: sel = st.selectbox("Resultaat:", res).split(" | ")[0]
                else: sel = txt.upper()
    with c2:
        per = st.selectbox("Periode", ["1mo", "6mo", "1y", "5y"], index=2)

    if sel:
        try:
            st.markdown("---")
            tick = yf.Ticker(sel)
            info = tick.info
            hist = tick.history(period=per)
            
            if hist.empty: st.error("Geen data.")
            else:
                curr = safe_get(info, 'currency', 'EUR')
                rate = get_currency_rate(curr, "EUR")
                price = hist['Close'].iloc[-1] * rate
                
                # SMART DATA
                mt = sel
                if sel in EU_TO_US_MAP: mt = EU_TO_US_MAP[sel]
                minfo = yf.Ticker(mt).info
                mfin = get_finviz_data(mt)
                mscr = scrape_yahoo_stats(mt)
                mgoogle = scrape_google_finance(mt) # Nieuw

                # --- METRICS OPHALEN ---
                def c_peg():
                    p = safe_get(minfo, 'trailingPE')
                    g = safe_get(minfo, 'earningsGrowth')
                    # Als winst negatief is, is PEG onbruikbaar (geen berekening)
                    if p and g and p > 0 and g > 0: return p/(g*100)
                    return None
                
                peg, peg_s = get_best_metric('pegRatio', minfo, mfin, mscr, mgoogle, c_peg)
                
                # P/E Fallback: Prijs / Winst per aandeel
                def c_pe():
                    eps = safe_get(minfo, 'trailingEps')
                    if eps and eps > 0: return price / (eps*rate)
                    return None
                pe, pe_s = get_best_metric('trailingPE', minfo, mfin, mscr, mgoogle, c_pe)
                
                fwd, fwd_s = get_best_metric('forwardPE', minfo, mfin, mscr, mgoogle)
                pb, pb_s   = get_best_metric('priceToBook', minfo, mfin, mscr, mgoogle)
                tgt, tgt_s = get_best_metric('targetMeanPrice', minfo, mfin, mscr, mgoogle)
                if tgt: tgt *= get_currency_rate(safe_get(minfo, 'currency', 'USD'), "EUR")

                # Balans Calcs
                def c_de():
                    d = safe_get(minfo, 'totalDebt')
                    e = safe_get(minfo, 'totalStockholderEquity')
                    if d and e: return d/e
                    return None
                de, de_s = get_best_metric('debtToEquity', minfo, {}, {}, {}, c_de)
                if de and de > 10: de/=100 # Fix percentage vs ratio

                def c_cr():
                    a = safe_get(minfo, 'totalCurrentAssets')
                    l = safe_get(minfo, 'totalCurrentLiabilities')
                    if a and l: return a/l
                    return None
                cr, cr_s = get_best_metric('currentRatio', minfo, {}, {}, {}, c_cr)
                
                roe, roe_s = get_best_metric('returnOnEquity', minfo, {}, {}, {})
                
                dy, dy_s = get_best_metric('dividendYield', minfo, {}, {}, {})
                if not dy:
                    dr = safe_get(minfo, 'dividendRate')
                    if dr:
                        try:
                            op = yf.Ticker(mt).history(period="1d")['Close'].iloc[-1]
                            dy = dr/op; dy_s = "Berekend"
                        except: pass
                if dy and dy > 0.5: dy /= 100

                marg = safe_get(minfo, 'profitMargins')
                
                # SCORE
                sd = {'peg': peg, 'fwd_pe': fwd, 'roe': roe, 'de': de, 'cr': cr, 'pb': pb, 'margin': marg}
                score, opps, risks = calculate_ai_score(sd)

                # --- UI ---
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1: 
                    st.title(truncate_text(safe_get(info, 'shortName', sel)))
                    st.caption(f"{sel} | Data: {mt}")
                with c2: st.metric("Prijs", f"€ {price:.2f}")
                with c3: st.metric("Doel", f"€ {tgt:.2f}" if tgt else "-", help=f"Bron: {tgt_s}")
                with c4:
                    with st.popover("➕ Toevoegen"):
                        with st.form("add"):
                            n = st.number_input("Aantal", 1.0)
                            p = st.number_input("GAK (€)", value=float(round(price, 2)))
                            if st.form_submit_button("Portfolio"):
                                st.session_state['portfolio'].append({'ticker': sel, 'amount': n, 'avg_price_eur': p})
                                st.rerun()
                        if st.button("Wishlist"):
                            st.session_state['wishlist'].append({'ticker': sel, 'name': safe_get(info, 'shortName')})
                            st.rerun()

                col = "green" if score>=7 else "orange" if score>=5.5 else "red"
                st.markdown(f"""
                <div style="display:flex; align-items:center; gap: 15px; margin: 10px 0;">
                    <div style="min-width:70px; height:70px; border-radius:50%; border:5px solid {col}; display:flex; align-items:center; justify-content:center;">
                        <span style="font-size:24px; font-weight:bold; color:{col}">{score:.1f}</span>
                    </div>
                    <div><small>✅ {', '.join(opps) if opps else '-'}<br>⚠️ {', '.join(risks) if risks else '-'}</small></div>
                </div>
                """, unsafe_allow_html=True)

                # METRICS DISPLAY
                cols = st.columns(4)
                def dm(i, l, v, s, k, f="{:.2f}"):
                    ic = "⚪"
                    if v is not None:
                        # Kleur logica hier (simpel gehouden voor beknoptheid)
                        # Je kunt hier je uitgebreide color_logic terugzetten
                        pass 
                    with cols[i]: st.metric(l, f.format(v) if v else "-", help=f"Bron: {s}\n\n{TOOLTIPS.get(k,'')}")
                
                dm(0, "PEG", peg, peg_s, 'peg')
                dm(1, "Fwd P/E", fwd, fwd_s, 'fwd_pe')
                dm(2, "ROE", roe, roe_s, 'roe', "{:.1%}")
                dm(3, "Dividend", dy, dy_s, 'div', "{:.2%}")
                
                # 2e rij
                cols2 = st.columns(4)
                with cols2[0]: st.metric("Current", f"{cr:.2f}" if cr else "-", help=f"Bron: {cr_s}")
                with cols2[1]: st.metric("D/E", f"{de:.2f}" if de else "-", help=f"Bron: {de_s}")
                with cols2[2]: st.metric("P/B", f"{pb:.2f}" if pb else "-", help=f"Bron: {pb_s}")
                with cols2[3]: st.metric("Marge", f"{marg:.1%}" if marg else "-", help="Winstmarge")

                st.plotly_chart(px.line(x=hist.index, y=hist['Close']), use_container_width=True)
                
                cn, cv = st.columns(2)
                with cn:
                    st.markdown("##### 📰 Nieuws")
                    for n in get_google_news(safe_get(info, 'shortName', sel)):
                        st.markdown(f"[{n['title']}]({n['link']})")
                with cv:
                    st.markdown("##### 📺 Video")
                    vid = get_youtube_video_id(safe_get(info, 'shortName', sel))
                    if vid: st.video(f"https://www.youtube.com/watch?v={vid}")

        except Exception as e: st.error(f"Fout: {e}")

# ==================================================
# 2. PORTFOLIO
# ==================================================
with tabs[1]:
    if st.session_state['portfolio']:
        rows = []
        tv = 0
        ti = 0
        div_cal = []
        sec_dist = {}
        
        for item in st.session_state['portfolio']:
            try:
                t = item['ticker']
                tick = yf.Ticker(t)
                h = tick.history(period="1d")
                if not h.empty:
                    r = get_currency_rate(safe_get(tick.info, 'currency', 'EUR'), "EUR")
                    cur = h['Close'].iloc[-1] * r
                else: cur = item['avg_price_eur']
                
                v = cur * item['amount']
                c = item['avg_price_eur'] * item['amount']
                tv += v
                ti += c
                
                s = safe_get(tick.info, 'sector', 'Overig')
                sec_dist[s] = sec_dist.get(s, 0) + v
                
                dr = safe_get(tick.info, 'dividendRate')
                if dr:
                    dye = dr * r * item['amount']
                    ex = safe_get(tick.info, 'exDividendDate')
                    exs = datetime.fromtimestamp(ex).strftime('%d-%m') if ex else "-"
                    div_cal.append({"Aandeel": t, "Ex": exs, "Jaar (€)": dye})
                
                rows.append({"Naam": t, "Aantal": item['amount'], "Prijs": f"€{cur:.2f}", "Waarde": f"€{v:.2f}", "Winst": f"€{v-c:.2f}"})
            except: pass
        
        k1, k2 = st.columns(2)
        k1.metric("Waarde", f"€ {tv:,.2f}")
        k2.metric("Winst", f"€ {tv-ti:,.2f}", delta=f"{((tv-ti)/ti)*100:.1f}%" if ti else "0%")
        
        c_pie, c_div = st.columns(2)
        with c_pie:
            if sec_dist and tv>0: st.plotly_chart(px.pie(values=list(sec_dist.values()), names=list(sec_dist.keys()), hole=0.4), use_container_width=True)
        with c_div:
            if div_cal:
                dfd = pd.DataFrame(div_cal)
                st.metric("Mnd Inkomen", f"€ {dfd['Jaar (€)'].sum()/12:.2f}")
                st.dataframe(dfd, hide_index=True)
        
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if st.button("Alles Wissen"): st.session_state['portfolio'] = []; st.rerun()
        
        with st.expander("🧮 Calculator"):
            c1, c2 = st.columns(2)
            m = c1.number_input("Mnd Inleg", 500)
            j = c2.number_input("Jaren", 20)
            r = 7.0
            end = tv * ((1+r/100)**j) + (m * 12 * (((1+r/100)**j - 1) / (r/100)))
            st.metric("Eindbedrag (7%)", f"€ {end:,.0f}")
    else: st.info("Leeg.")

# ==================================================
# 3. WISHLIST
# ==================================================
with tabs[2]:
    if st.session_state['wishlist']:
        for item in st.session_state['wishlist']:
            t = item['ticker']
            
            # Geen redirect knop meer, gewoon info
            try:
                tick = yf.Ticker(t)
                h = tick.history(period="2d")
                if not h.empty:
                    r = get_currency_rate(safe_get(tick.info, 'currency', 'EUR'), "EUR")
                    cp = h['Close'].iloc[-1] * r
                    ch = ((cp - h['Close'].iloc[0] * r) / (h['Close'].iloc[0] * r)) * 100
                    col = "green" if ch >= 0 else "red"
                else: cp=0; ch=0; col="gray"
            except: cp=0; ch=0; col="gray"

            # Mobiele weergave (1 regel, flexbox)
            st.markdown(f"""
            <div style="border-bottom:1px solid #444; padding:8px; display:flex; justify-content:space-between; align-items:center;">
                <div><b>{item['name']}</b><br><span style='font-size:0.8em;color:#888'>{t}</span></div>
                <div style="text-align:right;"><b>€{cp:.2f}</b><br><span style='color:{col};font-size:0.9em'>{ch:+.1f}%</span></div>
            </div>
            """, unsafe_allow_html=True)
            
        if st.button("Legen", key="wl_clr"): st.session_state['wishlist'] = []; st.rerun()
    else: st.info("Leeg.")

# ==================================================
# 4. BELASTING (NIEUW)
# ==================================================
with tabs[3]:
    st.header("🇳🇱 Box 3 Check")
    c1, c2 = st.columns(2)
    with c1:
        start = st.number_input("Waarde 1 jan", 0.0, step=100.0)
        end = st.number_input("Waarde 31 dec", 0.0, step=100.0)
    with c2:
        inleg = st.number_input("Netto Inleg", 0.0, step=100.0)
        div = st.number_input("Dividend", 0.0, step=10.0)
        
    real = (end - start) - inleg + div
    
    # Fiscus (2024)
    fictief = (start * 6.04) / 100
    tax = (fictief * 0.36)
    
    c3, c4 = st.columns(2)
    with c3: st.metric("Werkelijk", f"€ {real:.2f}")
    with c4: st.metric("Belasting (Est)", f"€ {tax:.2f}", help="o.b.v. 6.04% rendement en 36% taks")
    
    if real > fictief: st.success("Je verslaat de fiscus!")
    else: st.warning("Fictief > Werkelijk")
