"""Gate 1 routing: turn scores into a tier, a status and an action (SOW 3.4.5, TDD 9.2-9.3).

Pure functions with no GCP dependency, so the business rules are unit-testable
and every threshold comes from configuration.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Scoring


class Status:
    APPROVED = "APPROVED"                 # auto-approved (>= auto_approve_min) or approved by a human
    NEEDS_REVISION = "NEEDS_REVISION"     # human edits the prompt and regenerates
    FLAGGED = "FLAGGED"                   # mandatory human review
    FAILED_QC = "FAILED_QC"               # below revision_min with below_revision_action=FAIL
    REJECTED = "REJECTED"                 # rejected by a human
    PENDING_SCORE = "PENDING_SCORE"
    UNMATCHED = "UNMATCHED"               # image not linked to a prompt yet


FINAL_STATUSES = {Status.APPROVED, Status.FAILED_QC, Status.REJECTED}


@dataclass(frozen=True)
class Decision:
    composite: float           # 0-100
    hallucination_risk: float  # 0-100
    hallucination_band: str    # LOW | MEDIUM | HIGH
    tier: str                  # HIGH_COMPLIANCE | MODERATE_COMPLIANCE | NON_COMPLIANT
    status: str
    action: str                # AUTO_APPROVED | HUMAN_REVISE_AND_REGENERATE | HITL_REVIEW | FAILED_QC
    reason: str


def composite_score(sub_scores: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted average of 0-1 sub-scores, returned on a 0-100 scale."""
    missing = set(weights) - set(sub_scores)
    if missing:
        raise ValueError(f"critic result missing sub-score(s): {sorted(missing)}")
    return round(100 * sum(sub_scores[k] * w for k, w in weights.items()), 2)


def hallucination_risk(sub_scores: dict[str, float]) -> float:
    """ADR-06: H = (1 - artifact_freedom) * 100. A critic-provided value wins if present."""
    if "hallucination_risk" in sub_scores:
        return round(float(sub_scores["hallucination_risk"]), 2)
    return round((1 - sub_scores["artifact_freedom"]) * 100, 2)


def band(risk: float, scoring: Scoring) -> str:
    t = scoring.thresholds
    if risk > t.hallucination_high:
        return "HIGH"
    if risk > t.hallucination_medium:
        return "MEDIUM"
    return "LOW"


def decide(sub_scores: dict[str, float], scoring: Scoring) -> Decision:
    t = scoring.thresholds
    comp = composite_score(sub_scores, scoring.weights)
    risk = hallucination_risk(sub_scores)
    b = band(risk, scoring)

    if comp >= t.auto_approve_min:
        if b == "HIGH" and t.high_hallucination_blocks_auto_approve:
            return Decision(comp, risk, b, "HIGH_COMPLIANCE", Status.FLAGGED, "HITL_REVIEW",
                            f"composite {comp} >= {t.auto_approve_min} but hallucination risk {risk} is HIGH")
        return Decision(comp, risk, b, "HIGH_COMPLIANCE", Status.APPROVED, "AUTO_APPROVED",
                        f"composite {comp} >= {t.auto_approve_min}")
    if comp >= t.revision_min:
        return Decision(comp, risk, b, "MODERATE_COMPLIANCE", Status.NEEDS_REVISION, "HUMAN_REVISE_AND_REGENERATE",
                        f"{t.revision_min} <= composite {comp} < {t.auto_approve_min}")
    if t.below_revision_action == "FAIL":
        return Decision(comp, risk, b, "NON_COMPLIANT", Status.FAILED_QC, "FAILED_QC",
                        f"composite {comp} < {t.revision_min}")
    return Decision(comp, risk, b, "NON_COMPLIANT", Status.FLAGGED, "HITL_REVIEW",
                    f"composite {comp} < {t.revision_min} (flagged for mandatory review)")
