from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from companion_kernel.drives import DriveState
from companion_kernel.emotions import EmotionState
from companion_kernel.relationship import RelationshipState
from companion_kernel.types import DriveKind, EmotionLabel


class SnapshotCorrupt(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KernelState:
    version: int
    last_event_at: datetime
    drives: tuple[tuple[DriveKind, DriveState], ...]
    emotion: EmotionState
    relationship: RelationshipState
    paused: bool
    awaiting_reply: bool
    proactive_sent_at: tuple[datetime, ...]

    @classmethod
    def initial(
        cls,
        at: datetime,
        drives: Mapping[DriveKind, DriveState],
        emotion: EmotionState,
    ) -> "KernelState":
        ordered = tuple(sorted(drives.items(), key=lambda pair: pair[0].value))
        return cls(0, at, ordered, emotion, RelationshipState.initial(), False, False, ())

    def drive_map(self) -> dict[DriveKind, DriveState]:
        return dict(self.drives)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "last_event_at": self.last_event_at.isoformat(),
            "drives": [
                {
                    "kind": kind.value,
                    "value": item.value,
                    "unmet_since": item.unmet_since.isoformat() if item.unmet_since else None,
                    "last_updated_at": item.last_updated_at.isoformat(),
                    "evidence": list(item.evidence),
                }
                for kind, item in sorted(self.drives, key=lambda pair: pair[0].value)
            ],
            "emotion": {
                "label": self.emotion.label.value,
                "valence": self.emotion.valence,
                "arousal": self.emotion.arousal,
                "intensity": self.emotion.intensity,
                "mood_valence": self.emotion.mood_valence,
                "updated_at": self.emotion.updated_at.isoformat(),
            },
            "relationship": self.relationship.to_dict(),
            "paused": self.paused,
            "awaiting_reply": self.awaiting_reply,
            "proactive_sent_at": [item.isoformat() for item in self.proactive_sent_at],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "KernelState":
        drive_items = tuple(
            (
                DriveKind(item["kind"]),
                DriveState(
                    value=float(item["value"]),
                    unmet_since=datetime.fromisoformat(item["unmet_since"]) if item["unmet_since"] else None,
                    last_updated_at=datetime.fromisoformat(item["last_updated_at"]),
                    evidence=tuple(item["evidence"]),
                ),
            )
            for item in raw["drives"]
        )
        emotion_raw = raw["emotion"]
        emotion = EmotionState(
            label=EmotionLabel(emotion_raw["label"]),
            valence=float(emotion_raw["valence"]),
            arousal=float(emotion_raw["arousal"]),
            intensity=float(emotion_raw["intensity"]),
            mood_valence=float(emotion_raw["mood_valence"]),
            updated_at=datetime.fromisoformat(emotion_raw["updated_at"]),
        )
        relationship = RelationshipState.from_dict(dict(raw["relationship"]))
        return cls(
            version=int(raw["version"]),
            last_event_at=datetime.fromisoformat(raw["last_event_at"]),
            drives=drive_items,
            emotion=emotion,
            relationship=relationship,
            paused=bool(raw["paused"]),
            awaiting_reply=bool(raw["awaiting_reply"]),
            proactive_sent_at=tuple(datetime.fromisoformat(item) for item in raw["proactive_sent_at"]),
        )


class SnapshotRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, state: KernelState) -> None:
        payload = state.to_dict()
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        envelope = {"checksum": hashlib.sha256(canonical.encode()).hexdigest(), "state": payload}
        encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self._path.parent, delete=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, self._path)

    def load(self) -> KernelState | None:
        if not self._path.exists():
            return None
        try:
            envelope = json.loads(self._path.read_text(encoding="utf-8"))
            canonical = json.dumps(
                envelope["state"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            actual = hashlib.sha256(canonical.encode()).hexdigest()
            if actual != envelope["checksum"]:
                raise SnapshotCorrupt("snapshot checksum mismatch")
            return KernelState.from_dict(envelope["state"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SnapshotCorrupt("invalid snapshot structure") from exc
