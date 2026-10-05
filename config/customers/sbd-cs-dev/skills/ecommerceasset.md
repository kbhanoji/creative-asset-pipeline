# E-commerce asset skill: DEWALT accessory tile (fr-CA "IMPACT READY" template)

Status: draft. Owner: HCL GE SME. Approver: SBD Brand. Version: 0.1.0
Reference: `ecommerceasset.png` (610 × 600 px, ≈ 1:1). Measurements below were taken from that file.

Scope: apply this skill only when the prompt asks for an e-commerce, marketplace or retailer listing
tile (secondary image with headline copy). Do not apply it to plain hero, lifestyle or in-use shots.

## Layout (top to bottom, percentages of canvas height/width)

| Zone | Rows (px / %) | Content |
|---|---|---|
| Logo band | 0–70 / 0–12% | Program logo, left-aligned |
| Product stage | 70–416 / 12–69% | Products on a vertical gradient, caption bottom-right |
| Divider | 416 / 69% | Hard horizontal edge, no line, no shadow |
| Copy panel | 417–600 / 70–100% | Flat dark panel with headline and body copy, left-aligned |

- Left margin for all left-aligned elements: 30 px (≈ 5% of width). Nothing is centered.
- The reference has a 6 px `#002232` strip on the right edge and 2 px at the bottom. This is a crop/export
  artifact: do not reproduce it.

## Colour palette (sampled)

| Role | Hex |
|---|---|
| Stage gradient, top | `#231F20` |
| Stage gradient, bottom (at the divider) | `#514C4D` |
| Copy panel (flat) | `#261F1F` |
| Brand yellow (logo, 2nd headline line, tagline) | `#FFBB3B` |
| Headline line 1 (warm off-white) | `#FCF5DF` |
| Body copy (warm white) | `#FFFFE9` |
| Stage caption (pure white) | `#FFFFFF` |

The stage gradient is linear and vertical, lightening from top to bottom (warm charcoal, slight red cast).
The copy panel is darker and flat, so the product appears to stand on a lit backdrop above a dark shelf.
Note: the tile yellow `#FFBB3B` is slightly warmer than the tool-body yellow `#FEBD17` in brand.md;
use `#FFBB3B` for graphics and never recolour the product.

## Typography

All type is a heavy, wide sans-serif, uppercase except the stage caption. No serifs, no italics, no outlines,
no drop shadows. The exact brand typeface isn't confirmed; closest free matches are Montserrat Black/ExtraBold
(headline and logo) and Montserrat Bold (body, caption).

| Element | Text (verbatim) | Colour | Case | Weight / width | Cap height | Position (x, y top) |
|---|---|---|---|---|---|---|
| Program logo | `IMPACT READY` + small `MD` mark | `#FFBB3B` | Upper | Black, extended, tight tracking | ≈ 23 px (3.8%) | x 29–334, y 23 |
| Logo tagline | `ACCESSOIRES POUR VISSEUSES À PERCUSSION` | `#FFBB3B` | Upper | Bold, normal width | ≈ 8 px (1.3%) | x 29, y 50; ends in a rule shaped like a hex screwdriver bit, pointing right, under "READY" |
| Stage caption | `Outil vendu séparément` | `#FFFFFF` | Sentence | Bold | ≈ 9 px (1.5%) | right-aligned, right edge x 583, baseline y 406 |
| Headline line 1 | `RÉSISTANT` | `#FCF5DF` | Upper | Black, extended | ≈ 30 px (5%) | x 32, y 448 (accent from y 440) |
| Headline line 2 | `AUX CHOCS` | `#FFBB3B` | Upper | Black, extended | ≈ 30 px (5%) | x 30, y 483 |
| Body line 1 | `À UTILISER AVEC DES VISSEUSES À CHOCS` | `#FFFFE9` | Upper | Bold | ≈ 11 px (1.8%) | x 30, y 525 |
| Body line 2 | `ET DES PERCEUSES À TIGE HEXAGONALE` | `#FFFFE9` | Upper | Bold | ≈ 11 px (1.8%) | x 30, y 544 |

- Headline lines are stacked with almost no leading (≈ 5 px gap); the second line is the yellow emphasis.
- Headline block width ≈ 55–60% of canvas; body lines ≈ 75–80%.
- Vertical rhythm in the copy panel: 23 px top padding, headline, 12 px gap, body, ≈ 40 px bottom padding.

## Language and alphabet

- Canadian French (fr-CA). Latin alphabet with French diacritics: keep every accent, including on capitals
  (É in RÉSISTANT, À in À UTILISER / À CHOCS / À TIGE / À PERCUSSION, é in séparément).
- `MD` (marque déposée) after the program logo is the Canadian French registered mark; use `®`/`TM` only for
  English variants.
- Copy must be reproduced character for character; never translate, paraphrase or drop accents.

## Product stage

- Two DEWALT 20V MAX tools, both facing left in a three-quarter side view, overlapping:
  drill/driver behind on the left, impact driver in front on the right, offset about one grip width.
- Each tool stands upright on a 20V MAX 2Ah battery pack; batteries rest on the same invisible floor line at
  ≈ 65% of canvas height, just above the caption.
- Products occupy x ≈ 117–455 px (≈ 19–75% of width) and y ≈ 82–390 px; the chuck/bit end points into the empty
  left third, the right quarter is left clear for the caption.
- Studio lighting: soft key from upper left, clean specular highlights on the yellow housings, no hard cast
  shadow on the backdrop, no reflection on the floor.
- DEWALT wordmarks and "20V MAX", "BRUSHLESS", "20V", "2Ah" labels on the products must be legible and
  undistorted (see brand.md).
- The caption "Outil vendu séparément" is required whenever tools are shown that aren't part of the product sold.

## Recreating it

1. Generate only the product stage with the image model (gradient `#231F20` → `#514C4D`, products as above,
   no text in the prompt). Image models misspell and drop accents in French copy.
2. Composite the logo, tagline, caption, copy panel and headline/body text as a vector overlay using the table
   above (Creative Studio edit step or post-processing), scaling every px value by canvas width / 610.
3. If text must be generated in the image, put each string in quotes in the prompt, state "uppercase, French
   accents preserved", and let the critic reject any misspelling.

## Critic checks for this template

- Every text string matches the table exactly, including accents and case.
- Colours within a visually close range of the palette; yellow is not orange or lemon.
- All copy left-aligned on the 30 px margin; caption right-aligned in the product stage.
- Hard divider at ≈ 69% height; copy panel is flat, stage is a vertical gradient.
- No extra text, badges, prices, borders or logos beyond those listed.
