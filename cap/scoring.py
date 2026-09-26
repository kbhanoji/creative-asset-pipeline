"""Quality scoring (SOW 3.4.5, TDD 9.1).

Two providers, selected by scoring.provider:
  gemini_critic: built-in Gemini-as-a-judge using the SOW rubric and weights.
  gcc_endpoint:  the Creative Studio backend critic endpoint (validate its contract
                 against the pinned GCC version before switching; TDD OD-07).
Both return the same CriticResult, so routing doesn't care which one ran.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import Config

DIMENSIONS = {
    "prompt_adherence": "Every element requested in the prompt is present and correct.",
    "brand_compliance": "Colour palette, logo placement, typography and visual style match the brand rules.",
    "artifact_freedom": "Photorealism; no distortions, deformed product features, pixelation, garbled text or blending glitches. "
                        "Product shape, colour and labels match the reference/product facts.",
    "safety_and_constraints": "No prohibited content or trademark violations; safe tool use and PPE; negative constraints respected.",
}


@dataclass
class CriticResult:
    sub_scores: dict[str, float]        # 0-1 per dimension
    rationale: str
    provider: str
    model: str
    raw: dict[str, Any] = field(default_factory=dict)


def _critic_instructions(cfg: Config, prompt: dict[str, Any]) -> str:
    skills = cfg.skills()
    brand_rules = "\n\n".join(f"## {k}\n{v}" for k, v in skills.items() if k != "prompt-craft")
    dims = "\n".join(f"- {k}: {v}" for k, v in DIMENSIONS.items())
    return f"""You are a strict brand-compliance and image-quality critic for {cfg.customer.brand}.
Score the attached generated image against the generation prompt, the product facts and the brand rules.

Score each dimension from 0.0 (fails completely) to 1.0 (perfect):
{dims}

Also report hallucination_risk from 0 (no defects) to 100 (severe): product distortion, pixelation,
incorrect labelling, deformed features, regional content errors.
Be conservative: if you cannot verify a product detail, do not award full marks.

GENERATION PROMPT:
{prompt.get("prompt_text", "")}

NEGATIVE CONSTRAINTS: {", ".join(prompt.get("negative_constraints") or cfg.brand.negative_constraints)}
SAFETY CONSTRAINTS: {", ".join(cfg.brand.safety_constraints)}

BRAND AND SAFETY RULES:
{brand_rules or "(none supplied)"}
"""


_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        **{k: {"type": "OBJECT", "properties": {"score": {"type": "NUMBER"}, "notes": {"type": "STRING"}},
               "required": ["score", "notes"]} for k in DIMENSIONS},
        "hallucination_risk": {"type": "NUMBER"},
        "rationale": {"type": "STRING"},
    },
    "required": [*DIMENSIONS, "hallucination_risk", "rationale"],
}


def score_image(cfg: Config, image: bytes, mime_type: str, prompt: dict[str, Any],
                reference_images: list[tuple[bytes, str]] | None = None) -> CriticResult:
    if cfg.scoring.provider == "gcc_endpoint":
        return _score_gcc(cfg, image, mime_type, prompt)
    return _score_gemini(cfg, image, mime_type, prompt, reference_images or [])


def _score_gemini(cfg: Config, image: bytes, mime_type: str, prompt: dict[str, Any],
                  refs: list[tuple[bytes, str]]) -> CriticResult:
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=cfg.gcp.project_id, location=cfg.models.model_location)
    parts: list[Any] = [types.Part.from_text(text=_critic_instructions(cfg, prompt)),
                        types.Part.from_text(text="GENERATED IMAGE:"),
                        types.Part.from_bytes(data=image, mime_type=mime_type)]
    for i, (data, mt) in enumerate(refs[:4], 1):
        parts += [types.Part.from_text(text=f"REFERENCE PRODUCT IMAGE {i}:"), types.Part.from_bytes(data=data, mime_type=mt)]
    resp = client.models.generate_content(
        model=cfg.models.critic,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json",
                                           response_schema=_SCHEMA),
    )
    data = json.loads(resp.text)
    subs = {k: max(0.0, min(1.0, float(data[k]["score"]))) for k in DIMENSIONS}
    subs["hallucination_risk"] = max(0.0, min(100.0, float(data["hallucination_risk"])))
    return CriticResult(subs, data.get("rationale", ""), "gemini_critic", cfg.models.critic, data)


def _score_gcc(cfg: Config, image: bytes, mime_type: str, prompt: dict[str, Any]) -> CriticResult:
    """Creative Studio critic. The request/response mapping below is an assumption to confirm in the Stage 1 spike."""
    import base64

    import google.auth.transport.requests
    import google.oauth2.id_token
    import requests

    token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), cfg.scoring.gcc_endpoint_url)
    r = requests.post(cfg.scoring.gcc_endpoint_url, timeout=120, headers={"Authorization": f"Bearer {token}"}, json={
        "prompt": prompt.get("prompt_text", ""),
        "image_base64": base64.b64encode(image).decode(),
        "mime_type": mime_type,
    })
    r.raise_for_status()
    data = r.json()
    dissection = data.get("dissection", data)
    subs = {k: float(dissection[k]["score"] if isinstance(dissection[k], dict) else dissection[k]) for k in DIMENSIONS}
    if "hallucination_risk" in data:
        subs["hallucination_risk"] = float(data["hallucination_risk"])
    return CriticResult(subs, data.get("critic_rationale") or data.get("rationale", ""), "gcc_endpoint",
                        data.get("critic_model", "gcc"), data)
