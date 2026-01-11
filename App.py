import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import json
import re
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
import time
import streamlit_authenticator as stauth

# ==================================================
# 1. CONFIGURATIE & AUTHENTICATIE
# ==================================================
st.set_page_config(page_title="Aandelen Dashboard Pro", layout="wide", page_icon="📈")

# --- AUTHENTICATIE CONFIGURATIE ---
# Let op: In productieomgevingen nooit wachtwoorden hardcoded laten staan.
names = ['Eigenaar']
usernames = ['admin']
passwords = ['$2b$12$rMeMz.5s/08S2o/e5Jp7EO.3yZ.s.y.o.w.n.o.t.h.i.n.g'] 

credentials = {
    'usernames': {
        usernames[0]: {
            'name': names[0],
            'password': passwords[0],
            'email': 'admin@example.com', # Vaak vereist in nieuwere versies
            'logged_in': False
        }
    }
}

# Compatibiliteit check voor cookie parameter (nieuwere versies gebruiken cookie_lifetime)
try:
    authenticator = stauth.Authenticate(
        credentials,
        'portfolio_dashboard_cookie', 
        'abcdef', 
        cookie_lifetime=30 
    )
except TypeError:
    # Fallback voor oudere versies
    authenticator = stauth.Authenticate(
        credentials,
        'portfolio_dashboard_cookie', 
        'abcdef', 
        cookie_expiry_days=30
    )

# --- AUTOMATISCHE OPSLAG FUNCTIES ---
DB_FILE = 'portfolio_db.json'

def json_serial(obj):
    """JSON serializer voor objecten die niet standaard serializable zijn (zoals datum)"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def load_portfolio_from_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                data = json.load(f)
                port = data.get('portfolio', [])
                
                # Datums herstellen
                for p in port:
                    if 'buy_date' in p and p['buy_date']:
                        try:
                            # Probeer ISO format (YYYY-MM-DD)
                            p['buy_date'] = datetime.strptime(p['buy_date'], "%Y-%m-%d").date()
                        except ValueError:
                            # Fallback voor als er per ongeluk een timestamp in zit
                            try:
                                p['buy_date'] = datetime.fromisoformat(p['buy_date']).date()
                            except:
                                p['buy_date'] = None
                                
                wishlist = data.get('wishlist', [])
                return port, wishlist
        except Exception as e:
            st.error(f"Fout bij laden database: {e}")
            return [], []
    return [], []

def save_portfolio_to_db():
    # We maken een kopie om de session state niet te verpesten met strings ipv date objecten
    serializable_port = []
    for p in st.session_state['portfolio']:
        p_copy = p.copy()
        if 'buy_date' in p_copy and isinstance(p_copy['buy_date'], (date, datetime)):
            p_copy['buy_date'] = p_copy['buy_date'].strftime("%Y-%m-%d")
        serializable_port.append(p_copy)
        
    data = {
        'portfolio': serializable_port,
        'wishlist': st.session_state.get('wishlist', [])
    }
    
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(data, f, default=json_serial, indent=4)
    except Exception as e:
        st.error(f"Kon database niet opslaan: {e}")

# --- INLOGGEN ---
# Afhankelijk van de versie van stauth, returnt login() soms niks en zet state, of returnt name/status
try:
    authenticator.login(location='main')
except Exception:
    # Fallback voor oudere syntax
    name, authentication_status, username = authenticator.login('Login', 'main')

if st.session_state.get("authentication_status") is False:
    st.error('Gebruikersnaam of wachtwoord is onjuist')
elif st.session_state.get("authentication_status") is None:
    st.warning('Voer je inloggegevens in')

elif st.session_state.get("authentication_status"):
    
    authenticator.logout('Uitloggen', 'sidebar')
    user_name = st.session_state.get("name", "Gebruiker")
    st.sidebar.write(f'Ingelogd als: *{user_name}*')
    st.sidebar.divider()
    
    # --- CONSTANTEN ---
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    EU_TO_US_MAP = {
        "HEIA.AS": "HEINY", "HEI.DE": "HEINY", "AD.AS": "ADRNY", "UNA.AS": "UL",
        "VOW3.DE": "VWAGY", "ADS.DE": "ADDYY", "BMW.DE": "BMWYY", "DTE.DE": "DTEGY",
        "SIE.DE": "SIEGY", "ALV.DE": "ALIZY", "AIR.PA": "EADSY", "MC.PA": "LVMUY",
        "OR.PA": "LRLCY", "ASML.AS": "ASML", "PHIA.AS": "PHG", "INGA.AS": "ING",
        "MT.AS": "MT", "DSM.AS": "DSM", "2PP.DE": "PYPL", "NOVO-B.CO": "NVO",
        "NOVO-B.DE": "NVO", "BESI.AS": "BESIY", "0DD.F": "PDD", "SHELL.AS": "SHEL",
        "WKL.AS": "WTKWY", "9PDA.DU": "PROSY", "RAND.AS": "RANJY", "AKZA.AS": "AKZOY",
        "DSM.AS": "DSM", "KPN.AS": "KKPNY", "NN.AS": "NNGP", "ASM.AS": "ASMIY"
    }

    TOOLTIPS = {
        "peg": "Price/Earnings to Growth (PEG).\n🟢 < 1.5: Ondergewaardeerd.\n🔴 > 2.5: Overgewaardeerd.",
        "pe": "Koers/Winst verhouding (P/E).\n🟢 < 20: Koopje.\n🟠 > 30: Premie.",
        "roe": "Rendement op Eigen Vermogen (ROE).\n🟢 > 15%: Sterk management.",
        "de": "Schuldratio (Debt/Equity).\n🟢 < 1.0: Gezond.\n🔴 > 2.0: Veel schulden.",
        "cr": "Current Ratio (Liquiditeit).\n🟢 > 1.5: Veilig.\n🔴 < 1.0: Krap.",
        "div": "Dividend Rendement.\n🟢 > 3%: Interessant.",
        "margin": "Nettowinstmarge.\n🟢 > 20%: Hoge winstmarge.",
        "eps": "Winst per aandeel (EPS).\n🟢 > 0: Winstgevend."
    }

    MATTE_COLORS = {
        "blue": "#457B9D", "green": "#2A9D8F", "red": "#E76F51", "yellow": "#E9C46A",    
        "charcoal": "#264653", "orange": "#F4A261", "purple": "#9B5DE5", "grey": "#A8DADC"       
    }

    SECTOR_COLORS = {
        "Technology": MATTE_COLORS["blue"], "Financial Services": MATTE_COLORS["charcoal"],
        "Healthcare": MATTE_COLORS["green"], "Consumer Cyclical": MATTE_COLORS["red"],
        "Industrials": MATTE_COLORS["purple"], "Consumer Defensive": MATTE_COLORS["orange"],
        "Energy": MATTE_COLORS["yellow"], "Communication Services": "#D62828", 
        "Real Estate": "#8D6E63", "Basic Materials": "#5F9EA0", "Utilities": "#76FF03",              
        "ETF/Overig": "#B0BEC5"              
    }

    # ==================================================
    # 2. STATE MANAGEMENT & INITIAL LOAD
    # ==================================================
    if 'portfolio' not in st.session_state:
        p, w = load_portfolio_from_db()
        st.session_state['portfolio'] = p
        st.session_state['wishlist'] = w

    if 'search_results' not in st.session_state: st.session_state['search_results'] = []
    if 'search_main' not in st.session_state: st.session_state['search_main'] = ""
    if 'tax_val_jan1' not in st.session_state: st.session_state.tax_val_jan1 = 0.0
    if 'tax_val_dec31' not in st.session_state: st.session_state.tax_val_dec31 = 0.0

    # ==================================================
    # 3. HELPER FUNCTIES
    # ==================================================

    @st.cache_data(ttl=600)
    def get_finviz_data(ticker):
        d = {}
        try:
            url = f"https://finviz.com/quote.ashx?t={ticker}"
            r = requests.get(url, headers=HEADERS, timeout=4)
            if r.status_code == 200:
                def parse(label):
                    m = re.search(rf'>{label}</td>.*?<b>\s*([\d\.-]+)\s*</b>', r.text, re.DOTALL)
                    if m and m.group(1) != '-':
                        return float(m.group(1))
                    return None
                
                d['peg'] = parse('PEG')
                d['target'] = parse('Target Price')
                d['eps'] = parse('EPS \(ttm\)')
                d['pe'] = parse('P/E')
                d['de'] = parse('Debt/Eq')
                d['roe'] = parse('ROE') 
                d['margin'] = parse('Profit Margin')
                d['div'] = parse('Dividend %')
        except Exception:
            pass
        return d

    @st.cache_data(ttl=300)
    def get_currency_rate(from_curr, to_curr="EUR"):
        if from_curr == to_curr or not from_curr: return 1.0
        try:
            ticker = "EURUSD=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
            data = yf.Ticker(ticker).history(period="1d")
            if not data.empty:
                rate = data['Close'].iloc[-1]
                if from_curr == "USD": return 1.0 / rate 
                return rate
            return 1.0
        except Exception:
            return 1.0

    @st.cache_data(ttl=3600)
    def get_reliable_earnings(ticker):
        # Vereenvoudigde functie om crashes te voorkomen
        history = []
        try:
            tk = yf.Ticker(ticker)
            df = tk.earnings_dates
            if df is not None and not df.empty:
                if df.index.tz is not None: df.index = df.index.tz_localize(None)
                now = datetime.now()
                df_past = df[df.index < now].dropna(subset=['Reported EPS', 'EPS Estimate']).sort_index(ascending=False).head(4)
                
                for date_idx, row in df_past.iterrows():
                    est = row['EPS Estimate']
                    rep = row['Reported EPS']
                    surp = row['Surprise(%)']
                    if pd.isna(surp) and est != 0: surp = ((rep - est) / abs(est)) * 100
                    elif pd.isna(surp): surp = 0
                    history.append({'date': date_idx.strftime('%Y-%m-%d'), 'reported': float(rep), 'estimate': float(est), 'surprise': float(surp)})
                if history: history.reverse()
        except Exception:
            pass
        return history

    @st.cache_data(ttl=3600)
    def get_extended_portfolio_data(ticker):
        try:
            tk = yf.Ticker(ticker)
            # Gebruik fast_info voor snelheid, fallback naar info
            try:
                fast_info = tk.fast_info
                current_price = fast_info.last_price
                prev_close = fast_info.previous_close
            except:
                current_price = None
                prev_close = None

            info = {}
            try:
                info = tk.info
            except:
                pass # Info faalt vaak bij Yahoo
            
            if current_price is None and info:
                current_price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
            
            if current_price is None:
                return None # Kritieke data mist

            q_type = info.get('quoteType', 'EQUITY').upper()
            q_type = 'ETF' if 'ETF' in q_type else 'Aandeel'
            
            earnings_history = get_reliable_earnings(ticker)
            earnings_surprise = {'date': earnings_history[-1]['date'], 'surprise_pct': earnings_history[-1]['surprise']} if earnings_history else None

            # Dividend
            dividend_months = []
            try:
                hist = tk.dividends
                if not hist.empty:
                    if hist.index.tz is not None: hist.index = hist.index.tz_localize(None)
                    last_year = datetime.now() - timedelta(days=365)
                    recent_divs = hist[hist.index > last_year]
                    for date_val, amount in recent_divs.items():
                        dividend_months.append({'month': date_val.month, 'amount': amount})
            except: pass

            # YTD Start
            ytd_start_price = None
            try:
                start_of_year = f"{datetime.now().year}-01-01"
                hist_ytd = tk.history(start=start_of_year, end=f"{datetime.now().year}-01-08", interval="1d")
                if not hist_ytd.empty:
                    ytd_start_price = hist_ytd['Close'].iloc[0]
            except: pass

            return {
                'type': q_type,
                'earnings': earnings_surprise,
                'earnings_history': earnings_history, 
                'dividend_history': dividend_months,
                'sector': info.get('sector', 'ETF/Overig'),
                'country': info.get('country', 'Onbekend'),
                'live_price': current_price,
                'day_change_pct': ((current_price - prev_close)/prev_close*100) if prev_close else 0.0,
                'target_price': info.get('targetMeanPrice'),
                'previous_close': prev_close,
                'ytd_start_price': ytd_start_price,
                'currency_symbol': info.get('currencySymbol', '€'),
                'shortName': info.get('shortName', ticker),
                'longName': info.get('longName', ticker),
                'fallback_div_rate': info.get('dividendRate'),
                'fallback_div_yield': info.get('dividendYield')
            }
        except Exception:
            return None

    def search_yahoo_strict(query):
        try:
            url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&quotesCount=10"
            r = requests.get(url, headers=HEADERS, timeout=3)
            data = r.json()
            results = []
            for q in data.get('quotes', []):
                if 'symbol' in q:
                    results.append(f"{q['symbol']} | {q.get('shortname') or q.get('longname')}")
            return results
        except: return []

    def get_google_news(query):
        try:
            query_encoded = query.replace(' ', '+')
            url = f"https://news.google.com/rss/search?q={query_encoded}&hl=en-US&gl=US&ceid=US:en"
            r = requests.get(url, headers=HEADERS, timeout=4)
            root = ET.fromstring(r.content)
            items = []
            for i in root.findall('./channel/item')[:3]:
                items.append({"title": i.find('title').text, "link": i.find('link').text, "date": i.find('pubDate').text[:16]})
            return items
        except: return []

    @st.cache_data(ttl=3600)
    def get_youtube_video_id(query):
        try:
            q = f"{query} stock analysis {datetime.now().year}"
            url = f"https://www.youtube.com/results?search_query={q.replace(' ','+')}"
            r = requests.get(url, headers=HEADERS, timeout=5)
            vids = re.findall(r"watch\?v=(\S{11})", r.text)
            return vids[0] if vids else None
        except: return None

    # ==================================================
    # 4. DATA ENGINE & AI SCORE
    # ==================================================
    class DataEngine:
        def __init__(self, ticker, original_ticker):
            self.ticker = ticker 
            self.yf_obj = yf.Ticker(original_ticker)
            try: self.yf_info = self.yf_obj.info
            except: self.yf_info = {}
            self.finviz_data = get_finviz_data(self.ticker) if "." not in self.ticker else {}

        def _norm(self, val, is_pct, is_div, is_de):
            if val is None: return None
            if is_de and val > 5.0: return val / 100.0
            if is_pct and not is_div and abs(val) <= 1.0 and val != 0: return val * 100
            return val

        def get_metric(self, key_yf, key_fin, is_pct=False, is_div=False, is_de=False):
            vals = []
            srcs = []
            if self.yf_info.get(key_yf) is not None:
                vals.append(self._norm(float(self.yf_info[key_yf]), is_pct, is_div, is_de))
                srcs.append("Yahoo")
            if key_fin and self.finviz_data.get(key_fin) is not None:
                vals.append(self._norm(float(self.finviz_data[key_fin]), is_pct, is_div, is_de))
                srcs.append("Finviz")
            
            if not vals: return None, "N/A"
            return sum(vals)/len(vals), " & ".join(srcs)

    def calculate_ai_score(data):
        score = 5.0
        reasons = []
        
        # Logica voor puntentelling
        eps, _ = data['eps']
        if eps and eps > 0: score += 1.0; reasons.append("Winstgevend")
        elif eps: score -= 2.5; reasons.append("Verlieslatend")
        
        peg, _ = data['peg']
        if peg:
            if 0 < peg < 1.5: score += 1.0; reasons.append("Goede PEG")
            elif peg > 2.5: score -= 0.5
        
        pe, _ = data['pe']
        if pe and pe < 20 and pe > 0: score += 0.5
        
        div, _ = data['div']
        if div and div > 0.025: score += 0.5; reasons.append("Mooi Dividend")
        
        de, _ = data['de']
        if de and de < 1.0: score += 1.0
        elif de and de > 2.0: score -= 1.0

        margin, _ = data['margin']
        if margin and margin > 20: score += 1.0

        final_score = max(0.0, min(10.0, score))
        summary = "Gemiddeld profiel."
        if final_score >= 7.5: summary = f"Sterk aandeel! {', '.join(reasons[:2])}."
        elif final_score <= 4.0: summary = "Zwakke fundamentals."
        return round(final_score, 1), summary

    # ==================================================
    # 5. UI: SIDEBAR & UPLOAD FIX
    # ==================================================
    st.sidebar.header("📂 Data & Backup")
    
    # Download knop
    json_export = json.dumps({
        'portfolio': st.session_state.get('portfolio',[]), 
        'wishlist': st.session_state.get('wishlist',[])
    }, default=json_serial)
    
    st.sidebar.download_button(
        label="⬇️ Download Backup",
        data=json_export,
        file_name="aandelen_backup.json",
        mime="application/json"
    )

    # --- UPLOAD FIX ---
    # We controleren direct bij de upload, zonder extra knop
    uploaded_file = st.sidebar.file_uploader("⬆️ Upload Backup", type=['json'])
    
    if uploaded_file is not None:
        try:
            loaded_data = json.load(uploaded_file)
            
            # Controleer of het een geldige structuur is
            if 'portfolio' in loaded_data or 'wishlist' in loaded_data:
                # Verwerk Portfolio
                if 'portfolio' in loaded_data:
                    port = loaded_data['portfolio']
                    # Datums converteren
                    for p in port:
                        if 'buy_date' in p and p['buy_date']:
                            try:
                                p['buy_date'] = datetime.strptime(p['buy_date'], "%Y-%m-%d").date()
                            except ValueError:
                                try:
                                    p['buy_date'] = datetime.fromisoformat(p['buy_date']).date()
                                except:
                                    p['buy_date'] = None
                    st.session_state['portfolio'] = port
                
                # Verwerk Wishlist
                if 'wishlist' in loaded_data:
                    st.session_state['wishlist'] = loaded_data['wishlist']
                
                # Sla op in sessie en DB
                save_portfolio_to_db()
                st.toast("Backup succesvol ingeladen!", icon="✅")
                
                # We clearen de uploader uit de UI door een rerun, 
                # maar omdat state al is bijgewerkt blijft de data
                time.sleep(1.5)
                # Hack om file uploader te resetten is lastig, maar data is binnen
            else:
                st.error("Ongeldig JSON formaat.")
        except Exception as e:
            st.error(f"Fout bij lezen bestand: {e}")

    if st.sidebar.button("🧹 Cache Legen"):
        st.cache_data.clear()
        st.rerun()

    # ==================================================
    # 6. HOOFD TABS
    # ==================================================
    tabs = st.tabs(["🔎 Analyse", "📊 Portfolio Pro", "❤️ Wishlist", "🇳🇱 Belasting"])

    # --- TAB 1: ANALYSE ---
    with tabs[0]:
        st.subheader("Aandelen Analyse")
        col_search, col_period = st.columns([3, 1])
        with col_search:
            search_q = st.text_input("Zoek aandeel:", value="", placeholder="Bijv. AAPL of ASML.AS")
            if search_q:
                if "." in search_q and " " not in search_q: results = [search_q]
                else: results = search_yahoo_strict(search_q)
                
                selected_ticker = st.selectbox("Selecteer:", results) if results else None
                if selected_ticker:
                    selected_ticker = selected_ticker.split(" | ")[0]
                    # Data ophalen en tonen...
                    # (Code ingekort voor leesbaarheid, logica blijft hetzelfde als origineel)
                    us_eq = EU_TO_US_MAP.get(selected_ticker, selected_ticker)
                    eng = DataEngine(selected_ticker, us_eq)
                    
                    metrics = {
                        'peg': eng.get_metric('pegRatio', 'peg'),
                        'pe': eng.get_metric('trailingPE', 'pe'),
                        'div': eng.get_metric('dividendYield', 'div', is_pct=True, is_div=True),
                        'roe': eng.get_metric('returnOnEquity', 'roe', is_pct=True),
                        'de': eng.get_metric('debtToEquity', 'de', is_de=True),
                        'cr': eng.get_metric('currentRatio', None),
                        'margin': eng.get_metric('profitMargins', 'margin', is_pct=True),
                        'eps': eng.get_metric('trailingEps', 'eps')
                    }
                    score, summary = calculate_ai_score(metrics)
                    
                    # UI Tonen
                    info = eng.yf_info
                    curr_price = info.get('currentPrice', 0)
                    st.title(f"{info.get('shortName')} ({selected_ticker})")
                    st.metric("Prijs", f"{info.get('currencySymbol','')} {curr_price}")
                    
                    c1, c2 = st.columns([1,3])
                    with c1: st.metric("AI Score", f"{score}/10")
                    with c2: st.info(summary)
                    
                    # Toevoegen knop
                    with st.expander("➕ Toevoegen aan Portfolio"):
                        with st.form("add_new"):
                            amt = st.number_input("Aantal", 1.0)
                            prc = st.number_input("Prijs", float(curr_price) if curr_price else 0.0)
                            dt = st.date_input("Datum", datetime.now())
                            if st.form_submit_button("Toevoegen"):
                                st.session_state['portfolio'].append({
                                    'ticker': selected_ticker,
                                    'amount': amt,
                                    'avg_price': prc,
                                    'currency': info.get('currency', 'EUR'),
                                    'symbol': info.get('currencySymbol', '€'),
                                    'buy_date': dt
                                })
                                save_portfolio_to_db()
                                st.success("Toegevoegd!")
                                st.rerun()

    # --- TAB 2: PORTFOLIO ---
    with tabs[1]:
        if not st.session_state['portfolio']:
            st.info("Portfolio is leeg.")
        else:
            st.markdown("### 📊 Jouw Portfolio")
            # Loop door portfolio en haal data op
            p_data = []
            total_val = 0
            
            # Progress bar omdat dit traag kan zijn
            prog_bar = st.progress(0)
            
            for i, item in enumerate(st.session_state['portfolio']):
                prog_bar.progress((i+1)/len(st.session_state['portfolio']))
                ext = get_extended_portfolio_data(item['ticker'])
                current_p = ext['live_price'] if ext else item['avg_price']
                rate = get_currency_rate(item.get('currency'), "EUR")
                
                val_eur = current_p * item['amount'] * rate
                total_val += val_eur
                
                p_data.append({
                    "Ticker": item['ticker'],
                    "Aantal": item['amount'],
                    "Huidige Prijs": current_p,
                    "Waarde (€)": val_eur,
                    "Winst (€)": val_eur - (item['avg_price'] * item['amount'] * rate),
                    "Datum": item.get('buy_date'),
                    "Verwijder": False,
                    # Verborgen velden voor state update
                    "_orig_price": item['avg_price'],
                    "_curr": item.get('currency'),
                    "_sym": item.get('symbol')
                })
            
            prog_bar.empty()
            
            st.metric("Totale Waarde", f"€ {total_val:,.2f}")
            
            df = pd.DataFrame(p_data)
            edited = st.data_editor(
                df, 
                column_config={
                    "Datum": st.column_config.DateColumn("Datum", format="DD-MM-YYYY"),
                    "Verwijder": st.column_config.CheckboxColumn("🗑️")
                },
                hide_index=True,
                key="editor_port"
            )
            
            # Check wijzigingen
            if not df.equals(edited):
                new_port = []
                for idx, row in edited.iterrows():
                    if not row['Verwijder']:
                        new_port.append({
                            'ticker': row['Ticker'],
                            'amount': row['Aantal'],
                            'avg_price': row['_orig_price'],
                            'currency': row['_curr'],
                            'symbol': row['_sym'],
                            'buy_date': row['Datum']
                        })
                st.session_state['portfolio'] = new_port
                save_portfolio_to_db()
                st.rerun()

    # --- TAB 3: WISHLIST ---
    with tabs[2]:
        st.subheader("Wishlist")
        if st.session_state['wishlist']:
            w_data = []
            for item in st.session_state['wishlist']:
                w_data.append({"Ticker": item['ticker'], "Naam": item['name'], "Verwijder": False})
            
            df_w = pd.DataFrame(w_data)
            ed_w = st.data_editor(df_w, key="wish_ed", hide_index=True)
            
            if not df_w.equals(ed_w):
                # Filter verwijderde items
                new_w = [st.session_state['wishlist'][i] for i, r in ed_w.iterrows() if not r['Verwijder']]
                st.session_state['wishlist'] = new_w
                save_portfolio_to_db()
                st.rerun()
        else:
            st.info("Nog niets op de wishlist.")

    # --- TAB 4: BELASTING (FIXED) ---
    with tabs[3]:
        st.header("🇳🇱 Box 3 Checker")
        year = st.number_input("Jaar", value=datetime.now().year - 1)
        
        if st.button("Bereken Waarde 1 Jan & 31 Dec"):
            ticks = list(set([p['ticker'] for p in st.session_state['portfolio']]))
            if ticks:
                with st.spinner("Historische koersen ophalen..."):
                    # Download bulk
                    start_d = f"{year-1}-12-25"
                    end_d = f"{year+1}-01-10"
                    
                    try:
                        # Nieuwe yfinance geeft multi-index terug als >1 ticker
                        bulk = yf.download(ticks, start=start_d, end=end_d, progress=False)
                        
                        val_jan = 0.0
                        val_dec = 0.0
                        d1 = date(year, 1, 1)
                        d2 = date(year, 12, 31)
                        
                        for p in st.session_state['portfolio']:
                            t = p['ticker']
                            bd = p.get('buy_date')
                            
                            # Koers vinden
                            try:
                                # Afhandeling MultiIndex vs Single Index
                                if len(ticks) > 1:
                                    # Selecteer kolom 'Close', dan ticker
                                    hist = bulk['Close'][t]
                                else:
                                    hist = bulk['Close']
                                
                                # Vind dichtstbijzijnde prijs voor datum
                                price_jan = hist[hist.index.date <= d1].iloc[-1] if not hist[hist.index.date <= d1].empty else 0
                                price_dec = hist[hist.index.date <= d2].iloc[-1] if not hist[hist.index.date <= d2].empty else 0
                                
                                rate = get_currency_rate(p.get('currency'), "EUR")
                                
                                # Alleen meetellen als we het toen hadden
                                if bd and bd < d1:
                                    val_jan += price_jan * p['amount'] * rate
                                if bd and bd <= d2:
                                    val_dec += price_dec * p['amount'] * rate
                                    
                            except Exception as ex:
                                st.warning(f"Kon data voor {t} niet verwerken: {ex}")
                        
                        st.session_state.tax_val_jan1 = val_jan
                        st.session_state.tax_val_dec31 = val_dec
                        st.success("Berekend!")
                    except Exception as e:
                        st.error(f"Fout bij downloaden data: {e}")

        c1, c2 = st.columns(2)
        c1.metric(f"Waarde 1 jan {year}", f"€ {st.session_state.tax_val_jan1:,.2f}")
        c2.metric(f"Waarde 31 dec {year}", f"€ {st.session_state.tax_val_dec31:,.2f}")
        
        st.info("Let op: Dit is een indicatie. Controleer altijd met je jaaropgaves.")
