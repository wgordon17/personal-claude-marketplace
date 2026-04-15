"""Vertex AI judge adapter for DeepEval skill evaluation.

Uses claude-sonnet-4-6 via AnthropicVertex for LLM-as-judge assessments.
Reads ANTHROPIC_VERTEX_PROJECT_ID and CLOUD_ML_REGION env vars.
"""

import asyncio
import os

import anthropic
import instructor
from deepeval.models.base_model import DeepEvalBaseLLM
from pydantic import BaseModel

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096


class VertexSonnetJudge(DeepEvalBaseLLM):
    """DeepEval-compatible LLM judge backed by claude-sonnet-4-6 on Vertex AI."""

    def __init__(self) -> None:
        project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
        region = os.environ.get("CLOUD_ML_REGION", "us-east5")
        try:
            self.client = anthropic.AnthropicVertex(project_id=project_id, region=region)
        except Exception:
            raise RuntimeError(
                "Vertex AI judge init failed: check ANTHROPIC_VERTEX_PROJECT_ID"
                " and CLOUD_ML_REGION env vars"
            ) from None
        self.instructor_client = instructor.from_anthropic(
            self.client, mode=instructor.Mode.ANTHROPIC_TOOLS
        )
        # Call super().__init__() after self.client is set so load_model() can return self.
        super().__init__()

    def load_model(self) -> "VertexSonnetJudge":
        return self

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> str | BaseModel:
        """Generate a response, optionally structured against a Pydantic schema.

        Args:
            prompt: The evaluation prompt to send to the judge.
            schema: When provided, returns a validated BaseModel instance via
                instructor structured output. When None, returns raw string response.

        Returns:
            str when schema is None; BaseModel instance when schema is provided.
        """
        if schema is None:
            try:
                message = self.client.messages.create(
                    model=_MODEL,
                    max_tokens=_MAX_TOKENS,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as e:
                raise RuntimeError(
                    f"Vertex AI judge call failed ({type(e).__name__}):"
                    " check credentials and network connectivity"
                ) from None
            text = ""
            for block in message.content:
                if hasattr(block, "text"):
                    text = block.text
                    break
            return text
        else:
            try:
                return self.instructor_client.messages.create(
                    model=_MODEL,
                    max_tokens=_MAX_TOKENS,
                    messages=[{"role": "user", "content": prompt}],
                    response_model=schema,
                )
            except Exception as e:
                raise RuntimeError(
                    f"Vertex AI judge call failed ({type(e).__name__}):"
                    " check credentials and network connectivity"
                ) from None

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> str | BaseModel:
        """Async counterpart to generate(); delegates via run_in_executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.generate, prompt, schema)

    def get_model_name(self) -> str:
        return "vertex-sonnet-judge"
