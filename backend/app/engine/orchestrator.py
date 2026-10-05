import os
import json
import re
import time
import httpx
import asyncio
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Literal
import logging
from asgi_correlation_id.context import correlation_id

from app.db.database import AsyncSessionLocal
from app.crud.monitoring import create_system_alert
from app.schemas.monitoring import AlertComponent, AlertSeverity, SystemAlertCreate

from app.engine.prompts import PromptGenerator
from app.schemas.agent import ChatSessionState
from app.core.config import settings

logger = logging.getLogger(__name__)

VALID_INTENTS = ["START_BOOKING", "PROVIDE_DATA", "EDIT_DATA", "CANCEL_BOOKING", "CHECK_BOOKING", "ASK_QUESTION", "CONFIRM_BOOKING"]
REQUIRED_RESPONSE_KEYS = {"intent", "extracted_entities", "response_message"}

ProviderOutcomeStatus = Literal["success", "retryable_failure", "terminal_failure"]


@dataclass(frozen=True)
class ProviderOutcome:
    status: ProviderOutcomeStatus
    content: Optional[str] = None
    error: Optional[Exception] = None


class LLMOrchestrator:
    """
    Connects to the external LLM interface (Groq/Llama3 by default) and 
    executes the prompts.
    """
    def __init__(self):
        self.api_key = settings.LLM_API_KEY
        base = str(settings.LLM_BASE_URL or "").strip().rstrip("/")
        if not base.endswith("/chat/completions"):
            base = f"{base}/chat/completions"
        self.base_url = base
        self.model = settings.LLM_MODEL
        self.client = httpx.AsyncClient(timeout=30.0)
        self.max_retries = 3
        self.max_backoff_seconds = 8.0
        # Total wall-clock budget per logical LLM call. The per-attempt
        # 30s client timeout is routinely hit by slow providers, so 3 blind
        # retries could burn ~94s on one call — cap the whole call instead.
        self.max_total_seconds = 45.0

    async def aclose(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _classify_provider_failure(err: Exception) -> ProviderOutcomeStatus:
        if isinstance(err, httpx.TimeoutException):
            return "retryable_failure"
        if isinstance(err, (httpx.ConnectError, httpx.ReadError, httpx.WriteError)):
            return "retryable_failure"
        if isinstance(err, httpx.HTTPStatusError):
            status_code = err.response.status_code
            if status_code == 429 or status_code >= 500:
                return "retryable_failure"
            return "terminal_failure"
        return "terminal_failure"

    def _provider_outcome_from_exception(self, err: Exception) -> ProviderOutcome:
        return ProviderOutcome(
            status=self._classify_provider_failure(err),
            error=err,
        )

    async def _emit_llm_reliability_alert(
        self,
        *,
        code: str,
        message: str,
        context: Optional[dict] = None,
    ) -> None:
        request_id = correlation_id.get()
        async with AsyncSessionLocal() as db:
            await create_system_alert(
                db,
                SystemAlertCreate(
                    component=AlertComponent.LLM,
                    severity=AlertSeverity.CRITICAL,
                    code=code,
                    message=message,
                    request_id=request_id,
                    trace_id=None,
                    context=context,
                ),
            )

    async def _call_llm(self, prompt: str, system_prompt: str = "You are a helpful assistant.", json_mode: bool = False) -> Optional[str]:
        """Generic wrapper with exponential backoff retry."""
        if not self.api_key:
            logger.warning("No LLM API Key found. Returning mock response.")
            return None
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
        }
        
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error = None
        deadline = time.monotonic() + self.max_total_seconds
        for attempt in range(self.max_retries):
            remaining = deadline - time.monotonic()
            if remaining <= 2.0:
                logger.warning(
                    "LLM retry budget exhausted after attempt %s/%s; failing fast",
                    attempt,
                    self.max_retries,
                )
                break
            try:
                response = await self.client.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=min(30.0, remaining),
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"].strip()
                logger.debug(f"LLM response received ({len(content)} chars)")
                return ProviderOutcome(status="success", content=content).content
            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException, httpx.ReadError, httpx.WriteError) as e:
                outcome = self._provider_outcome_from_exception(e)
                last_error = outcome.error
                is_last_attempt = attempt >= (self.max_retries - 1)
                if outcome.status == "terminal_failure" or is_last_attempt:
                    logger.error(
                        "LLM API terminal failure on attempt %s/%s: %s",
                        attempt + 1,
                        self.max_retries,
                        e,
                    )
                    try:
                        await self._emit_llm_reliability_alert(
                            code="LLM_PROVIDER_DOWN",
                            message="LLM provider call failed with terminal/exhausted failure",
                            context={
                                "attempt_count": attempt + 1,
                                "max_retries": self.max_retries,
                                "failure_type": outcome.status,
                                "error": str(e),
                            },
                        )
                    except Exception as alert_err:
                        logger.warning("llm.alert.emit.failed: %s", alert_err)
                    break
                wait_time = min(self.max_backoff_seconds, (2 ** attempt) + 0.5)
                if time.monotonic() + wait_time >= deadline:
                    logger.warning(
                        "LLM retry budget exhausted; failing fast without sleeping (attempt %s/%s)",
                        attempt + 1,
                        self.max_retries,
                    )
                    break
                logger.warning(f"LLM API attempt {attempt + 1}/{self.max_retries} failed: {e}. Retrying in {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
            except Exception as e:
                logger.error(f"LLM API unexpected error: {e}")
                return None

        logger.error(f"LLM API failed after {self.max_retries} retries: {last_error}")
        return None

    @staticmethod
    def _validate_unified_response(parsed: Dict[str, Any]) -> Dict[str, Any]:
        """Validates and sanitizes the LLM response structure."""
        # Ensure all required keys exist
        if not REQUIRED_RESPONSE_KEYS.issubset(parsed.keys()):
            missing = REQUIRED_RESPONSE_KEYS - parsed.keys()
            logger.warning(f"LLM response missing keys: {missing}")
            for key in missing:
                if key == "intent":
                    parsed["intent"] = "PROVIDE_DATA"
                elif key == "extracted_entities":
                    parsed["extracted_entities"] = {}
                elif key == "response_message":
                    parsed["response_message"] = "Could you repeat that please?"

        # Validate intent is one of the valid options
        if parsed.get("intent") not in VALID_INTENTS:
            logger.warning(f"LLM returned invalid intent: {parsed.get('intent')}. Defaulting to PROVIDE_DATA.")
            parsed["intent"] = "PROVIDE_DATA"

        # Ensure extracted_entities is a dict
        if not isinstance(parsed.get("extracted_entities"), dict):
            parsed["extracted_entities"] = {}

        # Ensure response_message is a string
        if not isinstance(parsed.get("response_message"), str):
            parsed["response_message"] = str(parsed.get("response_message", ""))

        return parsed

    async def process_turn(
        self,
        user_message: str,
        missing_fields: List[str],
        collected_data: Dict[str, Any],
        current_intent: str,
        persona: Optional[Dict[str, Any]] = None,
        field_definitions: Optional[list] = None,
        org_id: Optional[str] = None,
        db: Optional[Any] = None,
        business_name: Optional[str] = None,
        org_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Unified Orchestration Call.
        Returns a dictionary containing:
        - "intent"
        - "extracted_entities"
        - "response_message"

        When ``org_id`` and ``db`` are provided, performs vectorless RAG
        (PostgreSQL BM25 + LLM rerank) over uploaded documents and crawled
        website content for this organization.
        """
        rag_context = None
        if org_id and db is not None:
            try:
                from app.services.retrieval_service import retrieve_and_rerank
                rag_context = await retrieve_and_rerank(
                    db=db,
                    org_id=str(org_id),
                    query=user_message,
                    top_k=5,
                    candidate_k=20,
                )
            except Exception as _rag_err:
                logger.warning(f"RAG retrieval skipped due to error: {_rag_err}")
                rag_context = None

        prompt = PromptGenerator.build_unified_turn_prompt(
            user_message,
            missing_fields,
            collected_data,
            current_intent,
            persona=persona,
            field_definitions=field_definitions,
            rag_context=rag_context,
            business_name=business_name,
            org_name=org_name,
        )

        result = await self._call_llm(
            prompt,
            system_prompt="You are a professional AI Booking Assistant Core.",
            json_mode=True
        )
        
        if not result:
            return {
                "intent": current_intent or "PROVIDE_DATA",
                "extracted_entities": {},
                "response_message": "I'm having a bit of trouble connecting to my brain. Can you please repeat that?"
            }
            
        try:
            clean_result = result
            if "```json" in result:
                clean_result = result.split("```json")[-1].split("```")[0].strip()
            elif "```" in result:
                clean_result = result.split("```")[-1].split("```")[0].strip()

            # Providers sometimes wrap the object in prose despite the
            # json_object request — salvage the first {...} span before parsing.
            match = re.search(r"\{.*\}", clean_result, re.DOTALL)
            if match:
                clean_result = match.group(0)
            parsed = json.loads(clean_result)
            return self._validate_unified_response(parsed)
        except json.JSONDecodeError:
            logger.error(f"Failed to decode Unified Extraction JSON: {result}")
            return {
                "intent": current_intent or "PROVIDE_DATA",
                "extracted_entities": {},
                "response_message": "I'm sorry, I'm finding that a bit hard to process. Could you rephrase it for me?"
            }

    async def classify_intent(self, user_message: str, current_state: ChatSessionState) -> str:
        """Determines the user's intent."""
        prompt = PromptGenerator.build_intent_prompt(user_message, current_state.model_dump(mode="json"))
        result = await self._call_llm(
            prompt, 
            system_prompt="You are a strict text classification engine.",
            json_mode=False
        )
        
        valid_intents = ["START_BOOKING", "PROVIDE_DATA", "EDIT_DATA", "CANCEL_BOOKING", "CHECK_BOOKING", "ASK_QUESTION", "CONFIRM_BOOKING"]
        
        if not result or result not in valid_intents:
            # Fallback heuristic if LLM fails or hallucinates
            logger.warning(f"LLM intent failed or hallucinated: {result}. Defaulting to PROVIDE_DATA.")
            return "PROVIDE_DATA"
            
        return result

    async def extract_entities(self, user_message: str, missing_fields: List[str], collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extracts JSON entities from the message."""
        if not missing_fields:
            return {}
            
        prompt = PromptGenerator.build_entity_extraction_prompt(user_message, missing_fields, collected_data)
        result = await self._call_llm(
            prompt,
            system_prompt="You are a strict data extraction engine.",
            json_mode=True
        )
        
        if not result:
            return {}
            
        try:
            # Robust parsing: Local models often wrap JSON in markdown blocks
            clean_result = result
            if "```json" in result:
                clean_result = result.split("```json")[-1].split("```")[0].strip()
            elif "```" in result:
                clean_result = result.split("```")[-1].split("```")[0].strip()

            # Same prose-wrapping salvage as process_turn above.
            match = re.search(r"\{.*\}", clean_result, re.DOTALL)
            if match:
                clean_result = match.group(0)
            extracted = json.loads(clean_result)
            return extracted
        except json.JSONDecodeError:
            logger.error(f"Failed to decode LLM extraction JSON: {result}")
            return {}
