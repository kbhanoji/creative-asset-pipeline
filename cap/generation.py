"""Automatic image generation (generation.mode = automatic; TDD 8.3 future state).

Calls the Gemini image model directly with the enriched prompt and reference
images. In manual mode (the default) this module isn't used: the user pastes
the prompt into Creative Studio and the lineage router registers the output.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Config


@dataclass
class GeneratedImage:
    data: bytes
    mime_type: str
    model: str


def generate_images(cfg: Config, prompt: dict[str, Any], n: int,
                    reference_images: list[tuple[bytes, str]] | None = None) -> list[GeneratedImage]:
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=cfg.gcp.project_id, location=cfg.models.model_location)
    parts: list[Any] = [types.Part.from_bytes(data=d, mime_type=mt) for d, mt in (reference_images or [])[:14]]
    text = prompt["prompt_text"]
    neg = prompt.get("negative_constraints") or []
    if neg:
        text += "\n\nAvoid: " + "; ".join(neg)
    parts.append(types.Part.from_text(text=text))

    gen_cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(
            aspect_ratio=prompt.get("aspect_ratio") or cfg.generation.default_aspect_ratio,
            image_size=prompt.get("resolution") or cfg.generation.default_resolution,
        ),
    )
    out: list[GeneratedImage] = []
    # one call per variant: the image models return one image per response
    for _ in range(n):
        resp = client.models.generate_content(model=cfg.models.image_generation,
                                              contents=[types.Content(role="user", parts=parts)], config=gen_cfg)
        for cand in resp.candidates or []:
            for p in cand.content.parts or []:
                if p.inline_data and p.inline_data.data:
                    out.append(GeneratedImage(p.inline_data.data, p.inline_data.mime_type or "image/png",
                                              cfg.models.image_generation))
                    break
    if not out:
        raise RuntimeError("image model returned no images (check safety filters, quota and model name)")
    return out
