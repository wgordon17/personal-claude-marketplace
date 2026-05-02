"""Vertex AI judge adapter for DeepEval skill evaluation.

Uses claude-sonnet-4-6 via AnthropicVertex for LLM-as-judge assessments.
Reads ANTHROPIC_VERTEX_PROJECT_ID and CLOUD_ML_REGION env vars (default: global).

Multi-trial averaging (K-trial):
  When k_samples > 1 and a schema is requested (the GEval scoring path),
  the judge runs K independent calls at eval_temperature **concurrently**
  via ThreadPoolExecutor and averages the numeric scores. This produces
  continuous values from discrete integer outputs, matching the G-Eval
  paper's Monte Carlo approach for models without logprob access (Claude
  does not expose logprobs).

  Skill execution calls (schema=None) always use a single call at
  temperature=0 for deterministic output.
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import instructor
from deepeval.models.base_model import DeepEvalBaseLLM
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS_EXECUTE = 128000  # Set high to avoid truncation; API caps at model's actual limit.
_MAX_TOKENS_SCORE = 4096  # Scoring — ReasonScore JSON is small.

# Defaults for multi-trial averaging.
_DEFAULT_K_SAMPLES = 5
_DEFAULT_EVAL_TEMPERATURE = 0.7


class VertexSonnetJudge(DeepEvalBaseLLM):
    """DeepEval-compatible LLM judge backed by claude-sonnet-4-6 on Vertex AI.

    Args:
        k_samples: Number of independent scoring calls to average per evaluation.
            Only applies when schema is provided (GEval scoring path). Default 5.
            Set to 1 to disable multi-trial averaging (legacy behavior).
        eval_temperature: Temperature for scoring calls when k_samples > 1.
            Higher values produce more diverse samples for better averaging.
            Default 0.7 (compromise between variance and quality).
    """

    def __init__(
        self,
        k_samples: int = _DEFAULT_K_SAMPLES,
        eval_temperature: float = _DEFAULT_EVAL_TEMPERATURE,
    ) -> None:
        self._k_samples = k_samples
        self._eval_temperature = eval_temperature
        project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
        region = os.environ.get("CLOUD_ML_REGION", "global")
        try:
            self.client = anthropic.AnthropicVertex(
                project_id=project_id,
                region=region,
                timeout=600.0,
            )
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

    def _single_generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
        temperature: float = 0,
    ) -> str | BaseModel:
        """Single LLM call — the building block for both direct and multi-trial modes."""
        if schema is None:
            # Skill execution — needs room for full structured output.
            try:
                message = self.client.messages.create(
                    model=_MODEL,
                    max_tokens=_MAX_TOKENS_EXECUTE,
                    temperature=temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as e:
                raise RuntimeError(
                    f"Vertex AI judge call failed ({type(e).__name__}): {e}"
                ) from None

            if message.stop_reason == "max_tokens":
                logger.warning(
                    "TRUNCATED: skill execution hit %d token limit"
                    " — output is incomplete, scores will be unreliable."
                    " Increase _MAX_TOKENS_EXECUTE in judge.py",
                    _MAX_TOKENS_EXECUTE,
                )

            text = ""
            for block in message.content:
                if hasattr(block, "text"):
                    text = block.text
                    break
            return text
        else:
            # Scoring — ReasonScore JSON is small.
            try:
                return self.instructor_client.messages.create(
                    model=_MODEL,
                    max_tokens=_MAX_TOKENS_SCORE,
                    temperature=temperature,
                    messages=[{"role": "user", "content": prompt}],
                    response_model=schema,
                )
            except Exception as e:
                raise RuntimeError(
                    f"Vertex AI judge call failed ({type(e).__name__}): {e}"
                ) from None

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> str | BaseModel:
        """Generate a response, optionally structured against a Pydantic schema.

        When schema is provided and k_samples > 1, runs K independent calls
        at eval_temperature and returns a schema instance with the averaged
        score and the reason from the highest-scoring trial.

        Args:
            prompt: The evaluation prompt to send to the judge.
            schema: When provided, returns a validated BaseModel instance via
                instructor structured output. When None, returns raw string response.

        Returns:
            str when schema is None; BaseModel instance when schema is provided.
        """
        # Skill execution (no schema) — always single call at temperature=0.
        if schema is None:
            return self._single_generate(prompt, schema=None, temperature=0)

        # Scoring path with single trial — legacy behavior.
        if self._k_samples <= 1:
            return self._single_generate(prompt, schema=schema, temperature=0)

        # Multi-trial averaging: run K times at eval_temperature concurrently.
        scores: list[float] = []
        best_reason = ""
        best_score = -1.0

        def _run_trial(trial_idx: int) -> BaseModel | None:
            try:
                return self._single_generate(
                    prompt, schema=schema, temperature=self._eval_temperature
                )
            except Exception:
                logger.warning(
                    "Multi-trial sample %d/%d failed — skipping",
                    trial_idx + 1,
                    self._k_samples,
                )
                return None

        with ThreadPoolExecutor(max_workers=self._k_samples) as pool:
            futures = [pool.submit(_run_trial, i) for i in range(self._k_samples)]
            for future in as_completed(futures):
                result = future.result()
                if result is not None and hasattr(result, "score"):
                    score = float(result.score)
                    scores.append(score)
                    if score > best_score:
                        best_score = score
                        best_reason = getattr(result, "reason", "")

        if not scores:
            # All trials failed — fall back to single deterministic call.
            logger.warning(
                "All %d multi-trial samples failed — falling back to single call",
                self._k_samples,
            )
            return self._single_generate(prompt, schema=schema, temperature=0)

        avg_score = sum(scores) / len(scores)
        logger.debug(
            "Multi-trial scores (%d/%d succeeded): %s → avg=%.3f",
            len(scores),
            self._k_samples,
            [f"{s:.1f}" for s in scores],
            avg_score,
        )

        # Return schema instance with averaged score and best reason.
        return schema(score=avg_score, reason=best_reason)

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> str | BaseModel:
        """Async counterpart to generate(); delegates via run_in_executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.generate, prompt, schema)

    def get_model_name(self) -> str:
        return "vertex-sonnet-judge"
