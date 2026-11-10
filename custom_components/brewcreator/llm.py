"""LLM tools of the BrewCreator integration.

Offers the integration's intents to LLM agents as tools, through the LLM integration. Its Assist API (Device control)
gives them to conversation agents, and the MCP Server integration to MCP clients.
"""

from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.llm import LLM_API_ASSIST, IntentTool, LLMContext, Tool, ToolAnnotations

from .api import Ferminator, Hydrometer
from .const import DOMAIN
from .intent import (
    INTENT_GET_FERMENTATION_STATUS,
    INTENT_LINK_HYDROMETER,
    INTENT_SET_BATCH_INFO,
    has_exposed,
)

# The intents only act on the user's own BrewCreator equipment. Setting the same batch info or hydrometer again has
# no further effect.
_READ_ONLY = ToolAnnotations(read_only=True, destructive=False, idempotent=True, open_world=False)
_CONTROL = ToolAnnotations(idempotent=True, open_world=False)

# Intent type: tool name without the domain prefix, title, annotations, and whether it needs an exposed Ferminator
LLM_INTENTS = {
    INTENT_GET_FERMENTATION_STATUS: ("GetFermentationStatus", "Get fermentation status", _READ_ONLY, False),
    INTENT_SET_BATCH_INFO: ("SetBatchInfo", "Set batch info", _CONTROL, True),
    INTENT_LINK_HYDROMETER: ("LinkHydrometer", "Link hydrometer", _CONTROL, True),
}


@callback
def async_get_tools(hass: HomeAssistant, llm_context: LLMContext, api_id: str) -> LLMTools | None:
    """Return a tool for each intent when the assistant has BrewCreator equipment exposed to it."""
    if api_id != LLM_API_ASSIST or not llm_context.assistant:
        return None
    # Called for every request to an agent, so it stops at the first exposed equipment
    has_ferminator = has_exposed(hass, llm_context.assistant, Ferminator)
    if not has_ferminator and not has_exposed(hass, llm_context.assistant, Hydrometer):
        return None
    tools: list[Tool] = []
    for handler in intent.async_get(hass):
        if handler.intent_type not in LLM_INTENTS:
            continue
        name, title, annotations, needs_ferminator = LLM_INTENTS[handler.intent_type]
        if needs_ferminator and not has_ferminator:
            continue
        tools.append(
            IntentTool(f"{DOMAIN}__{name}", handler, title=title, integration=DOMAIN, annotations=annotations)
        )
    return LLMTools(tools=tools) if tools else None
