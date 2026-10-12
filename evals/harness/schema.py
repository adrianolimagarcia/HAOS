"""Schema declarativo para cenários de avaliação determinística do HAOS (Fase 7).

Cenários são dados puros em JSON validado por schema, sem qualquer código executável na fixture.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AssertionTarget(str, Enum):
    GBRAIN_GATE = "gbrain_gate"
    ROUTER = "router"
    CANDIDATE_STATUS = "candidate_status"
    STAGING_REASON = "staging_reason"
    EVIDENCE_STATUS = "evidence_status"
    DESTINATION = "destination"


class EvalTurn(BaseModel):
    """Representa um turno no cenário, com mensagem de entrada e respostas simuladas."""
    user_input: str
    mock_response: Optional[str] = None
    mock_tool_calls: Optional[List[Dict[str, Any]]] = None
    extracted_fact: Optional[str] = None
    source_uri: Optional[str] = None
    confidence: float = 0.9
    evidence_attestation: Optional[Dict[str, Any]] = None


class EvalAssertion(BaseModel):
    """Asserção determinística sobre as decisões tomadas pelo sistema."""
    target: AssertionTarget
    expected_value: Any
    description: Optional[str] = None


class EvalScenario(BaseModel):
    """Cenário puro de avaliação."""
    name: str
    description: str
    tags: List[str] = Field(default_factory=list)
    initial_state: Dict[str, Any] = Field(default_factory=dict)
    turns: List[EvalTurn] = Field(default_factory=list)
    assertions: List[EvalAssertion] = Field(default_factory=list)
