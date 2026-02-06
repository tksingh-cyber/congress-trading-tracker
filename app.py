import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import yfinance as yf
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

st.set_page_config(page_title="Congress Trading Tracker", layout="wide", page_icon="🏛️")

API_KEY = st.secrets.get("QUIVER_API_KEY", "3e58cb4de846a54998b70a3775f6cff2f25ead56")
EMAIL_USER = st.secrets.get("EMAIL_USER", "")
EMAIL_PASS = st.secrets.get("EMAIL_PASS", "")
EMAIL_TO = st.secrets.get("EMAIL_TO", "")
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}

def send_email_alert(df):
    if df.empty or not EMAIL_USER: return False
    msg = MIMEMultipart()
    msg["From"], msg["To"], msg["Subject"] = EMAIL_USER, EMAIL_TO, "🔥 Congress Alerts"
    msg.attach(MIMEText(f"<h2>Alerts</h2>{df.to_html(index=False)}", "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        return True
    except: return False

def ultimate_backtest(ticker, disclosure_date):
    try:
        start = pd.to_datetime(disclosure_date)
        prices = yf.download(ticker, start=start, end=start+timedelta(days=370), progress=False)
        if prices.empty: return None
        entry = float(prices.iloc[0]["Close"])
        results = {"entry_price": round(entry, 2)}
        for days in [7,14,30,60,90,180,365]:
            idx = min(days, len(prices)-1)
            results[f"return_{days}d"] = round((float(prices.iloc[idx]["Close"]) - entry) / entry * 100, 2)
        results["max_gain_%"] = round((float(prices["Close"].max()) - entry) / entry * 100, 2)
        results["max_drawdown_%"] = round((float(prices["Close"].min()) - entry) / entry * 100, 2)
        return results
    except: return None

def politician_performance_analysis(bt_df):
    for col in ["return_90d", "return_180d", "return_365d", "max_gain_%", "max_drawdown_%"]:
        bt_df[col] = pd.to_numeric(bt_df[col], errors='coerce')
    stats = bt_df.groupby("Politician").agg({
        "return_90d":["mean","median","count"],
        "return_180d":["mean","median"],
        "return_365d":["mean","median"],
        "max_gain_%":"max",
        "max_drawdown_%":"min"
    }).round(2)
    stats.columns = ['_'.join(c).strip() for c in stats.columns.values]
    stats["skill_score"] = (stats["return_90d_mean"]*0.2 + stats["return_180d_mean"]*0.4 + stats["return_365d_mean"]*0.3 + stats["max_gain_%_max"]*0.1).round(2)
    stats["win_rate_%"] = bt_df.groupby("Politician").apply(lambda x: (x["return_90d"]>0).sum()/len(x)*100 if len(x)>0 else 0).round(1)
    stats["total_trades"] = stats["return_90d_count"]
    return stats.sort_values("skill_score", ascending=False)

@st.cache_data(ttl=3600)
def load_and_process_data():
    house = requests.get("https://api.quiverquant.com/beta/live/congresstrading", headers=HEADERS, timeout=30)
    senate = requests.get("https://api.quiverquant.com/beta/live/senatetrading", headers=HEADERS, timeout=30)
    if house.status_code!=200 or senate.status_code!=200: st.error("API Error"); st.stop()
    df = pd.concat([pd.DataFrame(house.json()).assign(Chamber="House"), pd.DataFrame(senate.json()).assign(Chamber="Senate")])
    df = df[["Representative","Chamber","Party","Ticker","Transaction","Range","TransactionDate","ReportDate"]].copy()
    df.rename(columns={"Representative":"Politician","Transaction":"transaction_type","Range":"amount_range","ReportDate":"DisclosureDate"}, inplace=True)
    df["DisclosureDate"] = pd.to_datetime(df["DisclosureDate"], errors="coerce")
    df = df.dropna(subset=["Ticker","DisclosureDate"])
    df = df[df["DisclosureDate"]<datetime.now()]
    df = df[df["Ticker"]!=""]
    
    # Calculate signal strength for ALL trades
    def calc_signal_strength(r):
        score = 0
        if r["transaction_type"]=="Purchase": score += 2
        if "$50,000" in str(r["amount_range"]) or "$100,000" in str(r["amount_range"]): score += 2
        if r["Chamber"]=="Senate": score += 1
        if score >= 4: return "HIGH"
        if score >= 2: return "MEDIUM"
        return "LOW"
    
    df["signal_strength"] = df.apply(calc_signal_strength, axis=1)
    
    # Backtest purchases only
    purchases = df[df["transaction_type"]=="Purchase"]
    st.info(f"🔄 Backtesting {len(purchases)} trades...")
    rows, progress_bar, status_text = [], st.progress(0), st.empty()
    for idx,(i,row) in enumerate(purchases.iterrows()):
        res = ultimate_backtest(row["Ticker"], row["DisclosureDate"])
        if res: rows.append({**row.to_dict(), **res})
        if idx%10==0: progress_bar.progress((idx+1)/len(purchases)); status_text.text(f"{idx+1}/{len(purchases)}")
    progress_bar.empty(); status_text.empty()
    bt_df = pd.DataFrame(rows)
    if len(bt_df)==0: st.error("No trades"); st.stop()
    politician_rank = politician_performance_analysis(bt_df)
    bt_df = bt_df.merge(politician_rank[["skill_score"]], left_on="Politician", right_index=True, how="left")
    
    # Calculate final signal for backtested trades
    bt_df["final_signal"] = bt_df.apply(lambda r: "STRONG BUY" if (3 if r["signal_strength"]=="HIGH" else 0)+(2 if pd.notna(r.get("skill_score")) and r["skill_score"]>30 else 0)+(2 if pd.notna(r.get("return_90d")) and r["return_90d"]>15 else 0)>=5 else ("BUY" if (3 if r["signal_strength"]=="HIGH" else 0)+(2 if pd.notna(r.get("skill_score")) and r["skill_score"]>30 else 0)>=4 else ("WATCH" if (3 if r["signal_strength"]=="HIGH" else 0)>=2 else "IGNORE")), axis=1)
    
    # Add basic final_signal to all trades based on signal_strength
    df["final_signal"] = df.apply(lambda r: "BUY" if r["signal_strength"]=="HIGH" and r["transaction_type"]=="Purchase" else ("WATCH" if r["signal_strength"]=="MEDIUM" and r["transaction_type"]=="Purchase" else "IGNORE"), axis=1)
    
    return df, bt_df, politician_rank

st.title("🏛️ Congress Trading Intelligence")
st.markdown("### Live tracking with ultimate backtesting & auto portfolio builder")

df, bt_df, politician_rank = load_and_process_data()

st.success(f"✅ Analyzed {len(bt_df)} trades from {len(politician_rank)} politicians!")

# =============================
# LIVE FEED AT TOP
# =============================
st.header("📡 Live Congress Trading Feed")
st.markdown("*Most recent congressional trades with signal strength*")

# Show latest 50 trades
live_feed = df.sort_values("DisclosureDate", ascending=False).head(50)

# Color code by signal
def color_signal(val):
    if val == "STRONG BUY": return 'background-color: #00ff00'
    if val == "BUY": return 'background-color: #90EE90'
    if val == "WATCH": return 'background-color: #FFD700'
    return 'background-color: #FFB6C1'

st.dataframe(
    live_feed[["Politician", "Ticker", "transaction_type", "amount_range", "Party", "Chamber", "signal_strength", "final_signal", "DisclosureDate"]],
    use_container_width=True,
    hide_index=True,
    column_config={
        "Politician": "Politician",
        "Ticker": "Ticker",
        "transaction_type": "Type",
        "amount_range": "Amount",
        "Party": "Party",
        "Chamber": "Chamber",
        "signal_strength": "Signal Strength",
        "final_signal": "Rating",
        "DisclosureDate": st.column_config.DateColumn("Disclosure Date", format="YYYY-MM-DD")
    }
)

# Signal distribution in live feed
col1, col2 = st.columns(2)
with col1:
    signal_counts = live_feed['final_signal'].value_counts()
    fig = px.pie(signal_counts, values=signal_counts.values, names=signal_counts.index, 
                 title="Signal Distribution (Latest 50)", hole=0.3)
    st.plotly_chart(fig, use_container_width=True)

with col2:
    party_counts = live_feed['Party'].value_counts()
    fig = px.bar(x=party_counts.index, y=party_counts.values, 
                 title="Trades by Party (Latest 50)",
                 color=party_counts.index,
                 color_discrete_map={'Republican': '#FF4B4B', 'Democrat': '#4B4BFF'})
    st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# =============================
# KEY METRICS
# =============================
col1,col2,col3,col4 = st.columns(4)
col1.metric("Total Trades Backtested", len(bt_df))
col2.metric("Strong Buys", len(bt_df[bt_df['final_signal']=='STRONG BUY']))
col3.metric("Avg 180d Return", f"{bt_df['return_180d'].mean():.2f}%")
col4.metric("Top Trader", politician_rank.index[0][:20] if len(politician_rank)>0 else "N/A")

st.markdown("---")

# =============================
# AUTO PORTFOLIO BUILDER
# =============================
st.header("🧺 Auto Portfolio Builder")
portfolio_value = st.number_input("Portfolio value (£)", min_value=50, value=200, step=50)
strong = bt_df[bt_df["final_signal"]=="STRONG BUY"]
portfolio = strong.groupby("Ticker").agg({"skill_score":"mean","return_90d":"mean","Politician":"count"}).rename(columns={"Politician":"signal_count"}).sort_values("skill_score",ascending=False).head(10).reset_index()
if not portfolio.empty:
    portfolio["weight"] = portfolio["skill_score"]/portfolio["skill_score"].sum()
    portfolio["allocation_£"] = (portfolio["weight"]*portfolio_value).round(2)
    def get_info(t):
        try: s=yf.Ticker(t); return {"price":round(s.history(period="5d")["Close"].iloc[-1],2),"name":s.info.get('longName',t)[:30]}
        except: return {"price":None,"name":t}
    portfolio = pd.concat([portfolio, portfolio["Ticker"].apply(get_info).apply(pd.Series)], axis=1)
    portfolio["shares"] = (portfolio["allocation_£"]/portfolio["price"]).fillna(0).astype(int)
    st.dataframe(portfolio[["Ticker","name","signal_count","skill_score","return_90d","price","allocation_£","shares"]], use_container_width=True, hide_index=True)
    st.plotly_chart(px.pie(portfolio, values='allocation_£', names='Ticker', title='Portfolio Allocation', hole=0.3), use_container_width=True)
else:
    st.warning("No STRONG BUY signals yet")

st.markdown("---")

# =============================
# POLITICIAN PERFORMANCE
# =============================
st.header("🏆 Politician Performance Leaderboard")
top10 = politician_rank.head(10).reset_index()
st.dataframe(top10[["Politician","skill_score","win_rate_%","total_trades","return_90d_mean","return_180d_mean","return_365d_mean","max_gain_%_max"]], use_container_width=True, hide_index=True)

st.markdown("---")

# =============================
# TOP 5 OPPORTUNITIES
# =============================
st.header("🔥 Top 5 Investment Opportunities")
top5 = bt_df[bt_df["final_signal"].isin(["STRONG BUY","BUY"])].sort_values("skill_score",ascending=False).groupby("Ticker").first().reset_index().head(5)
if len(top5)>0: 
    st.dataframe(top5[['Politician','Ticker','Party','Chamber','amount_range','skill_score','return_90d','return_180d','return_365d','max_gain_%','final_signal','DisclosureDate']], use_container_width=True, hide_index=True)
else:
    st.warning("No strong opportunities found")

st.markdown("---")

# =============================
# EMAIL ALERTS
# =============================
st.header("📧 Email Alerts")
if st.button("Send STRONG BUY Alerts"):
    if send_email_alert(strong[["Politician","Ticker","amount_range","skill_score","return_90d"]]): st.success("✅ Sent!")
    else: st.warning("⚠️ Email not configured")

st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC | Auto-updates every hour")
