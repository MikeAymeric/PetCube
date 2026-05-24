"""
notification_packet.py
Mirror della struct NotifPacket di petcube_battle.h (64 byte fissi).
Usa struct.pack per serializzare in network byte order coerente col firmware.
"""
import struct
from dataclasses import dataclass
from enum import IntEnum


class NotifSource(IntEnum):
    DISCORD  = 0
    GMAIL    = 1
    CALENDAR = 2
    SLACK    = 3
    TRELLO   = 4
    GITHUB   = 5
    GENERIC  = 255


class NotifPriority(IntEnum):
    LOW    = 0
    NORMAL = 1
    HIGH   = 2


class NotifCategory(IntEnum):
    LODE        = 0  # positive + low urgency
    OPPORTUNITA = 1  # positive + high urgency
    ROUTINE     = 2  # neutral + low
    SCADENZA    = 3  # neutral + high
    CRITICA     = 4  # negative + low
    CRISI       = 5  # negative + high
    CURIOSITA   = 6  # question + low
    AIUTO       = 7  # question + high


PACKET_SIZE = 64
SCHEMA_VERSION = 1

# Layout C (little-endian, ESP32):
#   uint8_t  version
#   uint8_t  source
#   uint8_t  priority
#   uint8_t  category
#   uint16_t seed_hash
#   uint8_t  seed_length
#   uint8_t  _reserved
#   uint32_t timestamp
#   char     seed_preview[52]
# Totale: 1+1+1+1+2+1+1+4+52 = 64 byte ✓
STRUCT_FORMAT = "<BBBBHBBI52s"


@dataclass
class NotifPacket:
    source: NotifSource
    priority: NotifPriority
    category: NotifCategory
    seed_hash: int
    seed_length: int
    timestamp: int
    seed_preview: str   # max 51 char + zero terminator

    def to_bytes(self) -> bytes:
        # Tronca prima a livello di code point (non di byte) per evitare di
        # spezzare sequenze UTF-8 multi-byte, poi codifica e zero-padda a 52 byte.
        # Il campo seedPreview è 52 byte: max 51 byte di contenuto + 1 null terminator.
        preview_str = self.seed_preview[:51]          # tronca su codepoint
        encoded = preview_str.encode("utf-8", errors="ignore")
        # Se anche dopo il taglio a 51 codepoint il risultato supera 51 byte
        # (improbabile ma possibile con caratteri > 1 byte), tronca ulteriormente.
        if len(encoded) > 51:
            encoded = encoded[:51]
        preview_bytes = encoded + b"\x00" * (52 - len(encoded))
        packed = struct.pack(
            STRUCT_FORMAT,
            SCHEMA_VERSION,
            int(self.source),
            int(self.priority),
            int(self.category),
            self.seed_hash & 0xFFFF,
            min(self.seed_length, 50),
            0,  # reserved
            self.timestamp & 0xFFFFFFFF,
            preview_bytes,
        )
        if len(packed) != PACKET_SIZE:
            raise ValueError(f"Packet size mismatch: {len(packed)} byte, attesi {PACKET_SIZE}")
        return packed

    @classmethod
    def from_bytes(cls, data: bytes) -> "NotifPacket":
        if len(data) != PACKET_SIZE:
            raise ValueError(f"from_bytes: dati di dimensione errata ({len(data)} byte, attesi {PACKET_SIZE})")
        version, source, priority, category, h, length, _, ts, preview = struct.unpack(
            STRUCT_FORMAT, data
        )
        return cls(
            source=NotifSource(source),
            priority=NotifPriority(priority),
            category=NotifCategory(category),
            seed_hash=h,
            seed_length=length,
            timestamp=ts,
            seed_preview=preview.rstrip(b"\x00").decode("utf-8", errors="ignore"),
        )


def compute_seed_hash(seed: str) -> int:
    """
    Hash a 16 bit del seed. Stessa logica del firmware mockNotification:
    h = 0; for ch in seed: h = h * 31 + ch
    """
    h = 0
    for ch in seed[:50]:
        h = (h * 31 + ord(ch)) & 0xFFFF
    return h


def truncate_seed(text: str, max_len: int = 50) -> str:
    """
    Tronca il seed alla prima frase (punto/?/!) o ai max_len caratteri.
    Se la prima frase è < 10 caratteri (es. 'Aiuto!'), continua oltre
    perché probabilmente è un'interiezione e il contesto utile viene dopo.
    """
    if not text:
        return ""
    MIN_SENTENCE_LEN = 10
    # Cerca la fine della prima frase abbastanza lunga
    for i, ch in enumerate(text):
        if ch in ".!?" and i + 1 >= MIN_SENTENCE_LEN and i + 1 <= max_len:
            return text[: i + 1]
    return text[:max_len]
