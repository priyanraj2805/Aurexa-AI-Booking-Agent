from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from app.engine.agent import tool_contracts


@tool
async def save_field_value(field_name: str, value: str, config: RunnableConfig) -> str:
    """
    Save an extracted piece of data that the business needs (like name, email, phone).
    Use this tool as soon as the user provides necessary information.
    
    Args:
        field_name: The exact name of the field (must match requested fields).
        value: The extracted value.
    """
    contract_result = tool_contracts.validate_field_save_args(
        {"field_name": field_name, "value": value}
    )
    if not contract_result["ok"]:
        return str(contract_result["error"])

    normalized = contract_result["normalized"]
    normalized_field_name = normalized["field_name"]
    normalized_value = normalized["value"]

    db = config.get("configurable", {}).get("db")
    session_id = config.get("configurable", {}).get("thread_id") or config.get(
        "configurable", {}
    ).get("session_id")
    db_lock = config.get("configurable", {}).get("db_lock")

    if not db or not session_id or not db_lock:
        return "System error: Could not access database context or session context."

    from app.crud import chat as crud_chat

    async with db_lock:
        # Fetch the actual chat session
        chat_session = await crud_chat.get_chat_session(db, session_id)
        if not chat_session:
            return "System error: Chat session not found."

        # Validate against field_definitions if needed
        # For now, just save directly into collected_data
        if not isinstance(chat_session.collected_data, dict):
            chat_session.collected_data = {}
            
        chat_session.collected_data[normalized_field_name] = normalized_value
        await crud_chat.update_chat_session(db, session_id, chat_session)

    return f"SUCCESS: Saved {normalized_field_name} = {normalized_value}"
