"""Status and mode constants for pipeline entities."""

SOURCE_STATUS_NEW = "new"
SOURCE_STATUS_ANALYZING = "analyzing"
SOURCE_STATUS_DONE = "done"
SOURCE_STATUS_FAILED = "failed"

SOURCE_TYPE_FILE = "file"
SOURCE_TYPE_URL = "url"

CLIP_STATUS_CUT = "cut"
CLIP_STATUS_FORMATTING = "formatting"
CLIP_STATUS_READY = "ready"
CLIP_STATUS_REVIEW = "review"
CLIP_STATUS_APPROVED = "approved"
CLIP_STATUS_REJECTED = "rejected"
CLIP_STATUS_PUBLISHED = "published"

ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_PAUSED = "paused"
ACCOUNT_STATUS_BLOCKED = "blocked"

POST_STATUS_SCHEDULED = "scheduled"
POST_STATUS_PUBLISHED = "published"
POST_STATUS_FAILED = "failed"

REVIEW_MODE_MANUAL = "manual"
REVIEW_MODE_AUTO = "auto"
