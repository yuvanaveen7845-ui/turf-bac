from django.urls import path
from .views import stream_events, poll_events, publish_live_event

urlpatterns = [
    path("stream/", stream_events, name="realtime-stream"),
    path("poll/", poll_events, name="realtime-poll"),
    path("publish/", publish_live_event, name="realtime-publish"),
]
