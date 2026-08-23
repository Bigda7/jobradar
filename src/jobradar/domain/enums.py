from enum import StrEnum


class OpportunityKind(StrEnum):
    EMPLOYMENT = "employment"
    FREELANCE_PROJECT = "freelance_project"


class OpportunityStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    FLEXIBLE = "flexible"
    UNKNOWN = "unknown"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED_PAUSED = "notification_skipped_paused"


class OpportunityDisposition(StrEnum):
    NEW = "new"
    FAVORITE = "favorite"
    HIDDEN = "hidden"
