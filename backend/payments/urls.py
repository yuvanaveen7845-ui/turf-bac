from django.urls import path
from .views import PaymentListCreateView, ProcessRefundView, RefundListView

urlpatterns = [
    path("", PaymentListCreateView.as_view(), name="payment_list_create"),
    path("refunds/", RefundListView.as_view(), name="refund_list"),
    path("<str:pk>/refund/", ProcessRefundView.as_view(), name="process_refund"),
]
