"""Deterministic opportunity matching."""

from jobradar.matching.profile import BOHDAN_PROFILE, SearchProfile
from jobradar.matching.scorer import MatchCandidate, ScoreResult, score_candidate
from jobradar.matching.service import MatchingService

__all__ = [
    "BOHDAN_PROFILE",
    "MatchCandidate",
    "MatchingService",
    "ScoreResult",
    "SearchProfile",
    "score_candidate",
]
