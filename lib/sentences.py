"""Split a <speak> block into sentences for the playback queue (Chatterbox would join them back up)."""
import re

_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str, min_chars: int = 15) -> list[str]:
    """Pieces shorter than min_chars ride with a neighbour so playback does not stutter."""
    out: list[str] = []
    for part in (p.strip() for p in _END.split(text.strip())):
        if not part:
            continue
        if out and (len(out[-1]) < min_chars or len(part) < min_chars):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out
