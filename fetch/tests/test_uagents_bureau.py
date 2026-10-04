"""Real uAgents wrappers in a local Bureau (no Agentverse, nothing published).

A scripted "patient" agent talks to the orchestrator over the actual chat
protocol; the orchestrator exchanges typed messages with the specialists, which
call a live Core over HTTP. Skipped when uagents is not installed.
"""

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

pytest.importorskip("uagents")

from uagents import Agent, Bureau, Context, Protocol  # noqa: E402
from uagents_core.contrib.protocols.chat import (  # noqa: E402
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

from fetch.agents.local_direct import DEMO_CONVERSATION  # noqa: E402
from fetch.agents.orchestrator import HELLO_REPLY, build_orchestrator  # noqa: E402
from fetch.agents.run_all import build_bureau  # noqa: E402
from fetch.agents.settings import (  # noqa: E402
    OFFLINE_AGENTVERSE,
    MissingSeedError,
    require_seed,
)

from .conftest import SEEDS, agent_settings, free_port  # noqa: E402

PROMPT_MARKERS = ("Reply", "reply **DONE**")


@pytest.fixture(autouse=True)
def no_agentverse_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """uAgents marks each agent active on Agentverse before running startup handlers,
    retrying 10x with backoff when offline. Skip that network call in tests."""

    async def skip(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("uagents.agent.update_agent_status", skip)


def _chat(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(UTC), msg_id=uuid4(), content=[TextContent(type="text", text=text)]
    )


def _converse(
    build: Callable[[], tuple[list[Agent], str]],
    script: list[str],
    *,
    finished: Callable[[str], bool],
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Run a Bureau (own thread + event loop, like ``bureau.run()``) with a patient agent
    that sends the next scripted line after each prompt. The Bureau cancels every task
    on its loop at shutdown, so it must not share a loop with the test."""
    state: dict[str, Any] = {"next": 1, "replies": [], "acks": 0, "done": False}
    handles: dict[str, Any] = {}
    ready = threading.Event()

    def run_bureau() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        agents, orchestrator_address = build()
        patient = Agent(
            name="test-patient",
            seed="claimshield-test-patient-seed-0001",
            agentverse=OFFLINE_AGENTVERSE,
        )
        chat = Protocol(spec=chat_protocol_spec)

        @chat.on_message(ChatMessage)
        async def on_reply(ctx: Context, sender: str, msg: ChatMessage) -> None:
            await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
            text = msg.text()
            state["replies"].append(text)
            if finished(text):
                state["done"] = True
            elif any(m in text for m in PROMPT_MARKERS) and state["next"] < len(script):
                await ctx.send(orchestrator_address, _chat(script[state["next"]]))
                state["next"] += 1

        @chat.on_message(ChatAcknowledgement)
        async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
            state["acks"] += 1

        patient.include(chat)

        @patient.on_event("startup")
        async def start(ctx: Context) -> None:
            await asyncio.sleep(0.5)
            await ctx.send(orchestrator_address, _chat(script[0]))

        bureau = Bureau(
            agents=[*agents, patient],
            port=free_port(),
            agentverse=OFFLINE_AGENTVERSE,
            shutdown_timeout=5,
            loop=loop,
        )
        handles["loop"], handles["task"] = loop, loop.create_task(bureau.run_async())
        ready.set()
        with contextlib.suppress(BaseException):
            loop.run_until_complete(handles["task"])

    thread = threading.Thread(target=run_bureau, daemon=True)
    thread.start()
    assert ready.wait(30), "bureau did not start"
    deadline = time.time() + timeout_s
    while not state["done"] and time.time() < deadline:
        time.sleep(0.2)
    handles["loop"].call_soon_threadsafe(handles["task"].cancel)
    thread.join(timeout=20)
    return state


def test_missing_seed_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_SEED", raising=False)
    with pytest.raises(MissingSeedError, match="ORCHESTRATOR_SEED is not set"):
        require_seed("ORCHESTRATOR_SEED")


def test_hello_world_mode(agent_env: None) -> None:
    def build() -> tuple[list[Agent], str]:
        orchestrator = build_orchestrator(
            agent_settings(hello_world=True),
            mailbox=False,
            publish=False,
            agentverse=OFFLINE_AGENTVERSE,
        )
        return [orchestrator], orchestrator.address

    state = _converse(build, ["hi"], finished=lambda t: t == HELLO_REPLY, timeout_s=20)
    assert state["done"], state["replies"]
    assert state["acks"] >= 1  # the orchestrator always acknowledges


def test_full_workflow_over_uagents(agent_env: None, live_core: dict[str, Any]) -> None:
    settings = agent_settings(core_base_url=live_core["base_url"])

    def build() -> tuple[list[Agent], str]:
        bureau = build_bureau(settings, online=False)
        agents = list(bureau._agents)  # noqa: SLF001 - reuse the configured agents
        orchestrator = next(a for a in agents if a.name == "claimshield-orchestrator")
        return agents, orchestrator.address

    state = _converse(
        build, list(DEMO_CONVERSATION), finished=lambda t: "Resolved" in t, timeout_s=90
    )
    assert state["done"], state["replies"][-3:]
    case = httpx.get(
        f"{live_core['base_url']}/cases/{live_core['case_id']}", headers=live_core["relay"]
    ).json()
    assert case["state"] == "RESOLVED"
    assert {r["agent"] for r in case["agent_runs"]} >= {
        "visit_agent",
        "claim_agent",
        "dispute_agent",
    }


def test_print_addresses_never_prints_seeds(
    agent_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from fetch.agents.print_addresses import main

    main()
    out = capsys.readouterr().out
    assert "ORCHESTRATOR_ADDRESS=agent1" in out
    assert not any(seed in out for seed in SEEDS.values())
