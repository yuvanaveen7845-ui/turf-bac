from rest_framework import serializers
from .models import WalletTransaction, LoyaltyTransaction


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "amount",
            "transaction_type",
            "source",
            "reference_id",
            "description",
            "balance_after",
            "created_at",
        ]


class LoyaltyTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyTransaction
        fields = [
            "id",
            "points",
            "transaction_type",
            "source",
            "reference_id",
            "description",
            "balance_after",
            "created_at",
        ]
