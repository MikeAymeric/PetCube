"""
test_e2e.py
Test del flusso completo Plugin → Sentiment → Packet → Sender (mock).
Non richiede BLE né credenziali Google.
Uso: cd petcube_companion && python3 test_e2e.py
"""
import asyncio
import sys

# Stub di un plugin che genera eventi sintetici
from plugins.base import Plugin, RawEvent
from notification_packet import NotifSource, NotifPriority, NotifPacket, truncate_seed
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
    received_packets: list[NotifPacket] = []

    config = {
        "plugins": {"mock": {"enabled": True, "poll_interval_sec": 1}},
        "transport": {"prefer": "mock"},
        "device": {},
        "logging": {"level": "INFO"},
    }
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    sender = Sender(config)

    async def consumer(pkt: NotifPacket):
        await sender.send(pkt)
        received_packets.append(pkt)

    # Loop manuale che bypassa il main.py
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[NotifPacket] = asyncio.Queue()

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

    # Verifica conteggio
    assert len(received_packets) == 8, f"Expected 8, got {len(received_packets)}"

    # Verifica categorizzazione.
    # by_text è indicizzato su seed_preview (già truncato da _dispatch).
    # Le check key usano truncate_seed() per essere sempre allineate a ciò che
    # il dispatcher produce — evita KeyError se il testo è stato troncato.
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
    all_ok = True
    for original_text, expected_cat in checks:
        # Calcola la chiave effettiva come la calcolerebbe il dispatcher
        key = truncate_seed(original_text, max_len=50)
        pkt = by_text.get(key)
        if pkt is None:
            print(f"  ✗ {original_text!r:<50} → PACCHETTO NON TROVATO (key={key!r})")
            all_ok = False
            continue
        actual = pkt.category.name
        mark = "✓" if actual == expected_cat else "✗"
        if actual != expected_cat:
            all_ok = False
        print(f"  {mark} {original_text!r:<50} → {actual} (atteso {expected_cat})")

    if all_ok:
        print("\n✅  Tutti i check passati.")
    else:
        print("\n❌  Alcuni check falliti.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
