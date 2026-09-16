import time
import json
from django.http import StreamingHttpResponse, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from .events import publish_event, get_events_since, format_sse

@api_view(["GET"])
@permission_classes([AllowAny])
def poll_events(request):
    """
    Delta-polling endpoint returning events recorded since a client-provided timestamp.
    Query params:
      - since: float (epoch timestamp in seconds)
      - channels: comma-separated list of channels (e.g. 'slots,gate,operations')
    """
    try:
        since_ts = float(request.GET.get("since", 0.0))
    except (ValueError, TypeError):
        since_ts = 0.0

    channels_param = request.GET.get("channels", "")
    channels = [c.strip() for c in channels_param.split(",") if c.strip()] if channels_param else None

    events = get_events_since(since_ts, channels)
    return JsonResponse({
        "events": events,
        "count": len(events),
        "server_time": time.time(),
        "status": "online"
    })

@api_view(["GET"])
@permission_classes([AllowAny])
def stream_events(request):
    """
    Server-Sent Events (SSE) streaming endpoint.
    Keeps an open HTTP connection and pushes events matching requested channels.
    """
    channels_param = request.GET.get("channels", "")
    channels = [c.strip() for c in channels_param.split(",") if c.strip()] if channels_param else None

    def event_generator():
        last_check = time.time()
        # Initial greeting event
        yield format_sse({
            "type": "CONNECTION_ESTABLISHED",
            "message": "Friends Turf Real-Time Stream Connected",
            "channels": channels or ["all"],
            "server_time": last_check,
        }, event_name="open")

        heartbeat_counter = 0
        while True:
            time.sleep(0.5)
            heartbeat_counter += 1
            now = time.time()
            
            # Fetch any new events since last check
            new_events = get_events_since(last_check, channels)
            if new_events:
                last_check = now
                for ev in new_events:
                    yield format_sse(ev, event_name=ev["type"])
            
            # Send keep-alive comment ping every 15 seconds (30 cycles of 0.5s)
            if heartbeat_counter >= 30:
                heartbeat_counter = 0
                yield f": heartbeat {now}\n\n"

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response

@api_view(["POST"])
@permission_classes([AllowAny])
def publish_live_event(request):
    """
    Endpoint allowing internal services or testing tools to trigger real-time events.
    """
    channel = request.data.get("channel", "operations")
    event_type = request.data.get("type", "PING")
    payload = request.data.get("payload", {})
    
    event = publish_event(channel, event_type, payload)
    return JsonResponse({"status": "published", "event": event})
