import asyncio
import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pydantic import BaseModel, Field, validator
from typing import Optional, List

from app.agents.base import BaseAgent, GeminiBaseAgent
from app.tools.market import format_market_summary, get_market_data
from app.tools.news import format_news_summary
from app.tools.forex_mt5 import MT5Client
from app.tools.risk_manager import RiskManager
import MetaTrader5 as mt5

class LuxDecision(BaseModel):
    decisao: str = Field(..., description="COMPRAR, VENDER, HOLD ou trailing_stop")
    ativo_prioritario: str = Field(..., description="EURUSD, GBPUSD, USDJPY, AUDUSD ou none")
    direcao: str = Field(..., description="long, short ou none")
    total_confidence: float = Field(default=0.0, ge=0.0, le=10.0)
    risk_verdict: str = Field(default="PENDING", description="APPROVED | REJECTED")
    justificativa: str = Field(..., min_length=10)

    @validator("decisao")
    def validate_decisao(cls, v):
        v = v.lower()
        if any(k in v for k in ["executar", "comprar", "buy"]): return "COMPRAR"
        if any(k in v for k in ["vender", "sell"]): return "VENDER"
        if "trailing" in v or "stop" in v: return "trailing_stop"
        return "AGUARDAR"

    @validator("ativo_prioritario")
    def validate_ativo(cls, v):
        v = v.upper()
        if v in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]: return v
        return "NONE"

logger = logging.getLogger(__name__)

@dataclass
class HeartbeatReport:
    id: int | None
    decision: str       # COMPRAR / VENDER / AGUARDAR
    asset: str          # EURUSD / GBPUSD / etc
    direction: str      # long / short / none
    reasoning: str
    hype_analysis: str
    oracle_analysis: str
    vitalik_analysis: str
    lux_raw: str
    market_snapshot: dict
    news_count: int
    trade_status: str = "none"
    order_id: str | None = None
    created_at: str = ""

    def to_dict(self):
        return asdict(self)

def _extract_gold_decision(lux_response: str, agent) -> tuple[str, str, str, dict]:
    """Extract decision and consensus metadata from Lux Response using Pydantic."""
    try:
        data = agent._extract_json(lux_response)
        if not data:
            return "AGUARDAR", "none", "none", {}
        
        # Validar via Pydantic
        validated = LuxDecision(**data)
        
        return (
            validated.decisao,
            validated.ativo_prioritario,
            validated.direcao,
            validated.dict()
        )
    except Exception as e:
        logger.warning(f"Pydantic validation failed for Lux: {e}")
        try:
             data = agent._extract_json(lux_response)
             if not data: return "AGUARDAR", "none", "none", {}
             
             decisao_str = str(data.get("decisao", "hold")).lower()
             decision = "AGUARDAR"
             if any(k in decisao_str for k in ["executar", "comprar", "buy"]): decision = "COMPRAR"
             elif any(k in decisao_str for k in ["vender", "sell"]): decision = "VENDER"
             elif "trailing" in decisao_str or "stop" in decisao_str: decision = "trailing_stop"
             
             return (
                 decision, 
                 str(data.get("ativo_prioritario", "none")).upper(), 
                 str(data.get("direcao", "none")).lower(), 
                 data
             )
        except Exception as e2:
             logger.error(f"Brute fallback failed: {e2}")
             return "AGUARDAR", "none", "none", {}

def _build_market_brief(market_data: dict, news: list, depth: dict = None) -> str:
    sections = []
    sections.append(f"## Monitoramento Forex (MT5)\n{format_market_summary(market_data)}")
    
    if depth:
        sections.append(f"## Order Flow (Depth of Market)\n{depth.get('summary', 'Dados DOM indisponíveis')}")
        
    sections.append(f"## Headlines Recentes\n{format_news_summary(news)}")
    return "\n\n".join(sections)

def _summarize_for_memory(analysis: str, market_data: dict, agent_id: str) -> str:
    sinal = "hold"
    try:
        match = re.search(r'"sinal"\s*:\s*"(\w+)"', analysis)
        if match: sinal = match.group(1)
    except Exception: pass
    
    conf = "?"
    try:
        match = re.search(r'"confianca"\s*:\s*([\d.]+)', analysis)
        if match: conf = match.group(1)
    except Exception: pass
    
    return f"- Sinal: {sinal} | Confiança: {conf}"

def _format_depth_summary(depth_data: list) -> str:
    if not depth_data:
        return "Sem dados de profundidade."
    
    buys = [d for d in depth_data if d['type'] in [mt5.BOOK_TYPE_BUY, mt5.BOOK_TYPE_BUY_MARKET]]
    sells = [d for d in depth_data if d['type'] in [mt5.BOOK_TYPE_SELL, mt5.BOOK_TYPE_SELL_MARKET]]
    
    total_buy_vol = sum(d['volume'] for d in buys)
    total_sell_vol = sum(d['volume'] for d in sells)
    
    pressure = "Neutro"
    if total_buy_vol > total_sell_vol * 1.5: pressure = "Alta Pressão de COMPRA (Smart Money Accumulation)"
    elif total_sell_vol > total_buy_vol * 1.5: pressure = "Alta Pressão de VENDA (Smart Money Distribution)"
    
    return f"Volume Compra: {total_buy_vol} | Volume Venda: {total_sell_vol} | Bias de Fluxo: {pressure}"

class LuxAgent(GeminiBaseAgent):
    def __init__(self):
        super().__init__("lux")

    async def run_heartbeat(self, market_data: dict, news: list) -> HeartbeatReport:
        from app.memory import db as memory_db
        import app.agents.registry as registry

        mt5_client = MT5Client()
        risk_manager = RiskManager()
        
        # 0. Gestão Dinâmica de Lucro (Breakeven) em trades abertos
        await self.log_event("Gerenciando posições abertas (Breakeven Check)...")
        risk_manager.apply_dynamic_profit_management()
        
        # 1. Check Drawdown
        if risk_manager.check_drawdown_limit():
            await self.log_event("DRAWDOWN ATINGIDO. Operações suspensas por hoje.", "warning")
        
        if not mt5_client.connect():
             await self.log_event("Falha ao conectar ao MT5 para o Heartbeat", "error")
        
        account_info = mt5_client.get_account_summary()
        positions = mt5_client.list_positions()
        
        position_briefs = []
        for pos in positions:
            profit = pos["profit"]
            position_briefs.append(
                f"- ATIVA: {pos['symbol']} | Profit: {profit} | Price: {pos['price_open']}"
            )
        
        pos_context = "\n".join(position_briefs) if position_briefs else "Nenhuma posição ativa."

        # 1.5. Fetch Order Flow (DOM)
        depth_summary = None
        current_asset = market_data.get("main_asset", "EURUSD")
        raw_depth = mt5_client.get_market_depth(current_asset)
        if raw_depth:
            depth_summary = {"summary": _format_depth_summary(raw_depth)}

        market_brief = _build_market_brief(market_data, news, depth=depth_summary)
        market_brief += f"\n\n### Status da Conta MT5\n{account_info}"
        market_brief += f"\n\n### Posições Ativas\n{pos_context}"
        
        await self.log_event("Analisando mercado Forex e notícias...")

        trader_prompt = (
            f"{market_brief}\n\n"
            "Analise os dados técnicos e fundamentais para o seu par específico e responda em JSON conforme seu SOUL.md."
        )

        await self.log_event("Iniciando 'MIA Team Meeting' (Analistas Especializados)...")
        hype_analysis, oracle_analysis, vitalik_analysis = await asyncio.gather(
            registry.call_agent("hype_beast", trader_prompt),
            registry.call_agent("oracle", trader_prompt),
            registry.call_agent("vitalik", trader_prompt),
        )
        await self.log_event("Análises recebidas. Consolidando inteligência interna...")

        trader_signals = {}
        for agent_id, analysis_text in [("hype_beast", hype_analysis), ("oracle", oracle_analysis), ("vitalik", vitalik_analysis)]:
            try:
                parsed = self._extract_json(analysis_text)
                if parsed:
                    trader_signals[agent_id] = {
                        "sinal": str(parsed.get("sinal", "hold")).lower(),
                        "confianca": float(parsed.get("confianca", 0)),
                    }
            except Exception:
                trader_signals[agent_id] = {"sinal": "hold", "confianca": 0}

        # 2. Risk Evaluation & Parameter Calculation
        possible_asset = "EURUSD" 
        
        best_agent = max(trader_signals, key=lambda k: trader_signals[k]["confianca"])
        if trader_signals[best_agent]["confianca"] >= 5.0:
            possible_asset = "EURUSD" if best_agent == "oracle" else ("GBPUSD" if best_agent == "hype_beast" else "USDJPY")
        
        tick = mt5_client.get_symbol_price(possible_asset)
        risk_params = {"volume": 0.01, "sl": 0, "tp": 0}
        if tick:
            direction_temp = "long" if trader_signals[best_agent]["sinal"] == "buy" else "short"
            risk_params = risk_manager.get_risk_params(possible_asset, direction_temp, tick["ask"] if direction_temp == "long" else tick["bid"])

        aggregate_prompt = (
            f"{market_brief}\n\n"
            f"## Reunião de Equipe (Analistas)\n\n"
            f"### Hype Beast (Scalper): {trader_signals.get('hype_beast', {}).get('sinal')} ({trader_signals.get('hype_beast', {}).get('confianca')})\n{hype_analysis}\n\n"
            f"### Oracle (Técnico): {trader_signals.get('oracle', {}).get('sinal')} ({trader_signals.get('oracle', {}).get('confianca')})\n{oracle_analysis}\n\n"
            f"### Vitalik (Macro): {trader_signals.get('vitalik', {}).get('sinal')} ({trader_signals.get('vitalik', {}).get('confianca')})\n{vitalik_analysis}\n\n"
            f"## Recomendação do Risk Manager\n"
            f"Para {possible_asset}: Volume Sugerido: {risk_params['volume']} | SL: {risk_params['sl']:.5f} | TP: {risk_params['tp']:.5f}\n\n"
            "Lux, como Diretor de Risco, valide as análises e emita o veredito final (JSON SOUL.md)."
        )
        
        lux_raw = await self.chat(aggregate_prompt)
        decision, asset, direction, lux_data = _extract_gold_decision(lux_raw, self)
        
        # 3. Final Verification
        is_approved = lux_data.get("risk_verdict") == "APPROVED"
        trade_info = {"status": "none", "order_id": None}
        
        if decision in ["COMPRAR", "VENDER"] and asset != "NONE" and is_approved:
            try:
                if not risk_manager.validate_execution_guard(asset):
                    await self.log_event(f"🛡️ Guard bloqueou entrada em {asset}: Spread muito alto.", "warning")
                    trade_info["status"] = "guard_blocked"
                else:
                    await self.log_event(f"⚖️ Risk Advisor aprovou: Executando {decision} em {asset}...")
                    is_buy = (decision == "COMPRAR")
                order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
                
                exec_tick = mt5_client.get_symbol_price(asset)
                if exec_tick:
                    price = exec_tick["ask"] if is_buy else exec_tick["bid"]
                    final_params = risk_manager.get_risk_params(asset, "long" if is_buy else "short", price)
                    
                    result = mt5_client.place_order(
                        asset, order_type, final_params["volume"], price, 
                        final_params["sl"], final_params["tp"]
                    )
                    
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        trade_info["status"] = "executed"
                        trade_info["order_id"] = str(result.order)
                        await self.log_event(f"✅ Execução Profissional: {asset} Volume:{final_params['volume']} @ {price}")
                    else:
                        trade_info["status"] = f"failed: {result.comment if result else 'unknown'}"
                        await self.log_event(f"❌ Abortado pelo Broker: {trade_info['status']}", "error")
            except Exception as e:
                 logger.error(f"Execution error: {e}")
                 trade_info["status"] = f"error: {str(e)}"
        elif decision == "trailing_stop":
            await self.log_event("🛡️ Protegendo lucro via trailing_stop...")
            trade_info["status"] = "trailing_active"
        else:
            await self.log_event(f"🧘 Decisão Coletiva: AGUARDAR ({lux_data.get('justificativa', 'Sem sinal claro')[:50]}...)")

        mt5_client.disconnect()

        # Update memories
        await registry.get_agent("hype_beast").append_memory(_summarize_for_memory(hype_analysis, market_data, "hype_beast"))
        await registry.get_agent("oracle").append_memory(_summarize_for_memory(oracle_analysis, market_data, "oracle"))
        await registry.get_agent("vitalik").append_memory(_summarize_for_memory(vitalik_analysis, market_data, "vitalik"))
        await self.append_memory(f"Trade Meeting Decision: {decision} | Asset: {asset} | Verdict: {lux_data.get('risk_verdict')}")

        report_id = await memory_db.save_report(
            market_data=market_data,
            news=news,
            hype_analysis=hype_analysis,
            oracle_analysis=oracle_analysis,
            vitalik_analysis=vitalik_analysis,
            lux_decision=decision,
            lux_raw=lux_raw,
            lux_decision_reason=lux_data.get("justificativa", ""),
            risk_params=risk_params if is_approved else {},
        )

        return HeartbeatReport(
            id=report_id,
            decision=decision,
            asset=asset,
            direction=direction,
            reasoning=lux_raw[:500],
            hype_analysis=hype_analysis,
            oracle_analysis=oracle_analysis,
            vitalik_analysis=vitalik_analysis,
            lux_raw=lux_raw,
            market_snapshot=market_data,
            news_count=len(news),
            trade_status=trade_info["status"],
            order_id=trade_info["order_id"],
            created_at=datetime.utcnow().isoformat(),
        )
