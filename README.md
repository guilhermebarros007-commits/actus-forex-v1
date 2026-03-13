# ACTUS-FOREX

ACTUS-FOREX is a high-performance algorithmic trading bot system designed for the Forex market, leveraging MetaTrader 5 and AI-driven analysis.

## Features
- **MT5 Integration**: Native connectivity for real-time order execution and market data.
- **AI Boardroom**: Multi-agent consensus system (Lux, Oracle, Hype Beast, Vitalik).
- **Risk Management**: Quantitative lot sizing based on equity and volatility (ATR).
- **Modern Dashboard**: Real-time monitoring via WebSockets and glassmorphic UI.
- **Telegram Connectivity**: Daily reports and critical alerts.

## Setup
1. Clone the repository.
2. Install dependencies: `pip install -r requirements.txt`.
3. Configure your `.env` file with MT5 and Telegram credentials.
4. Run the backend: `python -m uvicorn app.main:app`.

---
*Disclaimer: Trading Forex involves significant risk. Use this software at your own risk.*