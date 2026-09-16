from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from .events import publish_event, get_events_since

class RealtimeEngineTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_publish_and_get_events(self):
        event = publish_event("slots", "SLOT_LOCKED", {"turf_id": "1", "slot_ids": ["s1"]})
        self.assertIsNotNone(event["id"])
        self.assertEqual(event["channel"], "slots")
        self.assertEqual(event["type"], "SLOT_LOCKED")

        # Query events
        events = get_events_since(event["timestamp"] - 1.0, channels=["slots"])
        self.assertTrue(any(e["id"] == event["id"] for e in events))

    def test_channel_filtering(self):
        e_gate = publish_event("gate", "GATE_CHECK_IN", {"booking_id": "FT-123"})
        
        # Query only 'slots' channel
        slots_events = get_events_since(e_gate["timestamp"] - 0.5, channels=["slots"])
        self.assertFalse(any(e["id"] == e_gate["id"] for e in slots_events))

        # Query 'gate' channel
        gate_events = get_events_since(e_gate["timestamp"] - 0.5, channels=["gate"])
        self.assertTrue(any(e["id"] == e_gate["id"] for e in gate_events))

    def test_poll_endpoint(self):
        publish_event("operations", "TEST_PING", {"msg": "hello"})
        response = self.client.get(reverse("realtime-poll"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("events", data)
        self.assertIn("server_time", data)
        self.assertEqual(data["status"], "online")
