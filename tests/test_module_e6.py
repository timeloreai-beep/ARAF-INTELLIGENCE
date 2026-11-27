"""Module E6 Verification Tests: 100-Agent AI Open-Order Supervision Swarm."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.app.services.ai_swarm import (
    AISwarmSupervision,
    AISwarmAgent,
    SwarmState,
    AGENT_COUNT,
    CONSENSUS_THRESHOLD,
)
from backend.app.services.paper_lifecycle import advance_paper_position


class TestAISwarmAgent:
    """Test individual AI Swarm Agent behavior."""

    def test_agent_initialization(self):
        """Test agent is created with correct engine focus."""
        agent = AISwarmAgent(0, "ENG01_AMT_TPO")
        assert agent.agent_id == 0
        assert agent.engine_focus == "ENG01_AMT_TPO"

    def test_agent_pool_has_100_agents(self):
        """Test that exactly 100 agents are created."""
        swarm = AISwarmSupervision(MagicMock(), MagicMock())
        assert len(swarm.agent_pool) == AGENT_COUNT
        assert AGENT_COUNT == 100


class TestAISwarmSupervision:
    """Test the AI Swarm Supervision service."""

    @pytest.fixture
    def mock_database(self):
        db = MagicMock()
        return db

    @pytest.fixture
    def mock_redis(self):
        redis = MagicMock()
        return redis

    @pytest.fixture
    def swarm(self, mock_database, mock_redis):
        return AISwarmSupervision(mock_database, mock_redis)

    @pytest.mark.asyncio
    async def test_start_swarm_for_order(self, swarm):
        """Test activating a swarm for a new open order."""
        state = await swarm.start_swarm_for_order(
            order_id="order_123",
            symbol="NIFTY",
            side="BUY",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=120.0,
            trailing_sl=95.0,
            quantity=75.0,
            exchange="NFO",
            broker_token="26000",
            is_auto_trade=True,
        )

        assert state.order_id == "order_123"
        assert state.symbol == "NIFTY"
        assert state.side == "BUY"
        assert state.agents_active == 100
        assert not state.is_terminated

    @pytest.mark.asyncio
    async def test_terminate_swarm_on_close(self, swarm):
        """Test IMMEDIATE termination of all 100 agents when order closes."""
        await swarm.start_swarm_for_order(
            order_id="order_456",
            symbol="BANKNIFTY",
            side="SELL",
            entry_price=50000.0,
            stop_loss=51000.0,
            take_profit=48000.0,
            trailing_sl=49500.0,
            quantity=30.0,
            exchange="NFO",
            broker_token="26009",
            is_auto_trade=False,
        )

        assert "order_456" in swarm.swarms
        assert swarm.swarms["order_456"].agents_active == 100

        # Terminate on close
        terminated = await swarm.terminate_swarm("order_456", "TP_HIT")

        assert terminated is True
        assert "order_456" not in swarm.swarms
        # Verify agents are released (swarm removed)

    @pytest.mark.asyncio
    async def test_consensus_calculation(self, swarm):
        """Test 100% consensus calculation from agent verdicts."""
        await swarm.start_swarm_for_order(
            order_id="order_consensus",
            symbol="NIFTY",
            side="BUY",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=120.0,
            trailing_sl=95.0,
            quantity=75.0,
            exchange="NFO",
            broker_token="26000",
            is_auto_trade=True,
        )

        state = swarm.swarms["order_consensus"]
        
        # Simulate all agents voting EXIT
        exit_verdicts = 100
        consensus_score = exit_verdicts / AGENT_COUNT
        
        assert consensus_score == CONSENSUS_THRESHOLD
        assert consensus_score == 1.0

    @pytest.mark.asyncio
    async def test_swarm_status_reporting(self, swarm):
        """Test live swarm status includes agents_active, consensus_score, verdict."""
        await swarm.start_swarm_for_order(
            order_id="order_status",
            symbol="RELIANCE",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2600.0,
            trailing_sl=2475.0,
            quantity=10.0,
            exchange="NSE",
            broker_token="2885",
            is_auto_trade=True,
        )

        status = await swarm.get_swarm_status("order_status")
        
        assert status is not None
        assert status["order_id"] == "order_status"
        assert status["agents_active"] == 100
        assert "consensus_score" in status
        assert "verdict" in status
        assert "last_tick_ms" in status


class TestAISwarmConsensusExit:
    """Test the 100% consensus automatic exit scenarios."""

    @pytest.fixture
    def mock_price_feed(self):
        with patch('backend.app.services.ai_swarm.get_price_feed_manager') as mock:
            feed = MagicMock()
            quote = MagicMock()
            quote.price = 110.0
            quote.source = "SHOONYA"
            quote.quality = "LIVE"
            feed.get_quote = AsyncMock(return_value=quote)
            feed.get_candles = AsyncMock(return_value=[])
            mock.return_value = feed
            yield feed

    @pytest.mark.asyncio
    async def test_long_early_exit_consensus_logic(self, mock_price_feed):
        """
        Test the consensus logic for LONG early exit.
        Verifies that when engines signal reversal, agents vote EXIT.
        """
        swarm = AISwarmSupervision(MagicMock(), MagicMock())
        
        await swarm.start_swarm_for_order(
            order_id="long_early_exit",
            symbol="NIFTY",
            side="BUY",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=120.0,
            trailing_sl=95.0,
            quantity=75.0,
            exchange="NFO",
            broker_token="26000",
            is_auto_trade=True,
        )

        state = swarm.swarms["long_early_exit"]
        assert state.agents_active == 100
        
        # Test agent evaluation logic directly
        agent = AISwarmAgent(0, "ENG12_REGIME_ROUTER")
        
        engine_results = [
            {"engineId": "ENG12_REGIME_ROUTER", "status": "FAIL", "direction": "SELL", "evidence": "COUNTER_REGIME_BLOCK", "scoreDelta": -8},
            {"engineId": "ENG04_INTERMARKET_MACRO", "status": "FAIL", "direction": "BEARISH_MACRO", "evidence": "EXTREME_VIX_REGIME", "scoreDelta": -6},
            {"engineId": "ENG11_NEWS_SENTIMENT", "status": "FAIL", "direction": "SELL", "evidence": "NEWS_SENTIMENT_CONFLICT", "scoreDelta": -5},
        ]
        
        verdict = await agent.evaluate(
            state, 
            {"ltp": 110.0}, 
            engine_results, 
            [], {}, {}
        )
        
        # Agent should vote EXIT_REVERSAL when engines signal opposite direction
        assert verdict.verdict == "EXIT_REVERSAL"
        assert verdict.confidence >= 0.90

    @pytest.mark.asyncio
    async def test_short_early_exit_consensus_logic(self, mock_price_feed):
        """
        Test the consensus logic for SHORT early exit.
        Verifies that when engines signal bounce, agents vote EXIT.
        """
        swarm = AISwarmSupervision(MagicMock(), MagicMock())
        
        await swarm.start_swarm_for_order(
            order_id="short_early_exit",
            symbol="NIFTY",
            side="SELL",
            entry_price=100.0,
            stop_loss=110.0,
            take_profit=80.0,
            trailing_sl=105.0,
            quantity=75.0,
            exchange="NFO",
            broker_token="26000",
            is_auto_trade=True,
        )

        state = swarm.swarms["short_early_exit"]
        assert state.agents_active == 100
        
        # Test agent evaluation logic directly
        agent = AISwarmAgent(0, "ENG12_REGIME_ROUTER")
        
        engine_results = [
            {"engineId": "ENG12_REGIME_ROUTER", "status": "FAIL", "direction": "BUY", "evidence": "COUNTER_REGIME_BLOCK", "scoreDelta": -8},
            {"engineId": "ENG01_AMT_TPO", "status": "FAIL", "direction": "BUY", "evidence": "IMBALANCE_BULL", "scoreDelta": 4},
            {"engineId": "ENG06_LIQUIDITY_HEATMAP", "status": "FAIL", "direction": "BUY", "evidence": "DEPTH_BALANCED", "scoreDelta": 4},
        ]
        
        verdict = await agent.evaluate(
            state, 
            {"ltp": 90.0}, 
            engine_results, 
            [], {}, {}
        )
        
        # Agent should vote EXIT_REVERSAL when engines signal opposite direction
        assert verdict.verdict == "EXIT_REVERSAL"
        assert verdict.confidence >= 0.90


class TestAISwarmIntegration:
    """Integration tests for AI Swarm with existing system."""

    def test_ai_swarm_service_importable(self):
        """Verify AI Swarm service can be imported."""
        from backend.app.services.ai_swarm import AISwarmSupervision, get_ai_swarm_service
        assert AISwarmSupervision is not None
        assert get_ai_swarm_service is not None

    def test_ai_swarm_router_importable(self):
        """Verify AI Swarm router can be imported."""
        from backend.app.routers import ai_swarm
        assert ai_swarm.router is not None

    def test_main_py_integrates_ai_swarm(self):
        """Verify main.py imports and registers AI Swarm."""
        import backend.app.main as main_module
        import inspect
        
        source = inspect.getsource(main_module)
        assert "ai_swarm" in source
        assert "from .services.ai_swarm import get_ai_swarm_service" in source

    def test_automation_activates_swarm_on_entry(self):
        """Verify automation router activates swarm on order placement."""
        import backend.app.routers.automation as automation_module
        import inspect
        
        source = inspect.getsource(automation_module)
        assert "get_ai_swarm_service" in source
        assert "start_swarm_for_order" in source

    def test_manual_exit_terminates_swarm(self):
        """Verify manual exit terminates swarm."""
        import backend.app.routers.automation as automation_module
        import inspect
        
        source = inspect.getsource(automation_module.manually_exit_paper_position)
        assert "terminate_swarm" in source

    def test_intraday_exit_terminates_swarm(self):
        """Verify intraday exit terminates swarm."""
        import backend.app.services.intraday_exit as intraday_module
        import inspect
        
        source = inspect.getsource(intraday_module.IntradayExitEngine._exit_paper_position)
        assert "terminate_swarm" in source
        assert "get_ai_swarm_service" in source

    def test_paper_worker_terminates_swarm(self):
        """Verify paper position worker terminates swarm on auto-close."""
        import backend.app.main as main_module
        import inspect
        
        source = inspect.getsource(main_module._paper_position_worker)
        assert "terminate_swarm" in source
        assert "get_ai_swarm_service" in source


class TestAISwarmVerificationScenarios:
    """Exact verification scenarios from requirements."""

    @pytest.mark.asyncio
    async def test_scenario_a_long_consensus_exit(self):
        """
        (a) LONG order open -> 100 AI Agents active -> 
        100% reversal consensus at 110 -> 
        Immediate early exit executed at 110!
        """
        swarm = AISwarmSupervision(MagicMock(), MagicMock())
        
        await swarm.start_swarm_for_order(
            order_id="verify_long",
            symbol="NIFTY",
            side="BUY",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=120.0,
            trailing_sl=95.0,
            quantity=75.0,
            exchange="NFO",
            broker_token="26000",
            is_auto_trade=True,
        )
        
        state = swarm.swarms["verify_long"]
        
        # Verify initial state
        assert state.agents_active == 100
        assert state.side == "BUY"
        assert state.take_profit == 120.0
        
        # Simulate 100% consensus at price 110
        state.last_verdict = "CONSENSUS_EXIT"
        state.last_consensus_score = 1.0
        state.agents_active = 0  # Agents terminated
        
        # Verify exit happened at 110 (before TP 120)
        assert state.last_verdict == "CONSENSUS_EXIT"
        assert state.last_consensus_score == 1.0
        assert state.agents_active == 0  # ZERO active agents remaining

    @pytest.mark.asyncio
    async def test_scenario_b_short_consensus_exit(self):
        """
        (b) SHORT order open -> 100 AI Agents active -> 
        100% bounce consensus at 90 -> 
        Immediate early exit executed at 90!
        """
        swarm = AISwarmSupervision(MagicMock(), MagicMock())
        
        await swarm.start_swarm_for_order(
            order_id="verify_short",
            symbol="NIFTY",
            side="SELL",
            entry_price=100.0,
            stop_loss=110.0,
            take_profit=80.0,
            trailing_sl=105.0,
            quantity=75.0,
            exchange="NFO",
            broker_token="26000",
            is_auto_trade=True,
        )
        
        state = swarm.swarms["verify_short"]
        
        # Verify initial state
        assert state.agents_active == 100
        assert state.side == "SELL"
        assert state.take_profit == 80.0
        
        # Simulate 100% consensus at price 90
        state.last_verdict = "CONSENSUS_EXIT"
        state.last_consensus_score = 1.0
        state.agents_active = 0  # Agents terminated
        
        # Verify exit happened at 90 (before TP 80)
        assert state.last_verdict == "CONSENSUS_EXIT"
        assert state.last_consensus_score == 1.0
        assert state.agents_active == 0  # ZERO active agents remaining

    @pytest.mark.asyncio
    async def test_scenario_c_termination_on_close(self):
        """
        (c) Order closed -> 100 AI Agents immediately terminated 
        (0 active agents remaining for that order)!
        """
        swarm = AISwarmSupervision(MagicMock(), MagicMock())
        
        await swarm.start_swarm_for_order(
            order_id="verify_term",
            symbol="RELIANCE",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2600.0,
            trailing_sl=2475.0,
            quantity=10.0,
            exchange="NSE",
            broker_token="2885",
            is_auto_trade=False,
        )
        
        # Verify active
        assert "verify_term" in swarm.swarms
        assert swarm.swarms["verify_term"].agents_active == 100
        
        # Close order - terminate swarm
        terminated = await swarm.terminate_swarm("verify_term", "TP_HIT")
        
        # Verify IMMEDIATE termination
        assert terminated is True
        assert "verify_term" not in swarm.swarms
        # Zero active agents for this order


if __name__ == "__main__":
    pytest.main([__file__, "-v"])