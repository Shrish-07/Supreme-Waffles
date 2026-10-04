"""Shared builder for the internal specialist agents (Visit / Claim / Dispute).

Specialists are NOT published (no chat protocol, no manifest, no README); only
the orchestrator is discoverable. Each request is handled by a pure logic
function in a worker thread (the logic makes blocking HTTPS calls to Core).
"""

import asyncio
from collections.abc import Callable

from uagents import Agent, Context, Model

from claimshield_contracts.enums import AgentName

from .core_client import CoreClient
from .protocols import protocol
from .settings import AgentSettings, require_seed

Handler = Callable[[CoreClient, Model], Model]


def build_specialist(
    *,
    name: str,
    agent_name: AgentName,
    seed_env: str,
    port: int,
    protocol_name: str,
    request_model: type[Model],
    response_model: type[Model],
    handle: Handler,
    settings: AgentSettings,
    tools: CoreClient | None = None,
    mailbox: bool = True,
    agentverse: str | None = None,
) -> Agent:
    agent = Agent(
        name=name,
        seed=require_seed(seed_env),
        port=port,
        mailbox=mailbox,
        agentverse=agentverse,
        publish_agent_details=False,
    )
    client = tools or CoreClient(settings.core_base_url, settings.fetch_token, agent=agent_name)
    proto = protocol(protocol_name)

    @proto.on_message(request_model, replies=response_model)
    async def on_request(ctx: Context, sender: str, msg: Model) -> None:
        ctx.logger.info(f"{request_model.__name__} for case {msg.case_id}")
        response = await asyncio.to_thread(handle, client.for_request(msg.request_id), msg)
        await ctx.send(sender, response)

    agent.include(proto)
    return agent
