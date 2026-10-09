"""Reusable agent templates: a YAML file defines prompt, tool allow-list and policies for a new team's agent."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Policies(BaseModel):
    max_steps: int = Field(default=6, ge=1, le=20)
    max_output_tokens: int = Field(default=700, ge=64, le=4000)
    require_citations: bool = True
    approval_required_tools: list[str] = Field(default_factory=list)  # documented here, enforced by tool risk


class TriageConfig(BaseModel):
    enabled: bool = True
    prompt: str = (
        "Classify the IT request. Reply with JSON only: "
        '{"category": one of [access, vpn, password, hardware, software, network, security, licensing, general], '
        '"priority": one of [low, medium, high, urgent], "needs_human": boolean}.'
    )


class AgentTemplate(BaseModel):
    name: str
    description: str = ""
    provider: str | None = None  # None = platform default (OPSPILOT_LLM_PROVIDER)
    system_prompt: str
    tools: list[str]
    policies: Policies = Field(default_factory=Policies)
    triage: TriageConfig = Field(default_factory=TriageConfig)

    @classmethod
    def load(cls, path: Path) -> AgentTemplate:
        return cls.model_validate(yaml.safe_load(path.read_text()))


def load_template(name: str, template_dir: Path) -> AgentTemplate:
    path = template_dir / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in template_dir.glob("*.yaml"))
        raise FileNotFoundError(f"No agent template '{name}'. Available: {available}")
    return AgentTemplate.load(path)
