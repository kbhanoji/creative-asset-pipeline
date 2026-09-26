# Prompt craft for Google image models

Version: 0.1.0

Structure every prompt as: subject (exact product name and SKU) -> product attributes taken from
the SKU context (form, colour, materials, labels) -> composition and shot type -> lighting -> camera
(focal length, angle) -> background/environment -> constraints (negative and safety).
State aspect ratio and resolution explicitly. Never invent features that aren't in the SKU context.
