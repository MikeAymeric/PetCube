"""
test_e2e.py
Test del flusso completo Plugin → Sentiment → Packet → Sender (mock).
Non richiede BLE né credenziali Google.
Uso: cd petcube_companion && python3 test_e2e.py
"""
import asyncio
import json
import sys

# Stub di un plugin che genera eventi sintetici
from plugins.base import Plugin, RawEvent
from notification_packet import NotifSource, NotifPriority, NotifPacket
from plugin_manager import PluginManager, PLUGIN_REGISTRY
from ble_sender import Sender


class MockPlugin(Plugin):
    """Plugin di test: emette una serie di eventi predefiniti, poi smette."""
    name = "mock"

    def __init__(self, config):
        super().__init__(config)
        self.queue = [
            (NotifSource.GMAIL,    NotifPriority.NORMAL, "URGENT: server is down, fix ASAP"),
            (NotifSource.GMAIL,    NotifPriority.LOW,    "Great job on the demo!"),
            (NotifSource.CALENDAR, NotifPriority.NORMAL, "Daily standup at 10am"),
            (NotifSource.DISCORD,  NotifPriority.NORMAL, "Can you help me with this?"),
            (NotifSource.GMAIL,    NotifPriority.NORMAL, "Report due tomorrow EOD"),
            (NotifSource.GITHUB,   NotifPriority.LOW,    "PR review requested"),
            (NotifSource.SLACK,    NotifPriority.HIGH,   "Aiuto! Non funziona più nulla"),
            (NotifSource.TRELLO,   NotifPriority.NORMAL, "Riunione settimanale alle 15:00"),
        ]

    def poll(self):
        if not self.queue:
            return []
        events = []
        # Una notifica per poll
        src, prio, text = self.queue.pop(0)
        ext_id = f"mock-{len(self.seen_ids)}"
        self.seen_ids.add(ext_id)
        events.append(RawEvent(source=src, priority=prio, text=text, external_id=ext_id))
        return events

    @property
    def poll_interval_sec(self):
        return 1  # 1 sec per il test


# Inietta nel registry
PLUGIN_REGISTRY["mock"] = "__main__.MockPlugin"


async def main():
    received_packets = []

    config = {
        "plugins": {"mock": {"enabled": True, "poll_interval_sec": 1}},
        "transport": {"prefer": "mock"},
        "device": {},
        "logging": {"level": "INFO"},
    }
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%H:%M:%S")

    sender = Sender(config)

    async def consumer(pkt: NotifPacket):
        await sender.send(pkt)
        received_packets.append(pkt)

    # Loop manuale che bypassa il main.py
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def on_notif(pkt):
        loop.call_soon_threadsafe(queue.put_nowait, pkt)

    pm = PluginManager(config, on_notif)
    pm.start()

    # Consuma fino a quando tutti gli eventi sono stati emessi
    timeout = 15  # secondi max
    start = loop.time()
    while loop.time() - start < timeout:
        try:
            pkt = await asyncio.wait_for(queue.get(), timeout=2.0)
            await consumer(pkt)
        except asyncio.TimeoutError:
            # Verifica se il plugin ha finito
            if not pm.plugins[0].queue:
                break

    pm.stop()
    await sender.close()

    print(f"\n=== RIEPILOGO ===")
    print(f"Pacchetti ricevuti: {len(received_packets)}")
    print(f"\n{'Source':<10} {'Priority':<8} {'Category':<12} {'Hash':>6}  Preview")
    print("─" * 78)
    for p in received_packets:
        print(f"{p.source.name:<10} {p.priority.name:<8} {p.category.name:<12} {p.seed_hash:>6}  {p.seed_preview!r}")

    # Verifica
    assert len(received_packets) == 8, f"Expected 8, got {len(received_packets)}"

    # Verifica categorizzazione
    by_text = {p.seed_preview: p for p in received_packets}
    checks = [
        ("URGENT: server is down, fix ASAP", "CRISI"),
        ("Great job on the demo!", "OPPORTUNITA"),  # singolo "!" alza a HIGH urgency
        ("Daily standup at 10am", "ROUTINE"),
        ("Can you help me with this?", "AIUTO"),
        ("Report due tomorrow EOD", "ROUTINE"),
        ("PR review requested", "ROUTINE"),
        ("Aiuto! Non funziona più nulla", "CRISI"),
        ("Riunione settimanale alle 15:00", "ROUTINE"),
    ]
    print()
    for txt, expected_cat in checks:
        actual = by_text[txt].category.name
        mark = "✓" if actual == expected_cat else "✗"
        print(f"  {mark} {txt!r:<50} → {actual} (atteso {expected_cat})")


if __name__ == "__main__":
    asyncio.run(main())
