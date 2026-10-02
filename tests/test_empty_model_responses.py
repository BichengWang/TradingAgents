"""Empty proxy responses must never produce successful, evidence-free reports."""

import json
from unittest.mock import MagicMock

import anthropic
from anthropic import _base_client as sdk_transport
import pytest
from langchain_core.messages import AIMessage

from tradingagents.agents.schemas import TraderProposal
from tradingagents.agents.structured import invoke_structured_or_freetext
from tradingagents.llm_clients.anthropic_client import NormalizedChatAnthropic
from tradingagents.llm_clients.base_client import (
    EmptyModelResponseError, normalize_content, require_report_text,
)

# Use the installed SDK's transport; newer releases use httpx2.
httpx = getattr(sdk_transport, "httpx2", None) or sdk_transport.httpx


@pytest.mark.unit
@pytest.mark.parametrize("content", ["", " \n", [], [{"type": "thinking", "thinking": "..."}]])
def test_empty_answers_raise(content):
    message = AIMessage(content=content, response_metadata={"stop_reason": "max_tokens"})
    with pytest.raises(EmptyModelResponseError, match="max_tokens"):
        normalize_content(message)


@pytest.mark.unit
def test_tool_only_turn_still_runs_tools():
    message = AIMessage(content="", tool_calls=[{"name": "get_stock_data", "args": {}, "id": "call1"}])
    assert normalize_content(message).tool_calls == message.tool_calls


@pytest.mark.unit
def test_no_tool_agent_cannot_complete_from_a_tool_call():
    message = AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "call1"}])
    with pytest.raises(EmptyModelResponseError, match="expected report text"):
        require_report_text(message, "Custom future model")


@pytest.mark.unit
@pytest.mark.parametrize("block_type", ["text", "output_text"])
def test_text_blocks_remain_usable_after_reasoning_blocks(block_type):
    message = AIMessage(content=[
        {"type": "text", "text": "private reasoning", "thought": True},
        {"type": block_type, "text": "Completed report."},
    ])
    assert require_report_text(message, "Any model") == "Completed report."


@pytest.mark.unit
def test_structured_failure_cannot_fall_back_to_an_empty_report():
    structured = MagicMock()
    structured.invoke.return_value = None
    plain = MagicMock()
    plain.invoke.return_value = AIMessage(content="")
    with pytest.raises(EmptyModelResponseError):
        invoke_structured_or_freetext(structured, plain, "prompt", str, "Trader")


@pytest.mark.unit
def test_anthropic_retries_rejected_schema_tool_with_auto():
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error",
                "message": 'tool_choice: type "tool" and "any" are not supported for this model.',
            }})
        return httpx.Response(200, json={
            "id": "msg_fixture", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "tool_use", "id": "tool1", "name": "TraderProposal",
                         "input": {"action": "Buy", "reasoning": "Evidence supports it."}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        llm = NormalizedChatAnthropic(model="claude-sonnet-5-5", api_key="fixture")
        llm._client = anthropic.Anthropic(api_key="fixture", http_client=client)
        proposal = llm.with_structured_output(TraderProposal).invoke("Use the supplied evidence.")

    assert proposal.action.value == "Buy"
    assert len(requests) == 2
    assert requests[0]["tool_choice"]["type"] in {"tool", "any"}
    assert requests[1]["tool_choice"] == {"type": "auto"}
    assert requests[0]["tools"] == requests[1]["tools"]


@pytest.mark.unit
def test_unrelated_anthropic_400_is_not_retried():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(400, json={"type": "error", "error": {
            "type": "invalid_request_error", "message": "invalid model",
        }})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        llm = NormalizedChatAnthropic(model="claude-sonnet-5-5", api_key="fixture")
        llm._client = anthropic.Anthropic(api_key="fixture", http_client=client)
        with pytest.raises(anthropic.BadRequestError, match="invalid model"):
            llm.with_structured_output(TraderProposal).invoke("prompt")
    assert len(requests) == 1
