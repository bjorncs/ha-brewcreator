"""LLM tools of the BrewCreator integration.

Offers the integration's intents to LLM agents as tools, through the LLM integration. Its Assist API (Device control)
gives them to conversation agents, and the MCP Server integration to MCP clients.
"""

from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.llm import LLM_API_ASSIST, IntentTool, LLMContext, Tool, ToolAnnotations

from .api import BATCH_INFO_EQUIPMENT, SUPPORTED_EQUIPMENT, EquipmentTypes, Ferminator
from .const import DOMAIN
from .intent import (
    INTENT_GET_FERMENTATION_STATUS,
    INTENT_LINK_HYDROMETER,
    INTENT_SET_BATCH_INFO,
    exposed_equipment,
)

# The intents only act on the user's own BrewCreator equipment. Setting the same batch info or hydrometer again has
# no further effect.
_READ_ONLY = ToolAnnotations(read_only=True, destructive=False, idempotent=True, open_world=False)
_CONTROL = ToolAnnotations(idempotent=True, open_world=False)

# Intent type: tool name without the domain prefix, title, annotations, and the equipment it needs exposed
LLM_INTENTS: dict[str, tuple[str, str, ToolAnnotations, EquipmentTypes]] = {
    INTENT_GET_FERMENTATION_STATUS: (
        "GetFermentationStatus",
        "Get fermentation status",
        _READ_ONLY,
        SUPPORTED_EQUIPMENT,
    ),
    INTENT_SET_BATCH_INFO: ("SetBatchInfo", "Set batch info", _CONTROL, BATCH_INFO_EQUIPMENT),
    INTENT_LINK_HYDROMETER: ("LinkHydrometer", "Link hydrometer", _CONTROL, Ferminator),
}


@callback
def async_get_tools(hass: HomeAssistant, llm_context: LLMContext, api_id: str) -> LLMTools | None:
    """Return a tool for each intent when the assistant has BrewCreator equipment exposed to it."""
    if api_id != LLM_API_ASSIST or not llm_context.assistant:
        return None
    available = _exposed_intents(hass, llm_context.assistant)
    tools: list[Tool] = []
    for handler in intent.async_get(hass):
        if handler.intent_type not in available:
            continue
        name, title, annotations, _ = LLM_INTENTS[handler.intent_type]
        tools.append(IntentTool(f"{DOMAIN}__{name}", handler, title=title, integration=DOMAIN, annotations=annotations))
    return LLMTools(tools=tools) if tools else None


def _exposed_intents(hass: HomeAssistant, assistant: str) -> set[str]:
    """Return the intents of LLM_INTENTS whose equipment is exposed to the assistant.

    Called for every request to an agent, so it goes through the exposed equipment once, and stops as soon as every
    intent has some.
    """
    missing = {intent_type: needed for intent_type, (*_, needed) in LLM_INTENTS.items()}
    for equipment in exposed_equipment(hass, assistant):
        for intent_type, needed in list(missing.items()):
            if isinstance(equipment, needed):
                del missing[intent_type]
        if not missing:
            break
    return LLM_INTENTS.keys() - missing.keys()
