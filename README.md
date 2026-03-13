# ACTUS-FOREX V1 🌟

ACTUS-FOREX is a high-frequency algorithmic trading system designed for the Forex market. It leverages a multi-agent architectural model (MIA - Multi-Agent Intelligence) to perform technical, fundamental, and sentiment analysis in real-time.

## 🚀 Key Features

- **Professional Dashboard**: High-fidelity UI with real-time MetaTrader 5 (MT5) integration.
- **MIA Orchestration**: Specialized agents (Lux, Oracle, Hype Beast, Vitalik) collaborating for risk-adjusted trade execution.
- **MT5 Native Connectivity**: Direct execution and order flow analysis via MetaTrader 5.
- **Risk Management**: Rule-based engine with automated Stop Loss, Take Profit, and Drawdown protection.
- **Telegram Reporting**: Daily performance reports and real-time alerts.

## 🛠️ Architecture

The system is split into:
- **/backend**: FastAPI server managing agent logic, scheduling, and MT5 connectivity.
- **/app/agents**: Individual agent definitions and their cognitive frameworks (SOUL/MEMORY).
- **/app/tools**: Specialized toolsets for MT5 integration, News harvesting, and Risk Management.

## ⚙️ Configuration (.env)

Create a `.env` file in the `backend/` directory with the following variables:

```env
# AI Models (Gemini/OpenAI/Anthropic)
GEMINI_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here

# MetaTrader 5 Configuration
MT5_LOGIN=your_login
MT5_PASSWORD=your_password
MT5_SERVER=your_broker_server

# Telegram Notifications
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

## 📦 Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the backend:
   ```bash
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
3. Access the dashboard:
   `http://localhost:8000`

## ⚖️ Technical Audit
This version (V1) has been audited to ensure full parity between logic and execution, resolving previous inconsistencies in risk management and agent coordination.
