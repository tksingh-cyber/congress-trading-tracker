import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import yfinance as yf
from datetime import datetime, timedelta

st.set_page_config(page_title="Congress Trading Tracker", layout="wide", page_icon="🏛️")

# Custom CSS
st.markdown("""
    <style>
    .big-font {
        font-size:50px !important;
        font-weight: bold;
    }
    </style>
    """, unsafe_allow_html=True)

# API Configuration
API_KEY = st.secrets.get("QUIVER_API_KEY", "3e58cb4de846a54998b70a3775f6cff2f25ead56")
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
}

# Load and process data
@st.cache_data(ttl=3600)  # Cache for 1 hour
def load_and_process_data():
    try:
        # Fetch House trades
        house_url = "https://api.quiverquant.com/beta/live/congresstrading"
        house_response = requests.get(house_url, headers=HEADERS, timeout=30)
        
        # Fetch Senate trades
        senate_url = "https://api.quiverquant.com/beta/live/senatetrading"
        senate_response = requests.get(senate_url, headers=HEADERS, timeout=30)
        
        if house_response.status_code != 200:
            st.error(f"House API returned status code: {house_response.status_code}")
            st.stop()
            
        if senate_response.status_code != 200:
            st.error(f"Senate API returned status code: {senate_response.status_code}")
            st.stop()
        
        # Combine both datasets
        house_data = pd.DataFrame(house_response.json())
        senate_data = pd.DataFrame(senate_response.json())
        
        house_data['Chamber'] = 'House'
        senate_data['Chamber'] = 'Senate'
        
        df = pd.concat([house_data, senate_data], ignore_index=True)
        
        if len(df) == 0:
            st.error("No data returned from API")
            st.stop()
            
    except Exception as e:
        st.error(f"Error fetching data: {str(e)}")
        st.stop()
    
    # Clean data
    df_clean = df[[
        "Representative",
        "Chamber",
        "Party",
        "Ticker",
        "Transaction",
        "Range",
        "TransactionDate",
        "ReportDate"
    ]].copy()
    
    df_clean.rename(columns={
        "Representative": "Politician",
        "Transaction": "transaction_type",
        "Range": "amount_range",
        "ReportDate": "DisclosureDate"
    }, inplace=True)
    
    # Clean dates
    df_clean['DisclosureDate'] = pd.to_datetime(df_clean['DisclosureDate'], errors='coerce')
    df_clean['TransactionDate'] = pd.to_datetime(df_clean['TransactionDate'], errors='coerce')
    df_clean = df_clean.dropna(subset=['DisclosureDate'])
    df_clean = df_clean[df_clean['DisclosureDate'] < datetime.now()]
    df_clean = df_clean.dropna(subset=['Ticker'])
    df_clean = df_clean[df_clean['Ticker'] != '']
    
    # Signal strength
    def signal_strength(row):
        score = 0
        if row["transaction_type"] == "Purchase":
            score += 2
        if "$15,000" in str(row["amount_range"]) or "$50,000" in str(row["amount_range"]):
            score += 2
        if row["Chamber"] == "Senate":
            score += 1
        if score >= 4:
            return "HIGH"
        elif score >= 2:
            return "MEDIUM"
        else:
            return "LOW"
    
    df_clean["signal_strength"] = df_clean.apply(signal_strength, axis=1)
    
    # Allocation
    def allocation(signal):
        if signal == "HIGH":
            return "5–10%"
        if signal == "MEDIUM":
            return "2–4%"
        return "Avoid"
    
    df_clean["suggested_allocation"] = df_clean["signal_strength"].apply(allocation)
    
    # Backtest ALL purchases
    def backtest_trade(ticker, disclosure_date):
        try:
            start = pd.to_datetime(disclosure_date)
            end = start + timedelta(days=370)
            prices = yf.download(ticker, start=start, end=end, progress=False)
            if prices.empty:
                return None
            entry = float(prices.iloc[0]["Close"])
            results = {}
            for days in [30, 90, 180, 365]:
                idx = min(days, len(prices)-1)
                exit_price = float(prices.iloc[idx]["Close"])
                results[f"return_{days}d"] = round((exit_price - entry) / entry * 100, 2)
            return results
        except:
            return None
    
    rows = []
    # PROCESS ALL PURCHASES (NO LIMIT)
    purchases = df_clean[df_clean["transaction_type"] == "Purchase"]
    
    st.write(f"📊 Found {len(purchases)} purchase trades to backtest...")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, (i, row) in enumerate(purchases.iterrows()):
        try:
            res = backtest_trade(row["Ticker"], row["DisclosureDate"])
            if res:
                row_dict = {
                    "Politician": row["Politician"],
                    "Chamber": row["Chamber"],
                    "Party": row["Party"],
                    "Ticker": row["Ticker"],
                    "transaction_type": row["transaction_type"],
                    "amount_range": row["amount_range"],
                    "TransactionDate": row["TransactionDate"],
                    "DisclosureDate": row["DisclosureDate"],
                    "signal_strength": row["signal_strength"],
                    "suggested_allocation": row["suggested_allocation"],
                    **res
                }
                rows.append(row_dict)
        except:
            continue
        
        # Update progress every 10 trades to improve performance
        if idx % 10 == 0 or idx == len(purchases) - 1:
            progress_bar.progress((idx + 1) / len(purchases))
            status_text.text(f"Processing trade {idx + 1} of {len(purchases)}... ({len(rows)} successful)")
    
    progress_bar.empty()
    status_text.empty()
    
    bt_df = pd.DataFrame(rows)
    
    if len(bt_df) == 0:
        st.error("No trades could be backtested")
        st.stop()
    
    st.success(f"✅ Successfully backtested {len(bt_df)} trades!")
    
    # Politician rankings
    politician_rank = (
        bt_df.groupby("Politician")[["return_90d", "return_180d", "return_365d"]]
        .mean()
        .dropna()
    )
    
    politician_rank["skill_score"] = (
        politician_rank["return_90d"] * 0.3 +
        politician_rank["return_180d"] * 0.4 +
        politician_rank["return_365d"] * 0.3
    )
    
    politician_rank = politician_rank.sort_values("skill_score", ascending=False)
    
    # Merge skill scores
    bt_df = bt_df.merge(politician_rank["skill_score"], on="Politician", how="left")
    
    # Final signal
    def final_signal(row):
        score = 0
        if row["signal_strength"] == "HIGH":
            score += 2
        if pd.notna(row["skill_score"]) and row["skill_score"] > 20:
            score += 2
        if pd.notna(row["return_90d"]) and row["return_90d"] > 10:
            score += 1
        if score >= 4:
            return "STRONG BUY"
        elif score >= 3:
            return "BUY"
        elif score >= 2:
            return "WATCH"
        else:
            return "IGNORE"
    
    bt_df["final_signal"] = bt_df.apply(final_signal, axis=1)
    
    # Top 5
    top_5 = (
        bt_df[bt_df["final_signal"].isin(["STRONG BUY", "BUY"])]
        .sort_values("skill_score", ascending=False)
        .groupby("Ticker")
        .first()
        .reset_index()
        .head(5)
    )
    
    return top_5, politician_rank, bt_df

# Header
st.markdown('<p class="big-font">🏛️ Congress Trading Intelligence</p>', unsafe_allow_html=True)
st.markdown("### Track and analyze congressional stock trades in real-time")
st.info("⏱️ Loading fresh data... This may take 5-10 minutes for complete analysis")

# Load data
top_5, politician_rank, bt_df = load_and_process_data()

st.success("✅ Data fully updated!")

# Key Metrics Row
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Total Trades Analyzed", len(bt_df))
    
with col2:
    strong_buys = len(bt_df[bt_df['final_signal'] == 'STRONG BUY'])
    st.metric("Strong Buy Signals", strong_buys)
    
with col3:
    avg_return = bt_df['return_180d'].mean()
    st.metric("Avg 180-Day Return", f"{avg_return:.2f}%")
    
with col4:
    top_politicians = len(politician_rank)
    st.metric("Politicians Tracked", top_politicians)

st.markdown("---")

# TOP 5 OPPORTUNITIES
st.markdown("## 🔥 Top 5 Investment Opportunities")
st.markdown("*Highest conviction trades from best-performing politicians*")

if len(top_5) > 0:
    top_5_display = top_5[[
        'Politician', 'Ticker', 'Party', 'amount_range', 
        'skill_score', 'return_90d', 'return_180d', 
        'final_signal', 'suggested_allocation'
    ]].copy()

    top_5_display.columns = [
        'Politician', 'Ticker', 'Party', 'Amount', 
        'Skill Score', '90d Return %', '180d Return %', 
        'Signal', 'Allocation'
    ]

    st.dataframe(top_5_display, use_container_width=True, hide_index=True)
else:
    st.warning("No strong buy/buy signals found")

st.markdown("---")

# POLITICIAN LEADERBOARD
st.markdown("## 🏆 Politician Leaderboard")

col1, col2 = st.columns([2, 1])

with col1:
    top_10 = politician_rank.head(10).reset_index()
    top_10.columns = ['Politician', '90d Avg', '180d Avg', '365d Avg', 'Skill Score']
    st.dataframe(top_10, use_container_width=True, hide_index=True)

with col2:
    fig = px.bar(
        top_10.head(5),
        x='Skill Score',
        y='Politician',
        orientation='h',
        title="Top 5 by Skill Score",
        color='Skill Score',
        color_continuous_scale='Blues'
    )
    fig.update_layout(showlegend=False, height=400)
    st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# SIGNAL DISTRIBUTION
st.markdown("## 📊 Signal Distribution")

col1, col2 = st.columns(2)

with col1:
    signal_counts = bt_df['final_signal'].value_counts()
    fig = px.pie(values=signal_counts.values, names=signal_counts.index, 
                 title="Trade Signals Breakdown",
                 color_discrete_sequence=px.colors.sequential.RdBu)
    st.plotly_chart(fig, use_container_width=True)

with col2:
    party_counts = bt_df['Party'].value_counts()
    fig = px.bar(x=party_counts.index, y=party_counts.values, 
                 title="Trades by Party", 
                 labels={'x': 'Party', 'y': 'Number of Trades'},
                 color=party_counts.index,
                 color_discrete_map={'Republican': '#FF4B4B', 'Democrat': '#4B4BFF'})
    st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# FILTERS & FULL DATA
st.markdown("## 🔍 Explore All Trades")

col1, col2, col3 = st.columns(3)

with col1:
    signal_filter = st.multiselect(
        "Filter by Signal",
        options=bt_df['final_signal'].unique(),
        default=bt_df['final_signal'].unique()
    )

with col2:
    party_filter = st.multiselect(
        "Filter by Party",
        options=bt_df['Party'].unique(),
        default=bt_df['Party'].unique()
    )

with col3:
    min_return = st.slider(
        "Min 180d Return %",
        float(bt_df['return_180d'].min()),
        float(bt_df['return_180d'].max()),
        float(bt_df['return_180d'].min())
    )

filtered_df = bt_df[
    (bt_df['final_signal'].isin(signal_filter)) &
    (bt_df['Party'].isin(party_filter)) &
    (bt_df['return_180d'] >= min_return)
]

st.write(f"Showing {len(filtered_df)} trades")

st.dataframe(
    filtered_df[[
        'Politician', 'Ticker', 'Party', 'transaction_type',
        'amount_range', 'skill_score', 'return_90d', 'return_180d',
        'final_signal', 'suggested_allocation'
    ]],
    use_container_width=True,
    hide_index=True
)

st.markdown("---")
st.markdown(f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC. Data cached for 1 hour.*")
