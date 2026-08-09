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
names = ['Eigenaar']
usernames = ['admin']
# Wachtwoord is 'admin'
passwords = ['6f9a2b8c4d1e7f3a9b0c5e8d2f1a4c7b9e0d1f3a6b8c5d2e7f9a1b0c4e8d2f'] 

hashed_passwords = passwords
credentials = {
    'usernames':{
        usernames[0]:{
            'name':names[0],
            'password':hashed_passwords[0]
            }
        }
    }

authenticator = stauth.Authenticate(
    credentials,
    'portfolio_dashboard_cookie', 
    'abcdef', 
    cookie_expiry_days=30
)

# --- AUTOMATISCHE OPSLAG FUNCTIES ---
DB_FILE = 'portfolio_db.json'

def load_portfolio_from_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                data = json.load(f)
                port = data.get('portfolio', [])
                for p in port:
                    if 'buy_date' in p and p['buy_date']:
                        try:
                            p['buy_date'] = datetime.strptime(p['buy_date'], "%Y-%m-%d").date()
                        except:
                            p['buy_date'] = None
                wishlist = data.get('wishlist', [])
                return port, wishlist
        except:
            pass
    return [], []

def save_portfolio_to_db():
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
    with open(DB_FILE, 'w') as f:
        json.dump(data, f)

# --- INLOGGEN ---
authenticator.login(location='main')

if st.session_state["authentication_status"] is False:
    st.error('Gebruikersnaam of wachtwoord is onjuist')
elif st.session_state["authentication_status"] is None:
    st.warning('Voer je inloggegevens in')

elif st.session_state["authentication_status"]:
    
    authenticator.logout('Uitloggen', 'sidebar')
    user_name = st.session_state["name"]
    st.sidebar.write(f'Ingelogd als: *{user_name}*')
    st.sidebar.divider()
    st.sidebar.caption(f"Streamlit versie: {st.__version__}")

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
        "blue": "#457B9D",      
        "green": "#2A9D8F",     
        "red": "#E76F51",       
        "yellow": "#E9C46A",    
        "charcoal": "#264653",  
        "orange": "#F4A261",    
        "purple": "#9B5DE5",    
        "grey": "#A8DADC"       
    }

    SECTOR_COLORS = {
        "Technology": MATTE_COLORS["blue"],
        "Financial Services": MATTE_COLORS["charcoal"],
        "Healthcare": MATTE_COLORS["green"],
        "Consumer Cyclical": MATTE_COLORS["red"],
        "Industrials": MATTE_COLORS["purple"],
        "Consumer Defensive": MATTE_COLORS["orange"],
        "Energy": MATTE_COLORS["yellow"],
        "Communication Services": "#D62828", 
        "Real Estate": "#8D6E63",           
        "Basic Materials": "#5F9EA0",        
        "Utilities": "#76FF03",              
        "ETF/Overig": "#B0BEC5"              
    }

    # ==================================================
    # 2. STATE MANAGEMENT & INITIAL LOAD
    # ==================================================
    if 'portfolio' not in st.session_state:
        p, w = load_portfolio_from_db()
        st.session_state['portfolio'] = p
        st.session_state['wishlist'] = w

    if 'search_results' not in st.session_state:
        st.session_state['search_results'] = []
    
    if 'search_main' not in st.session_state:
        st.session_state['search_main'] = ""
    
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
        if from_curr == to_curr or not from_curr: 
            return 1.0
        try:
            ticker = "EURUSD=X" if from_curr == "USD" else f"{from_curr}{to_curr}=X"
            data = yf.Ticker(ticker).history(period="1d")
            if not data.empty:
                rate = data['Close'].iloc[-1]
                if from_curr == "USD": 
                    return 1.0 / rate 
                return rate
            return 1.0
        except Exception:
            return 1.0

    @st.cache_data(ttl=3600)
    def get_yahoo_earnings_calendar(ticker):
        history = []
        try:
            tk = yf.Ticker(ticker)
            df = tk.earnings_dates
            
            if df is not None and not df.empty:
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                
                current_year = datetime.now().year
                prev_year = current_year - 1
                
                years_in_data = df.index.year
                has_recent_data = any((years_in_data == current_year) | (years_in_data == prev_year))
                
                if not has_recent_data:
                    return None
                
                now = datetime.now()
                df_past = df[df.index < now].dropna(subset=['Reported EPS', 'EPS Estimate'])
                df_past = df_past.sort_index(ascending=False)
                top_4 = df_past.head(4)
                
                for date_idx, row in top_4.iterrows():
                    est = row['EPS Estimate']
                    rep = row['Reported EPS']
                    surp = row['Surprise(%)']
                    
                    if pd.isna(surp) and est != 0:
                        surp = ((rep - est) / abs(est)) * 100
                    elif pd.isna(surp):
                        surp = 0
                        
                    history.append({
                        'date': date_idx.strftime('%Y-%m-%d'),
                        'reported': float(rep),
                        'estimate': float(est),
                        'surprise': float(surp)
                    })
                
                if history:
                    history.reverse() 
                    return history
        except Exception:
            pass
        return None

    @st.cache_data(ttl=3600)
    def get_reliable_earnings(ticker):
        data = get_yahoo_earnings_calendar(ticker)
        if data: return data
        
        us_ticker = EU_TO_US_MAP.get(ticker)
        if us_ticker:
            data = get_yahoo_earnings_calendar(us_ticker)
            if data: return data
            
        return []

    @st.cache_data(ttl=3600)
    def get_extended_portfolio_data(ticker):
        try:
            tk = yf.Ticker(ticker)
            fast_info = tk.fast_info 
            info = tk.info
            
            if not info or ('regularMarketPreviousClose' not in info and 'currentPrice' not in info):
                if fast_info and fast_info.last_price:
                    current_price = fast_info.last_price
                    previous_close = fast_info.previous_close
                    info = {
                        'quoteType': 'EQUITY', 'sector': 'Onbekend', 'country': 'Onbekend',
                        'currencySymbol': '?', 'shortName': ticker, 'longName': ticker
                    }
                else:
                    return None 

            q_type = info.get('quoteType', 'EQUITY').upper()
            if 'ETF' in q_type: q_type = 'ETF'
            else: q_type = 'Aandeel'
            
            country = info.get('country', 'Onbekend')
                
            earnings_history_list = get_reliable_earnings(ticker)
            earnings_surprise = None
            
            if earnings_history_list:
                last = earnings_history_list[-1]
                earnings_surprise = {'date': last['date'], 'surprise_pct': last['surprise']}

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

            ytd_start_price = None
            current_year = datetime.now().year
            try:
                start_of_year = f"{current_year}-01-01"
                hist_ytd = tk.history(start=start_of_year, end=f"{current_year}-01-08", interval="1d")
                if not hist_ytd.empty:
                    ytd_start_price = hist_ytd['Close'].iloc[0]
            except: pass

            current_price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
            previous_close = info.get('regularMarketPreviousClose')
            target_price = info.get('targetMeanPrice')

            if not current_price:
                try: current_price = tk.fast_info.last_price
                except: pass
            
            day_change_pct = 0.0
            if current_price and previous_close:
                day_change_pct = ((current_price - previous_close) / previous_close) * 100

            return {
                'type': q_type,
                'earnings': earnings_surprise,
                'earnings_history': earnings_history_list, 
                'dividend_history': dividend_months,
                'sector': info.get('sector', 'ETF/Overig'),
                'country': country,
                'live_price': current_price,
                'day_change_pct': day_change_pct,
                'target_price': target_price,
                'previous_close': previous_close,
                'ytd_start_price': ytd_start_price,
                'currency_code': info.get('currency', 'EUR'),
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
            url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&lang=en-US&quotesCount=15"
            r = requests.get(url, headers=HEADERS, timeout=3)
            data = r.json()
            results = []
            for q in data.get('quotes', []):
                results.append(f"{q.get('symbol')} | {q.get('shortname') or q.get('longname')}")
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
                items.append({
                    "title": i.find('title').text,
                    "link": i.find('link').text,
                    "date": i.find('pubDate').text[:16]
                })
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
        
    def parse_tr_csv(uploaded_file):
        df = pd.read_csv(uploaded_file)
        
        isin_to_yf = {
            "IE00BK5BQT80": "VWCE.DE", "US0567521085": "BIDU", "US7223041028": "PDD",
            "NL0012015705": "TKWY.AS", "US2546871060": "DIS", "US4581401001": "INTC",
            "US6541061031": "NKE", "US7170811035": "PFE", "US70450Y1038": "PYPL",
            "US8336351056": "SQM", "NL0010273215": "ASML.AS", "IE0003XJA0J9": "WEBN.DE",
            "NL0000009165": "HEIA.AS", "US01609W1027": "BABA", "US7134481081": "PEP",
            "DE0007664005": "VOW3.DE", "US7960508882": "SSUN.F", "US47215P1066": "JD",
            "US4781601046": "JNJ", "DK0062498333": "NOVO-B.CO", "GB00BP6MXD84": "SHELL.AS",
            "US0079031078": "AMD", "US0231351067": "AMZN", "NL0012866412": "BESI.AS",
            "NL0000395903": "WKL.AS", "US02079K3059": "GOOGL", "US68389X1054": "ORCL",
            "US5949181045": "MSFT", "US5324571083": "LLY", "ES0119037010": "CBAV.MC",
            "KYG4124C1096": "GRAB", "US79466L3024": "CRM", "US5504241051": "LAZR", 
            "US5504243032": "LAZR"
        }
        
        portfolio_dict = {}
        
        # INCLUSIEF REVERSE SPLIT EN MIGRATION
        df_trades = df[df['type'].isin(['BUY', 'SELL', 'MIGRATION', 'REVERSE_SPLIT', 'SPLIT']) & df['asset_class'].isin(['STOCK', 'FUND'])]
        
        for _, row in df_trades.iterrows():
            isin = row['symbol']
            
            try: shares_raw = float(row['shares'])
            except: shares_raw = 0.0
                
            try: price = float(row['price'])
            except: price = 0.0
                
            try: date_val = datetime.strptime(str(row['date']).split('T')[0], "%Y-%m-%d").date()
            except: date_val = datetime.now().date()
            
            if isin not in portfolio_dict:
                portfolio_dict[isin] = {'shares': 0.0, 'total_invested': 0.0, 'date': date_val}
            
            if shares_raw > 0:
                portfolio_dict[isin]['shares'] += shares_raw
                portfolio_dict[isin]['total_invested'] += (shares_raw * price)
            elif shares_raw < 0:
                abs_shares = abs(shares_raw)
                old_shares = portfolio_dict[isin]['shares']
                portfolio_dict[isin]['shares'] -= abs_shares
                
                if portfolio_dict[isin]['shares'] < 0:
                    portfolio_dict[isin]['shares'] = 0.0
                    
                if old_shares > 0:
                    portfolio_dict[isin]['total_invested'] -= (abs_shares * (portfolio_dict[isin]['total_invested'] / old_shares))
                else:
                    portfolio_dict[isin]['total_invested'] = 0.0
                    
        new_portfolio = []
        for isin, data in portfolio_dict.items():
            if data['shares'] > 0.01: 
                ticker = isin_to_yf.get(isin, isin)
                avg_price = data['total_invested'] / data['shares'] if data['shares'] > 0 else 0
                
                new_portfolio.append({
                    'ticker': ticker,
                    'amount': data['shares'],
                    'avg_price': avg_price,
                    'currency': 'EUR', 
                    'symbol': '€',
                    'buy_date': data['date']
                })
        return new_portfolio

    # ==================================================
    # 4. DATA ENGINE
    # ==================================================
    class DataEngine:
        def __init__(self, ticker, original_ticker):
            self.ticker = ticker 
            self.original_ticker = original_ticker 
            self.yf_obj = yf.Ticker(original_ticker)
            try:
                self.yf_info = self.yf_obj.info
            except:
                self.yf_info = {}
            self.finviz_data = get_finviz_data(self.ticker) if "." not in self.ticker else {}

        def _normalize_value(self, val, is_percent, is_dividend, is_debt_equity, source_name):
            if val is None: return None
            if is_debt_equity:
                if val > 5.0: return val / 100.0
                return val
            if is_percent:
                if is_dividend: return val
                else:
                    if abs(val) <= 1.0 and val != 0: return val * 100
            return val

        def get_robust_peg(self):
            values = []
            sources = []
            val_yf = self.yf_info.get('pegRatio')
            if val_yf: values.append(float(val_yf)); sources.append("Yahoo")
            val_fin = self.finviz_data.get('peg')
            if val_fin: values.append(float(val_fin)); sources.append("Finviz")
            if not values: return None, "N/A"
            return sum(values) / len(values), " & ".join(sources)

        def get_combined_metric(self, key_yf, key_finviz=None, is_percent=False, is_dividend=False, is_debt_equity=False, use_average=True):
            valid_values = []
            sources = []
            raw_yf = self.yf_info.get(key_yf)
            if raw_yf is not None:
                norm_yf = self._normalize_value(float(raw_yf), is_percent, is_dividend, is_debt_equity, "Yahoo")
                valid_values.append(norm_yf)
                sources.append("Yahoo")
            
            if not use_average and valid_values: return valid_values[0], sources[0]

            if key_finviz:
                raw_fin = self.finviz_data.get(key_finviz)
                if raw_fin is not None:
                    norm_fin = self._normalize_value(float(raw_fin), is_percent, is_dividend, is_debt_equity, "Finviz")
                    valid_values.append(norm_fin)
                    sources.append("Finviz")

            if not valid_values: return None, "N/A"
            if not use_average: return valid_values[0], sources[0]
            
            avg_val = sum(valid_values) / len(valid_values)
            source_str = " & ".join(sources) + (f" (Avg)" if len(sources) > 1 else "")
            return avg_val, source_str

    # ==================================================
    # 5. AI SCORE
    # ==================================================
    def calculate_ai_score(data):
        score = 5.0
        reasons = []
        
        eps_val, _ = data['eps']
        if eps_val is not None:
            if eps_val > 0: score += 1.0; reasons.append("Winstgevend")
            else: score -= 2.5; reasons.append("Verlieslatend")
        
        peg_val, _ = data['peg']
        if peg_val is not None:
            if 0 < peg_val < 1.5: score += 1.0; reasons.append("Goede PEG")
            elif peg_val > 2.5: score -= 0.5
            elif peg_val < 0: score -= 1.0

        pe_val, _ = data['pe']
        if pe_val is not None:
            if 0 < pe_val < 20: score += 0.5
            elif pe_val > 40: score -= 0.5

        de_val, _ = data['de']
        if de_val is not None:
            if de_val < 1.0: score += 1.0; reasons.append("Gezonde Balans")
            elif de_val > 2.0: score -= 1.0; reasons.append("Veel Schulden")

        cr_val, _ = data['cr']
        if cr_val is not None:
            if cr_val > 1.5: score += 0.5
            elif cr_val < 1.0: score -= 0.5; reasons.append("Krap")

        margin_val, _ = data['margin']
        if margin_val is not None:
            if margin_val > 20: score += 1.0; reasons.append("Hoge Marges")
            elif margin_val < 5: score -= 0.5

        roe_val, _ = data['roe']
        if roe_val is not None:
            if roe_val > 15: score += 0.5
        
        div_val, _ = data['div']
        if div_val is not None:
            is_decimal = div_val < 1.0
            threshold = 0.025 if is_decimal else 2.5
            if div_val > threshold: score += 0.5; reasons.append("Mooi Dividend")

        final_score = max(0.0, min(10.0, score))
        summary = "Het bedrijf scoort gemiddeld."
        if final_score >= 7.5: summary = f"Sterk aandeel! Positieve punten: {', '.join(reasons[:3]).lower()}."
        elif final_score <= 4.0: summary = "De fundamentals zien er zwak uit."
        elif reasons: summary = f"Gemengd beeld. Opvallend: {', '.join(reasons[:2]).lower()}."
        
        return round(final_score, 1), summary

    # ==================================================
    # 6. UI: SIDEBAR
    # ==================================================
    st.sidebar.header("📂 Data & Backup")
    st.sidebar.info("💾 Data wordt nu automatisch lokaal opgeslagen.")

    # --- CSV UPLOADER ---
    uploaded_csv = st.sidebar.file_uploader("📈 Importeer Broker CSV", type=['csv'], help="Upload je Trade Republic transactie-export")
    if uploaded_csv and st.sidebar.button("Verwerk CSV"):
        try:
            parsed_portfolio = parse_tr_csv(uploaded_csv)
            if parsed_portfolio:
                st.session_state['portfolio'] = parsed_portfolio
                save_portfolio_to_db()
                st.sidebar.success(f"{len(parsed_portfolio)} posities succesvol ingeladen!")
                time.sleep(1)
                st.rerun()
            else:
                st.sidebar.warning("Geen geldige transacties gevonden in CSV.")
        except Exception as e:
            st.sidebar.error(f"Fout bij verwerken CSV: {e}")

    st.sidebar.divider()

    json_export = json.dumps({'portfolio': st.session_state.get('portfolio',[]), 'wishlist': st.session_state.get('wishlist',[])}, default=str)
    st.sidebar.download_button(
        label="⬇️ Download Backup (JSON)",
        data=json_export,
        file_name="aandelen_backup.json",
        mime="application/json"
    )

    uploaded_file = st.sidebar.file_uploader("⬆️ Upload Backup (JSON)", type=['json'])
    if uploaded_file and st.sidebar.button("📂 Data Inladen / Herstellen"):
        try:
            loaded_data = json.load(uploaded_file)
            if 'portfolio' in loaded_data: 
                port = loaded_data['portfolio']
                for p in port:
                    if 'buy_date' in p and p['buy_date']:
                         p['buy_date'] = datetime.strptime(p['buy_date'], "%Y-%m-%d").date()
                st.session_state['portfolio'] = port
            
            if 'wishlist' in loaded_data: 
                st.session_state['wishlist'] = loaded_data['wishlist']
            elif 'w' in loaded_data: 
                st.session_state['wishlist'] = loaded_data['w']
            
            save_portfolio_to_db() 
            st.toast("Data succesvol geladen!", icon="✅")
            time.sleep(1) 
            st.rerun()
        except Exception as e:
            st.error(f"Fout bij laden bestand: {e}")

    if st.sidebar.button("🧹 Cache Legen (Reset)"):
        st.cache_data.clear()
        st.rerun()

    # ==================================================
    # 7. HOOFDINTERFACE TABS
    # ==================================================
    tabs = st.tabs(["🔎 Analyse", "📊 Portfolio Pro", "❤️ Wishlist", "🇳🇱 Belasting"])

    # --------------------------------------------------
    # TAB 1: ANALYSE
    # --------------------------------------------------
    with tabs[0]:
        def update_search():
            st.session_state.search_main = st.session_state.search_input_widget

        def run_search():
            query = st.session_state.search_main
            if query:
                if "." in query and " " not in query:
                    st.session_state['search_results'] = [query]
                else:
                    st.session_state['search_results'] = search_yahoo_strict(query)
            else:
                st.session_state['search_results'] = []

        col_search, col_period = st.columns([3, 1])
        with col_search:
            st.text_input(
                "Zoek aandeel (Typ en druk op Enter):", 
                key="search_input_widget", 
                value=st.session_state.search_main,
                on_change=update_search
            )
            run_search()

            selected_ticker = None
            if st.session_state['search_results']:
                selection = st.selectbox("Zoekresultaten:", st.session_state['search_results'])
                if selection: selected_ticker = selection.split(" | ")[0]
            elif st.session_state.search_main and "." in st.session_state.search_main:
                selected_ticker = st.session_state.search_main
        
        with col_period:
            chart_period = st.selectbox("Grafiek Periode", ["1mo", "6mo", "1y", "5y", "max"], index=2)

        if selected_ticker:
            try:
                us_equivalent = EU_TO_US_MAP.get(selected_ticker, selected_ticker.split('.')[0] if '.' in selected_ticker else selected_ticker)
                engine = DataEngine(us_equivalent, selected_ticker)
                info = engine.yf_info
                
                metrics = {}
                metrics['peg'] = engine.get_robust_peg()
                metrics['pe'] = engine.get_combined_metric('trailingPE', 'pe', use_average=False)
                metrics['div'] = engine.get_combined_metric('dividendYield', 'div', is_percent=True, is_dividend=True, use_average=False)
                metrics['roe'] = engine.get_combined_metric('returnOnEquity', 'roe', is_percent=True)
                metrics['de'] = engine.get_combined_metric('debtToEquity', 'de', is_debt_equity=True)
                metrics['cr'] = engine.get_combined_metric('currentRatio', None)
                metrics['pb'] = engine.get_combined_metric('priceToBook', None)
                metrics['margin'] = engine.get_combined_metric('profitMargins', 'margin', is_percent=True)
                metrics['eps'] = engine.get_combined_metric('trailingEps', 'eps')
                
                raw_currency = info.get('currency', 'EUR').upper()
                curr_sym = '$' if raw_currency == 'USD' else '€' if raw_currency == 'EUR' else info.get('currencySymbol', '€')
                current_price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
                target_val = info.get('targetMeanPrice') or engine.finviz_data.get('target')
                
                score, summary = calculate_ai_score(metrics)

                with st.container(border=True):
                    h1, h2, h3 = st.columns([2, 1, 1])
                    with h1:
                        st.title(info.get('shortName', selected_ticker))
                        st.caption(f"Ticker: {selected_ticker} | Valuta: {raw_currency}")
                    with h2:
                        st.metric("Huidige Koers", f"{curr_sym} {current_price:.2f}" if current_price else "-")
                    with h3:
                        if target_val and current_price:
                            delta_target = (target_val - current_price) / current_price
                            st.metric("Koersdoel", f"{curr_sym} {target_val:.2f}", f"{delta_target:.1%}")
                        else: st.metric("Koersdoel", "N/A")
                    st.divider()
                    a1, a2 = st.columns([1, 4])
                    with a1:
                        score_col = "green" if score >= 7 else "orange" if score >= 5 else "red"
                        st.markdown(f"<div style='text-align:center; color:{score_col}; font-size:40px; font-weight:bold; border:3px solid {score_col}; border-radius:50%; width:100px; height:100px; line-height:90px; margin:auto'>{score}</div>", unsafe_allow_html=True)
                    with a2:
                        st.subheader("AI Oordeel")
                        st.write(summary)

                st.subheader("📊 Fundamentals")
                def display_metric(label, key, good_fn, bad_fn, fmt="{:.2f}", is_pct=False):
                    val, src = metrics[key]
                    if val is None: st.metric(label, "N/A", help=f"Bron: {src}"); return
                    delta_symbol = "✅" if good_fn(val) else "⚠️" if bad_fn(val) else "⚪"
                    val_display = f"{val:.2f}%" if is_pct else fmt.format(val)
                    st.metric(label, val_display, delta=delta_symbol, delta_color="off", help=f"Waarde: {val:.2f}\nBron: {src}\n{TOOLTIPS.get(key, '')}")

                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns(4)
                    with c1: display_metric("P/E Ratio", 'pe', lambda x: x<20, lambda x: x>35)
                    with c2: display_metric("PEG Ratio", 'peg', lambda x: x<1.5, lambda x: x>2.5)
                    with c3: display_metric("EPS", 'eps', lambda x: x>0, lambda x: x<0)
                    with c4: display_metric("Dividend", 'div', lambda x: x>3 or (x<1 and x>0.03), lambda x: x<1 and x>0.01, is_pct=True)
                    c5, c6, c7, c8 = st.columns(4)
                    with c5: display_metric("ROE", 'roe', lambda x: x>15, lambda x: x<5, is_pct=True)
                    with c6: display_metric("Marge", 'margin', lambda x: x>20, lambda x: x<2, is_pct=True)
                    with c7: display_metric("Debt/Eq", 'de', lambda x: x<1, lambda x: x>2)
                    with c8: display_metric("Current", 'cr', lambda x: x>1.5, lambda x: x<1)

                col_chart, col_news = st.columns([2, 1])
                with col_chart:
                    hist_data = engine.yf_obj.history(period=chart_period)
                    if not hist_data.empty:
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=hist_data.index, 
                            y=hist_data['Close'], 
                            mode='lines', 
                            name='Koers',
                            line=dict(color=MATTE_COLORS["blue"], width=2, shape='spline'),
                            fill='tozeroy',
                            fillcolor='rgba(69, 123, 157, 0.2)'
                        ))
                        fig.update_layout(
                            title=f"Koersverloop ({chart_period})",
                            plot_bgcolor='rgba(0,0,0,0)', 
                            paper_bgcolor='rgba(0,0,0,0)',
                            xaxis=dict(showgrid=False, color='#666'),
                            yaxis=dict(showgrid=False, color='#666'),
                            hovermode='x unified'
                        )
                        st.plotly_chart(fig, use_container_width=True)
                with col_news:
                    st.markdown("##### 📰 Earnings & Nieuws")
                    q_news = f"{info.get('shortName', selected_ticker)} earnings financial results"
                    news_items = get_google_news(q_news)
                    if not news_items:
                        news_items = get_google_news(info.get('shortName', selected_ticker))
                    
                    for item in news_items: st.markdown(f"🔹 [{item['title']}]({item['link']})\n*{item['date']}*")
                    
                    st.markdown("##### 📺 Analyse Video")
                    vid_id = get_youtube_video_id(selected_ticker)
                    if vid_id: st.video(f"https://www.youtube.com/watch?v={vid_id}")

                st.divider()
                col_add, col_wish = st.columns(2)
                with col_add:
                    with st.popover("➕ Toevoegen aan Portfolio", use_container_width=True):
                        st.subheader("Positie Toevoegen")
                        rate = 1.0
                        if raw_currency != "EUR":
                            rate = get_currency_rate(raw_currency, "EUR")
                            st.info(f"Koers: 1 {raw_currency} = € {rate:.4f}")
                            cur_price_eur = (float(current_price) * rate) if current_price else 0.0
                            st.write(f"Prijs: {curr_sym} {current_price} (≈ € {cur_price_eur:.2f})")
                        else: st.write(f"Prijs: € {current_price}")

                        with st.form("add_position_form"):
                            amount_input = st.number_input("Aantal stuks", min_value=0.0001, value=10.0, step=1.0)
                            price_input_eur = st.number_input("Aankoopprijs (€)", value=(float(current_price)*rate) if current_price else 0.0)
                            date_input = st.date_input("Aankoopdatum", value=datetime.now())
                            
                            if st.form_submit_button("Opslaan"):
                                price_native = price_input_eur / rate if rate > 0 else price_input_eur
                                st.session_state['portfolio'].append({
                                    'ticker': selected_ticker,
                                    'amount': amount_input,
                                    'avg_price': price_native,
                                    'currency': raw_currency,
                                    'symbol': curr_sym,
                                    'buy_date': date_input
                                })
                                save_portfolio_to_db()
                                st.success(f"{selected_ticker} toegevoegd!")
                                st.rerun()
                with col_wish:
                    if st.button("❤️ Zet op Wishlist", use_container_width=True):
                        st.session_state['wishlist'].append({'ticker': selected_ticker, 'name': info.get('shortName', selected_ticker)})
                        save_portfolio_to_db()
                        st.toast("Toegevoegd aan Wishlist!")
            except Exception as e:
                st.error(f"Fout: {e}")

    # --------------------------------------------------
    # TAB 2: PORTFOLIO PRO
    # --------------------------------------------------
    with tabs[1]:
        if not st.session_state['portfolio']:
            st.info("Je portfolio is leeg. Voeg aandelen toe via de Analyse tab of upload je CSV in het menu.")
        else:
            st.markdown("### 📊 Portfolio Dashboard Pro")
            
            portfolio_data = []
            monthly_div_projection = {i: 0.0 for i in range(1, 13)}
            sector_distribution = {}
            country_distribution = {}
            
            total_val_eur = 0
            total_cost_eur = 0
            total_div_eur = 0
            total_day_change_eur = 0
            total_year_change_eur = 0
            total_potential_eur = 0

            etf_stats = {'value': 0, 'cost': 0, 'profit': 0}
            stock_stats = {'value': 0, 'cost': 0, 'profit': 0}
            
            failed_tickers = []
            earnings_history_store = {}
            ticker_details_map = {} 

            progress_bar = st.progress(0, text="Portfolio data analyseren...")
            
            current_year_date = date(datetime.now().year, 1, 1)

            for i, item in enumerate(st.session_state['portfolio']):
                ticker = item['ticker']
                progress_bar.progress((i + 1) / len(st.session_state['portfolio']), text=f"Analyseren: {ticker}...")
                
                ext_data = get_extended_portfolio_data(ticker)
                
                data_failed = False
                if ext_data is None:
                    data_failed = True
                    failed_tickers.append(ticker)
                    ext_data = {
                        'live_price': 0, 
                        'target_price': 0,
                        'previous_close': 0, 
                        'ytd_start_price': 0,
                        'type': 'Onbekend', 
                        'sector': 'Fout',
                        'country': 'Onbekend',
                        'currency_symbol': item.get('symbol', '?'),
                        'shortName': f"⚠️ {ticker} (Fout)", 
                        'longName': ticker,
                        'earnings': None, 
                        'dividend_history': []
                    }

                live_price = ext_data.get('live_price', 0)
                target_price = ext_data.get('target_price') 
                if live_price is None: live_price = 0
                
                q_type = ext_data.get('type', 'Aandeel')
                sector = ext_data.get('sector', 'Onbekend')
                country = ext_data.get('country', 'Onbekend')
                short_name = ext_data.get('shortName', ticker)
                long_name = ext_data.get('longName', ticker)
                
                amount = item['amount']
                avg_price = item['avg_price']
                
                currency_code = ext_data.get('currency_code', 'EUR')
                symbol = ext_data.get('currency_symbol', '€')
                buy_date = item.get('buy_date', None)

                eur_rate = get_currency_rate(currency_code, "EUR")
                
                val_native = live_price * amount
                val_eur = val_native * eur_rate
                
                cost_eur = avg_price * amount
                profit_eur = val_eur - cost_eur
                
                used_target = target_price if target_price else live_price
                potential_eur = used_target * amount * eur_rate
                total_potential_eur += potential_eur
                
                gak_eur = avg_price 

                total_val_eur += val_eur
                total_cost_eur += cost_eur
                
                if not data_failed:
                    if ext_data.get('previous_close'):
                        total_day_change_eur += (live_price - ext_data['previous_close']) * amount * eur_rate
                    
                    ytd_ref_price = ext_data.get('ytd_start_price')
                    
                    if buy_date and buy_date >= current_year_date:
                        ytd_ref_price = avg_price 
                    
                    if ytd_ref_price and ytd_ref_price > 0:
                        total_year_change_eur += (live_price - ytd_ref_price) * amount * eur_rate

                yearly_div_eur = 0
                if ext_data:
                    if ext_data.get('dividend_history'):
                        for div_pay in ext_data['dividend_history']:
                            payout_eur = div_pay['amount'] * amount * eur_rate
                            monthly_div_projection[div_pay['month']] += payout_eur
                            yearly_div_eur += payout_eur
                    elif ext_data.get('fallback_div_rate'):
                        yearly_div_eur = ext_data['fallback_div_rate'] * amount * eur_rate
                    elif ext_data.get('fallback_div_yield') and ext_data['fallback_div_yield'] > 0:
                        yearly_div_eur = (live_price * ext_data['fallback_div_yield']) * amount * eur_rate
                
                total_div_eur += yearly_div_eur
                
                sector_distribution[sector] = sector_distribution.get(sector, 0) + val_eur
                country_distribution[country] = country_distribution.get(country, 0) + val_eur

                if q_type == 'ETF':
                    etf_stats['value'] += val_eur
                    etf_stats['cost'] += cost_eur
                    etf_stats['profit'] += profit_eur
                else:
                    stock_stats['value'] += val_eur
                    stock_stats['cost'] += cost_eur
                    stock_stats['profit'] += profit_eur

                earnings_str = "-"
                earnings_color = "black"
                if ext_data:
                    if ext_data.get('earnings_history'):
                        earnings_history_store[ticker] = ext_data['earnings_history']
                    
                    if ext_data.get('earnings'):
                        surp = ext_data['earnings']['surprise_pct']
                        if surp > 0:
                            earnings_str = f"Beat: +{surp:.1f}%"
                            earnings_color = MATTE_COLORS["green"]
                        else:
                            earnings_str = f"Miss: {surp:.1f}%"
                            earnings_color = MATTE_COLORS["red"]
                
                display_price = live_price
                if data_failed:
                    display_price = 0.0
                    earnings_str = "⚠️ ERROR"

                ticker_details_map[ticker] = {
                    'shortName': short_name,
                    'longName': long_name
                }

                portfolio_data.append({
                    "Ticker": ticker,
                    "Naam": short_name,
                    "Type": q_type,
                    "Aantal": float(amount),
                    "GAK": float(avg_price),
                    "GAK (€)": float(gak_eur),
                    "Prijs": display_price,
                    "Waarde (€)": val_eur,
                    "Winst (€)": profit_eur,
                    "Div/Jaar (€)": yearly_div_eur,
                    "Datum": buy_date,
                    "Earnings": earnings_str,
                    "_earnings_color": earnings_color,
                    "Sector": sector,
                    "Valuta": symbol,
                    "_currency_code": currency_code,
                    "_rate": eur_rate, 
                    "Verwijder": False
                })
                
            progress_bar.empty()
            
            if failed_tickers:
                st.warning(f"⚠️ Yahoo kon data niet ophalen voor: {', '.join(failed_tickers)}. Waarde tijdelijk op €0.")

            for p in portfolio_data:
                p['Weging'] = (p['Waarde (€)'] / total_val_eur * 100) if total_val_eur > 0 else 0

            portfolio_data.sort(key=lambda x: x["Waarde (€)"], reverse=True)

            with st.expander("📈 Portfolio Overzicht & Sectoren", expanded=True):
                k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
                abs_profit = total_val_eur - total_cost_eur
                pct_profit = (abs_profit / total_cost_eur * 100) if total_cost_eur else 0
                yield_pct = (total_div_eur / total_val_eur * 100) if total_val_eur else 0
                
                k1.metric("Totale Waarde", f"€ {total_val_eur:,.2f}")
                k2.metric("Potentiële Waarde", f"€ {total_potential_eur:,.2f}", help="Op basis van koersdoelen analisten")
                k3.metric("Totaal Winst", f"€ {abs_profit:,.2f}", delta=f"{pct_profit:.1f}%")
                k4.metric("Dag Resultaat", f"€ {total_day_change_eur:,.2f}", delta="Vandaag")
                
                ytd_pct = (total_year_change_eur / (total_val_eur - total_year_change_eur) * 100) if (total_val_eur - total_year_change_eur) > 0 else 0.0
                k5.metric("Jaar Resultaat (YTD)", f"€ {total_year_change_eur:,.2f}", delta=f"{ytd_pct:.2f}%")
                
                k6.metric("Verwacht Dividend", f"€ {total_div_eur:,.2f}", delta="Jaarbasis")
                k7.metric("Portfolio Yield", f"{yield_pct:.2f}%")
                
                st.divider()
                
                st.markdown("### 📊 Rendement: ETF's vs Aandelen")
                col_etf, col_stock = st.columns(2)
                
                etf_pct = (etf_stats['profit'] / etf_stats['cost'] * 100) if etf_stats['cost'] > 0 else 0
                stock_pct = (stock_stats['profit'] / stock_stats['cost'] * 100) if stock_stats['cost'] > 0 else 0
                
                with col_etf:
                    st.metric("ETF Rendement (Absoluut)", f"€ {etf_stats['profit']:,.2f}", f"{etf_pct:.1f}%")
                with col_stock:
                    st.metric("Aandelen Rendement (Absoluut)", f"€ {stock_stats['profit']:,.2f}", f"{stock_pct:.1f}%")
                    
                st.divider()

                comp1, comp2, comp3 = st.columns(3)
                with comp1:
                    st.markdown("**Verdeling: Aandelen vs ETF's**")
                    fig_d1 = go.Figure(data=[go.Pie(
                        labels=['ETF', 'Aandelen'], 
                        values=[etf_stats['value'], stock_stats['value']], 
                        hole=.6, 
                        textposition='inside',
                        textinfo='percent'
                    )])
                    fig_d1.update_traces(marker=dict(colors=[MATTE_COLORS["blue"], MATTE_COLORS["red"]]))
                    fig_d1.update_layout(
                        showlegend=True,
                        legend=dict(orientation="h", y=-0.1), 
                        height=200, 
                        margin=dict(t=0, b=0, l=0, r=0),
                        plot_bgcolor='rgba(0,0,0,0)', 
                        paper_bgcolor='rgba(0,0,0,0)'
                    )
                    st.plotly_chart(fig_d1, use_container_width=True)

                with comp2:
                    st.markdown("**Sectoren**")
                    if sector_distribution:
                        fig_d2 = go.Figure(data=[go.Pie(
                            labels=list(sector_distribution.keys()), 
                            values=list(sector_distribution.values()), 
                            hole=.6,
                            textposition='inside',
                            textinfo='percent'
                        )])
                        fig_d2.update_traces(marker=dict(colors=[SECTOR_COLORS.get(k, '#ccc') for k in sector_distribution.keys()]))
                        fig_d2.update_layout(
                            showlegend=True,
                            legend=dict(orientation="h", y=-0.1),
                            height=200, 
                            margin=dict(t=0, b=0, l=0, r=0),
                            plot_bgcolor='rgba(0,0,0,0)', 
                            paper_bgcolor='rgba(0,0,0,0)'
                        )
                        st.plotly_chart(fig_d2, use_container_width=True)
                
                with comp3:
                    st.markdown("**Landenverdeling**")
                    if country_distribution:
                        fig_d3 = go.Figure(data=[go.Pie(
                            labels=list(country_distribution.keys()), 
                            values=list(country_distribution.values()), 
                            hole=.6,
                            textposition='inside',
                            textinfo='percent'
                        )])
                        fig_d3.update_layout(
                            showlegend=True,
                            legend=dict(orientation="h", y=-0.1),
                            height=200, 
                            margin=dict(t=0, b=0, l=0, r=0),
                            plot_bgcolor='rgba(0,0,0,0)', 
                            paper_bgcolor='rgba(0,0,0,0)'
                        )
                        st.plotly_chart(fig_d3, use_container_width=True)

            with st.expander("💰 Dividend Dashboard", expanded=False):
                col_div1, col_div2 = st.columns(2)
                with col_div1:
                    monthly_vals = [monthly_div_projection[i] for i in range(1, 13)]
                    if sum(monthly_vals) > 0:
                        st.markdown("**Maandelijks Dividend Projectie**")
                        months = ["Jan", "Feb", "Mrt", "Apr", "Mei", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]
                        fig_bar = go.Figure([go.Bar(x=months, y=monthly_vals, marker_color=MATTE_COLORS["green"])])
                        fig_bar.update_layout(
                            height=300, 
                            margin=dict(t=10, b=10, l=0, r=0), 
                            plot_bgcolor='rgba(0,0,0,0)', 
                            paper_bgcolor='rgba(0,0,0,0)',
                            xaxis=dict(showgrid=False),
                            yaxis=dict(showgrid=True, gridcolor='#333')
                        )
                        st.plotly_chart(fig_bar, use_container_width=True)
                    else:
                        st.info("Geen gedetailleerde uitbetalingsdata beschikbaar.")
                with col_div2:
                    st.markdown("**Dividend per Positie**")
                    df_charts = pd.DataFrame(portfolio_data)
                    if not df_charts.empty:
                        df_charts = df_charts.sort_values(by="Div/Jaar (€)", ascending=False)
                        fig_bar = px.bar(df_charts, x='Ticker', y='Div/Jaar (€)', color='Sector', color_discrete_map=SECTOR_COLORS)
                        fig_bar.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', height=300)
                        st.plotly_chart(fig_bar, use_container_width=True)

            with st.expander("📋 Aandelen & Details", expanded=True):
                st.info("💡 Bewerk 'GAK (€)', 'Aantal' of 'Datum' in de tabel. Selecteer een aandeel daaronder voor details.")
                
                df_editor_source = pd.DataFrame(portfolio_data)
                
                column_settings = {
                    "Ticker": st.column_config.TextColumn("Ticker", disabled=True),
                    "Naam": st.column_config.TextColumn("Naam", disabled=True),
                    "Type": st.column_config.TextColumn("Type", disabled=True),
                    "Aantal": st.column_config.NumberColumn("Aantal", min_value=0.0001, required=True, step=1.0),
                    "GAK": st.column_config.NumberColumn("GAK (Origineel)", format="%.2f", disabled=True),
                    "GAK (€)": st.column_config.NumberColumn("GAK (€)", format="€ %.2f", disabled=False),
                    "Waarde (€)": st.column_config.NumberColumn("Waarde (€)", format="€ %.2f", disabled=True),
                    "Winst (€)": st.column_config.NumberColumn("Winst (€)", format="€ %.2f", disabled=True),
                    "Div/Jaar (€)": st.column_config.NumberColumn("Div/Jaar (€)", format="€ %.2f", disabled=True),
                    "Datum": st.column_config.DateColumn("Datum", format="DD-MM-YYYY", min_value=date(1980, 1, 1), max_value=date(2050, 12, 31)),
                    "Weging": st.column_config.ProgressColumn("Weging", format="%.1f%%", min_value=0, max_value=100),
                    "Earnings": st.column_config.TextColumn("Earnings", disabled=True),
                    "Verwijder": st.column_config.CheckboxColumn("🗑️", default=False)
                }
                
                edited_df = st.data_editor(
                    df_editor_source, 
                    column_config=column_settings, 
                    column_order=("Verwijder", "Ticker", "Naam", "Type", "Aantal", "GAK", "GAK (€)", "Datum", "Waarde (€)", "Winst (€)", "Weging", "Div/Jaar (€)", "Earnings"), 
                    hide_index=True, 
                    use_container_width=True, 
                    key="main_portfolio_editor"
                )

                if not df_editor_source.equals(edited_df):
                    new_state_portfolio = []
                    for index, row in edited_df.iterrows():
                        if not row['Verwijder']:
                            orig_row = df_editor_source.iloc[index] if index in df_editor_source.index else row
                            new_gak_eur = row['GAK (€)']
                            rate = row['_rate']
                            
                            if abs(new_gak_eur - orig_row['GAK (€)']) > 0.001:
                                new_avg_price = new_gak_eur / rate if rate > 0 else new_gak_eur
                            else:
                                new_avg_price = row['GAK']

                            new_state_portfolio.append({
                                'ticker': row['Ticker'], 
                                'amount': row['Aantal'], 
                                'avg_price': new_avg_price, 
                                'currency': row['_currency_code'], 
                                'symbol': row['Valuta'],
                                'buy_date': row['Datum']
                            })
                    st.session_state['portfolio'] = new_state_portfolio
                    save_portfolio_to_db()
                    st.rerun()

                st.divider()
                st.subheader("🔎 Details per Aandeel")
                
                available_tickers = [item['Ticker'] for item in portfolio_data]
                ticker_to_name = {item['Ticker']: item['Naam'] for item in portfolio_data}
                
                if available_tickers:
                    selected_ticker = st.selectbox(
                        "Selecteer een aandeel:", 
                        available_tickers,
                        format_func=lambda x: f"{ticker_to_name.get(x, x)} ({x})"
                    )
                    
                    if selected_ticker:
                        sel_info = ticker_details_map.get(selected_ticker, {})
                        long_name = sel_info.get('longName', selected_ticker)
                        
                        if selected_ticker in earnings_history_store:
                            st.markdown(f"**Earnings Surprise: {long_name}**")
                            st.caption(f"Bron: Yahoo Finance")
                            
                            hist_data = earnings_history_store[selected_ticker]
                            df_eps = pd.DataFrame(hist_data)
                            
                            fig_eps = go.Figure()
                            
                            fig_eps.add_trace(go.Scatter(
                                x=df_eps['date'], 
                                y=df_eps['estimate'], 
                                name='Verwacht', 
                                mode='markers',
                                marker=dict(color=MATTE_COLORS["grey"], size=15, symbol='circle')
                            ))
                            
                            fig_eps.add_trace(go.Scatter(
                                x=df_eps['date'], 
                                y=df_eps['reported'], 
                                name='Werkelijk', 
                                mode='markers',
                                marker=dict(color=MATTE_COLORS["blue"], size=15, symbol='circle')
                            ))
                            
                            fig_eps.update_layout(
                                xaxis_title="Datum", 
                                yaxis_title="EPS", 
                                height=350, 
                                margin=dict(t=30, b=0, l=0, r=0),
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                                plot_bgcolor='rgba(0,0,0,0)', 
                                paper_bgcolor='rgba(0,0,0,0)'
                            )
                            fig_eps.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#444')
                            fig_eps.update_xaxes(showgrid=False)
                            st.plotly_chart(fig_eps, use_container_width=True)
                            
                            latest = df_eps.iloc[-1]
                            est = latest['estimate']
                            rep = latest['reported']
                            diff = rep - est
                            pct = latest['surprise']
                            
                            m1, m2, m3, m4 = st.columns(4)
                            
                            m1.metric("Verwacht EPS", f"{est:.2f}")
                            m2.metric("Werkelijk EPS", f"{rep:.2f}")
                            m3.metric("Verschil", f"{diff:+.2f}")
                            
                            color_pct = MATTE_COLORS["green"] if pct > 0 else MATTE_COLORS["red"]
                            m4.markdown(
                                f"""
                                <div style="font-family: sans-serif;">
                                    <div style="font-size: 14px; color: rgb(49, 51, 63); margin-bottom: 4px;">Surprise %</div>
                                    <div style="font-size: 32px; font-weight: 600; color: {color_pct};">{pct:+.1f}%</div>
                                </div>
                                """, 
                                unsafe_allow_html=True
                            )

                        else:
                            st.info("Geen earnings informatie beschikbaar voor het huidige jaar.")
                        
                        st.divider()
                        
                        st.markdown(f"**Laatste Nieuws: {long_name}**")
                        query = f"{long_name} earnings stock news"
                        news = get_google_news(query)
                        if news:
                            for n in news:
                                st.markdown(f"🔹 [{n['title']}]({n['link']})\n*{n['date']}*")
                        else:
                            st.write("Geen specifiek nieuws gevonden.")

            with st.expander("🧮 Rente-op-Rente Calculator", expanded=False):
                col_cal1, col_cal2 = st.columns([1, 2])
                with col_cal1:
                    st.write("**Scenario Instellingen**")
                    start_cap = st.number_input("Startkapitaal (Huidig Portfolio)", value=float(round(total_val_eur, 2)))
                    mnd_inleg = st.number_input("Maandelijkse Inleg (€)", value=500)
                    jaren = st.slider("Aantal Jaren", 1, 40, 20)
                    rendement = st.slider("Verwacht Rendement (%)", 1, 15, 7) / 100
                with col_cal2:
                    years_range = range(1, jaren + 1)
                    values_list = []
                    invested_list = []
                    current_balance = start_cap
                    total_invested = start_cap
                    for _ in years_range:
                        for _ in range(12):
                            current_balance = current_balance * (1 + rendement/12) + mnd_inleg
                            total_invested += mnd_inleg
                        values_list.append(current_balance)
                        invested_list.append(total_invested)
                    winst_rente = current_balance - total_invested
                    mc1, mc2 = st.columns(2)
                    mc1.metric(f"Waarde over {jaren} jaar", f"€ {current_balance:,.0f}")
                    mc2.metric("Rente Winst", f"€ {winst_rente:,.0f}", delta="Gratis Geld")
                    df_comp = pd.DataFrame({"Jaar": years_range, "Portfolio Waarde": values_list, "Eigen Inleg": invested_list})
                    fig_comp = px.line(df_comp, x="Jaar", y=["Portfolio Waarde", "Eigen Inleg"], title="Vermogensgroei Projectie")
                    st.plotly_chart(fig_comp, use_container_width=True)

            if st.button("⚠️ Portfolio Volledig Wissen"):
                st.session_state['portfolio'] = []
                save_portfolio_to_db()
                st.rerun()

    # --------------------------------------------------
    # TAB 3: WISHLIST 
    # --------------------------------------------------
    with tabs[2]:
        st.subheader("❤️ Mijn Wishlist")
        if st.session_state['wishlist']:
            st.info("💡 Hier zie je je favoriete aandelen.")
            
            wishlist_data = []
            for item in st.session_state['wishlist']:
                ticker = item['ticker']
                name = item['name']
                
                w_data = get_extended_portfolio_data(ticker)
                
                score_str = "N/A"
                price = 0.0
                day_change = 0.0
                
                if w_data:
                    us_equivalent = EU_TO_US_MAP.get(ticker, ticker)
                    engine = DataEngine(us_equivalent, ticker)
                    metrics = {}
                    metrics['peg'] = engine.get_robust_peg()
                    metrics['pe'] = engine.get_combined_metric('trailingPE', 'pe', use_average=False)
                    metrics['div'] = engine.get_combined_metric('dividendYield', 'div', is_percent=True, is_dividend=True, use_average=False)
                    metrics['roe'] = engine.get_combined_metric('returnOnEquity', 'roe', is_percent=True)
                    metrics['de'] = engine.get_combined_metric('debtToEquity', 'de', is_debt_equity=True)
                    metrics['cr'] = engine.get_combined_metric('currentRatio', None)
                    metrics['margin'] = engine.get_combined_metric('profitMargins', 'margin', is_percent=True)
                    metrics['eps'] = engine.get_combined_metric('trailingEps', 'eps')
                    
                    score, _ = calculate_ai_score(metrics)
                    score_str = str(score)
                    price = w_data.get('live_price', 0) or 0.0
                    day_change = w_data.get('day_change_pct', 0) or 0.0

                wishlist_data.append({
                    "Verwijder": False,
                    "Ticker": ticker,
                    "Naam": name,
                    "Huidige Koers": price,
                    "Dag %": day_change, 
                    "AI Score": score_str
                })
            
            df_wishlist = pd.DataFrame(wishlist_data)
            
            col_config = {
                "Verwijder": st.column_config.CheckboxColumn("🗑️", default=False),
                "Ticker": st.column_config.TextColumn("Ticker", disabled=True),
                "Naam": st.column_config.TextColumn("Naam", disabled=True),
                "Huidige Koers": st.column_config.NumberColumn("Koers", format="%.2f", disabled=True),
                "Dag %": st.column_config.NumberColumn("Dag %", format="%.2f %%", disabled=True),
                "AI Score": st.column_config.TextColumn("AI Score", disabled=True)
            }
            
            edited_wishlist = st.data_editor(
                df_wishlist,
                column_config=col_config,
                hide_index=True,
                use_container_width=True,
                key="wishlist_editor"
            )
            
            if not df_wishlist.equals(edited_wishlist):
                new_wishlist_state = []
                for idx, row in edited_wishlist.iterrows():
                    if not row['Verwijder']:
                        if idx < len(st.session_state['wishlist']):
                             new_wishlist_state.append(st.session_state['wishlist'][idx])
                
                if len(new_wishlist_state) != len(st.session_state['wishlist']):
                    st.session_state['wishlist'] = new_wishlist_state
                    save_portfolio_to_db()
                    st.rerun()
                
        else: st.info("Je wishlist is nog leeg.")

    # --------------------------------------------------
    # TAB 4: BELASTING 
    # --------------------------------------------------
    with tabs[3]:
        st.header("🇳🇱 Box 3 Belasting Checker")
        st.info("Vul handmatig in of laat het script een schatting maken op basis van je portfolio.")

        c_inp1, c_inp2 = st.columns(2)
        
        with c_inp1:
            tax_year = st.number_input("Belastingjaar", min_value=2020, max_value=2030, value=datetime.now().year - 1)
            savings_amount = st.number_input("Spaargeld op 1 januari (€)", value=10000.0, step=1000.0)
            savings_yield_pct = st.number_input("Rendement op spaargeld (%)", value=1.03, step=0.01)

        with c_inp2:
            st.markdown(f"**Portfolio Waarde (Aandelen)**")
            
            if st.button("🔄 Bereken op basis van portfolio"):
                calc_jan1 = 0.0
                calc_dec31 = 0.0
                d_jan1 = date(tax_year, 1, 1)
                d_dec31 = date(tax_year, 12, 31)
                
                tickers_list = [p['ticker'] for p in st.session_state['portfolio']]
                
                if tickers_list:
                    with st.spinner("Koersen ophalen (Bulk)..."):
                        start_dl = date(tax_year - 1, 12, 15)
                        end_dl = date(tax_year + 1, 1, 15)
                        
                        try:
                            bulk_data = yf.download(tickers_list, start=start_dl, end=end_dl, group_by='ticker', progress=False)
                            
                            for p in st.session_state['portfolio']:
                                ticker = p['ticker']
                                amount = p['amount']
                                buy_date = p.get('buy_date')
                                currency = p.get('currency', 'EUR')
                                rate = get_currency_rate(currency, "EUR")

                                def get_price_from_bulk(t, d):
                                    try:
                                        if len(tickers_list) == 1:
                                            df_t = bulk_data
                                        else:
                                            df_t = bulk_data[t]
                                        
                                        valid_rows = df_t[df_t.index.date <= d]
                                        
                                        if not valid_rows.empty:
                                            return float(valid_rows['Close'].iloc[-1])
                                    except:
                                        pass
                                    return 0.0

                                if buy_date and buy_date < d_jan1:
                                    calc_jan1 += (get_price_from_bulk(ticker, d_jan1) * amount * rate)
                                
                                if buy_date and buy_date <= d_dec31:
                                    calc_dec31 += (get_price_from_bulk(ticker, d_dec31) * amount * rate)
                                    
                        except Exception as e:
                            st.error(f"Fout bij ophalen koersen: {e}")

                st.session_state.tax_val_jan1 = calc_jan1
                st.session_state.tax_val_dec31 = calc_dec31
                st.success("Waardes bijgewerkt!")
                st.rerun()

            pf_val_jan1 = st.number_input(f"Waarde 1 jan {tax_year}", value=st.session_state.tax_val_jan1, step=100.0)
            pf_val_dec31 = st.number_input(f"Waarde 31 dec {tax_year}", value=st.session_state.tax_val_dec31, step=100.0)
            
            st.session_state.tax_val_jan1 = pf_val_jan1
            st.session_state.tax_val_dec31 = pf_val_dec31

            heffingsvrij = st.number_input("Heffingsvrij vermogen (€)", value=57000.0)
            belasting_tarief = st.number_input("Belastingtarief Box 3 (%)", value=36.0, step=0.1)
            rendement_pct_input = st.number_input("Rendement voor Belasting (% - Forfaitair of Werkelijk)", value=6.04, step=0.01)

        st.divider()
        
        stock_gain = pf_val_dec31 - pf_val_jan1
        stock_return_pct = (stock_gain / pf_val_jan1 * 100) if pf_val_jan1 > 0 else 0.0
        
        savings_gain = savings_amount * (savings_yield_pct / 100)
        
        total_assets_start = savings_amount + pf_val_jan1
        total_gain = stock_gain + savings_gain
        total_return_pct = (total_gain / total_assets_start * 100) if total_assets_start > 0 else 0.0

        grondslag = max(0, total_assets_start - heffingsvrij)
        belastbaar_rendement = grondslag * (rendement_pct_input / 100)
        te_betalen = belastbaar_rendement * (belasting_tarief / 100)
        
        st.subheader("📊 Belastingberekening")
        
        col_res1, col_res2, col_res3 = st.columns(3)
        col_res1.metric("Totaal Vermogen 1 jan", f"€ {total_assets_start:,.2f}")
        col_res2.metric("Werkelijk Behaald Rendement", f"€ {total_gain:,.2f}", f"{total_return_pct:.2f}%", help="Dit is wat je daadwerkelijk hebt verdiend (koerswinst + spaarrente).")
        
        col_res3.metric("Belastbaar Bedrag", f"€ {belastbaar_rendement:,.2f}", help=f"Berekend als: (Vermogen - Heffingsvrij) * {rendement_pct_input}%")
        
        st.divider()
        final_c1, final_c2 = st.columns(2)
        final_c1.info(f"**Werkelijk resultaat:**\nRendement Spaargeld: € {savings_gain:.2f}\nRendement Beleggingen: € {stock_gain:.2f}")
        
        final_c2.markdown(f"""
        ### 💶 Te Betalen: € {te_betalen:,.2f}
        *Berekend als: Belastbaar Bedrag (€ {belastbaar_rendement:,.2f}) × {belasting_tarief}%*
        """)
