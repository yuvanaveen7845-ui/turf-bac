import time
import json
import threading
from typing import List, Dict, Any, Optional

# Thread-safe in-memory circular buffer for real-time broadcast events
_LOCK = threading.Lock()
_MAX_HISTORY = 250
_EVENT_HISTORY: List[Dict[str, Any]] = []
_EVENT_COUNTER = 0

def publish_event(channel: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Publish a real-time event to the circular buffer.
    channel: 'slots' | 'gate' | 'operations' | 'all'
    event_type: 'SLOT_LOCKED' | 'SLOT_RELEASED' | 'BOOKING_CONFIRMED' | 'GATE_CHECK_IN' | 'PRICE_CHANGED' | 'WALK_IN_CREATED'
    payload: JSON-serializable dictionary with context
    """
    global _EVENT_COUNTER
    now = time.time()
    
    with _LOCK:
        _EVENT_COUNTER += 1
        event = {
            "id": f"evt_{_EVENT_COUNTER}_{int(now * 1000)}",
            "seq": _EVENT_COUNTER,
            "channel": channel,
            "type": event_type,
            "timestamp": now,
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "payload": payload,
        }
        _EVENT_HISTORY.append(event)
        
        # Trim oldest events if exceeding maximum history
        if len(_EVENT_HISTORY) > _MAX_HISTORY:
            del _EVENT_HISTORY[: len(_EVENT_HISTORY) - _MAX_HISTORY]
            
    return event

def get_events_since(since_timestamp: float = 0.0, channels: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Retrieve all events occurring strictly after since_timestamp.
    Optionally filter by a list of allowed channels.
    """
    with _LOCK:
        matched = []
        for ev in _EVENT_HISTORY:
            if ev["timestamp"] > since_timestamp:
                if not channels or ev["channel"] in channels or "all" in channels or ev["channel"] == "all":
                    matched.append(ev)
        return matched

def format_sse(data: Dict[str, Any], event_name: Optional[str] = None) -> str:
    """Format dictionary into Server-Sent Events (SSE) wire string."""
    lines = []
    if event_name:
        lines.append(f"event: {event_name}")
    if "id" in data:
        lines.append(f"id: {data['id']}")
    lines.append(f"data: {json.dumps(data)}")
    return "\n".join(lines) + "\n\n"
