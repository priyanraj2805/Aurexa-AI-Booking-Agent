from typing import Dict, Any, List, Optional
from app.schemas.agent import FieldDefinition

class StateEvaluator:
    """
    The core Python logic that evaluates the 'Sweet Spot'.
    It strictly diffs the required parameters against what the LLM has extracted.
    """
    
    # Maps field TYPE → widget directive (not field NAME)
    TYPE_DIRECTIVE_MAP = {
        "date": "REQUIRE_DATE_PICKER",
        "time": "REQUIRE_TIME_SLOT_GRID",
        "phone": "REQUIRE_PHONE_INPUT",
        "email": "REQUIRE_EMAIL_INPUT",
        "select": "REQUIRE_SELECT",
        "textarea": "REQUIRE_TEXTAREA",
        "number": "REQUIRE_NUMBER_INPUT",
    }

    @staticmethod
    def calculate_missing_fields(
        required_fields: List[str], 
        collected_data: Dict[str, Any]
    ) -> List[str]:
        """
        Calculates the delta between what the tenant requires and what the 
        chat session currently holds.
        """
        missing = []
        for field in required_fields:
            if field not in collected_data or collected_data[field] is None:
                missing.append(field)
            elif isinstance(collected_data[field], str) and not collected_data[field].strip():
                missing.append(field)
                
        return missing

    @staticmethod
    def determine_next_action(
        missing_fields: List[str],
        current_intent: str,
        field_definitions: Optional[List[FieldDefinition]] = None
    ) -> Dict[str, Any]:
        """
        Determines the structural 'directive' for the frontend based on missing fields.
        Uses field type from definitions to pick the right widget directive.
        Returns field_meta with rich metadata for the widget to render.
        """
        if not missing_fields and current_intent != "CONFIRM_BOOKING":
             return {"directive": "REQUEST_CONFIRMATION", "focus": "all", "field_meta": None}
             
        if not missing_fields and current_intent == "CONFIRM_BOOKING":
            return {"directive": "PROCESS_BOOKING", "focus": "none", "field_meta": None}
            
        primary_missing = missing_fields[0]
        
        # Build a lookup from field name → FieldDefinition
        fd_map: Dict[str, FieldDefinition] = {}
        if field_definitions:
            fd_map = {fd.name: fd for fd in field_definitions}

        fd = fd_map.get(primary_missing)
        
        if fd:
            directive = StateEvaluator.TYPE_DIRECTIVE_MAP.get(fd.type, "TEXT_INPUT")
            field_meta = {
                "name": fd.name,
                "type": fd.type,
                "label": fd.label,
                "placeholder": fd.placeholder,
                "description": fd.description,
                "icon": fd.icon,
                "options": fd.options if fd.type == "select" else [],
            }
        else:
            # Fallback for unknown fields — legacy compat
            directive = StateEvaluator.TYPE_DIRECTIVE_MAP.get(primary_missing, "TEXT_INPUT")
            field_meta = {
                "name": primary_missing,
                "type": "text",
                "label": primary_missing.replace("_", " ").title(),
                "placeholder": "",
                "description": "",
                "icon": "📋",
                "options": [],
            }
        
        return {
            "directive": directive,
            "focus": primary_missing,
            "field_meta": field_meta,
        }

    @staticmethod
    def validate_directive_payload(
        directive: str,
        field_meta: Optional[Dict[str, Any]],
    ) -> tuple[bool, Optional[str]]:
        if directive in {"REQUEST_CONFIRMATION", "PROCESS_BOOKING", "TEXT_INPUT"}:
            return True, None
        if not isinstance(field_meta, dict):
            return False, "missing_field_meta"
        field_name = str(field_meta.get("name", "")).strip()
        field_type = str(field_meta.get("type", "")).strip()
        if not field_name or not field_type:
            return False, "missing_field_metadata"
        if directive == "REQUIRE_SELECT":
            options = field_meta.get("options")
            if not isinstance(options, list) or len(options) == 0:
                return False, "invalid_select_options"
        return True, None
