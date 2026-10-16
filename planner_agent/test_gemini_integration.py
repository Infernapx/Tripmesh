import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import HumanMessage
from shared.gemini_client import (
    is_gemini_configured,
    generate_gemini_content,
    generate_conversational_travel_response,
    generate_local_guide_highlight
)
from planner_agent.app.graph import render_conversational_node


@pytest.mark.anyio
async def test_gemini_client_fallback_when_no_key(monkeypatch):
    """Ensure gemini_client gracefully returns None when no key is set."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    
    assert not is_gemini_configured()
    res = await generate_gemini_content("Hello")
    assert res is None


@pytest.mark.anyio
async def test_gemini_client_mock_success(monkeypatch):
    """Ensure generate_gemini_content formats requests correctly and extracts candidates."""
    from unittest.mock import MagicMock
    monkeypatch.setenv("GEMINI_API_KEY", "mock-test-key")
    
    mock_response_data = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Mumbai is known for its vibrant street food and coastal sea breeze."}]
                }
            }
        ]
    }
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response_data

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        
        result = await generate_gemini_content("Tell me about Mumbai")
        assert result == "Mumbai is known for its vibrant street food and coastal sea breeze."


@pytest.mark.anyio
async def test_render_conversational_node_with_gemini():
    """Verify render_conversational_node utilizes Gemini output when returned."""
    state = {
        "session_id": "test-session",
        "destination": "BOM",
        "destination_name": "Mumbai",
        "origin": "DEL",
        "origin_name": "Delhi",
        "messages": [HumanMessage(content="What should I pack for Mumbai?")]
    }

    with patch(
        "planner_agent.app.graph.generate_conversational_travel_response",
        new_callable=AsyncMock
    ) as mock_llm:
        mock_llm.return_value = "Pack lightweight cotton clothes, an umbrella, and comfortable walking shoes for Mumbai."
        
        res = await render_conversational_node(state)
        assert len(res["messages"]) == 1
        assert "cotton clothes" in res["messages"][0].content
        assert res["invoked_nodes"] == ["render_conversational"]


@pytest.mark.anyio
async def test_render_conversational_node_fallback_when_gemini_unavailable():
    """Verify render_conversational_node falls back gracefully if LLM returns None."""
    state = {
        "session_id": "test-session",
        "destination": "BOM",
        "destination_name": "Mumbai",
        "origin": "DEL",
        "origin_name": "Delhi",
        "messages": [HumanMessage(content="What is the weather like?")]
    }

    with patch(
        "planner_agent.app.graph.generate_conversational_travel_response",
        new_callable=AsyncMock
    ) as mock_llm:
        mock_llm.return_value = None
        
        res = await render_conversational_node(state)
        assert len(res["messages"]) == 1
        assert "Mumbai" in res["messages"][0].content
        assert "looking at your trip" in res["messages"][0].content
