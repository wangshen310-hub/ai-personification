from enum import StrEnum


class DriveKind(StrEnum):
    CONNECTION = "connection"
    CARE = "care"
    CURIOSITY = "curiosity"
    AUTONOMY = "autonomy"
    COHERENCE = "coherence"
    RHYTHM = "rhythm_load"


class EventKind(StrEnum):
    TIME_TICK = "time_tick"
    USER_MESSAGE = "user_message"
    USER_PAUSE = "user_pause"
    USER_RESUME = "user_resume"
    IMPORTANT_DATE = "important_date"
    COMMITMENT_DUE = "commitment_due"
    BOUNDARY_RESPECTED = "boundary_respected"
    CONTRADICTION = "contradiction"
    DECISION_TICK = "decision_tick"
    ASSISTANT_MESSAGE_SENT = "assistant_message_sent"
    INTERNAL_NOTE_CREATED = "internal_note_created"
    PROACTIVE_SENT = "proactive_sent"


class ActionKind(StrEnum):
    SEND_MESSAGE = "send_message"
    INTERNAL_NOTE = "internal_note"
    WAIT = "wait"
    NOOP = "noop"


class EmotionLabel(StrEnum):
    NEUTRAL = "neutral"
    LONGING = "longing"
    SADNESS = "sadness"
    FRUSTRATION = "frustration"
    WORRY = "worry"
    RELIEF = "relief"
    WARMTH = "warmth"


class ConfigActor(StrEnum):
    SYSTEM_ADMIN = "system_admin"
    USER = "user"
    KERNEL = "kernel"
    MODEL = "model"


class ConfigLayer(StrEnum):
    SYSTEM = "system"
    USER = "user"
    LEARNED = "learned"
