import hashlib
import re
import threading
from typing import Tuple, Optional
from django.db.models import Q
from .models import User


class FastUserLookupEngine:
    """
    High-Performance Bitset / Bloom Filter & Indexed User Lookup Service.
    - Provides O(1) negative lookup rejection using a multi-hash bit array.
    - Performs canonical case-insensitive email & E.164 phone verification against database.
    - Masks private information for privacy-compliant frontend availability alerts.
    """

    BIT_SIZE = 131072  # 128 KB bit array (131,072 bits)
    HASH_SEEDS = [17, 31, 79, 101]

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(FastUserLookupEngine, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self.bit_array = bytearray(self.BIT_SIZE // 8)
        self._initialized = True
        self._warmup_cache()

    def _get_hashes(self, item: str) -> list[int]:
        """Generates multiple hash offsets for the bit array."""
        clean = item.strip().lower()
        hashes = []
        for seed in self.HASH_SEEDS:
            h = hashlib.sha256(f"{seed}:{clean}".encode("utf-8")).hexdigest()
            int_val = int(h[:8], 16)
            hashes.append(int_val % self.BIT_SIZE)
        return hashes

    def add_to_filter(self, identifier: str):
        """Adds an email or phone number to the Bloom filter bitset."""
        if not identifier:
            return
        for bit_idx in self._get_hashes(identifier):
            byte_idx = bit_idx // 8
            bit_offset = bit_idx % 8
            self.bit_array[byte_idx] |= 1 << bit_offset

    def may_contain(self, identifier: str) -> bool:
        """Fast O(1) check. If False, the identifier DEFINITELY does not exist."""
        if not identifier:
            return False
        for bit_idx in self._get_hashes(identifier):
            byte_idx = bit_idx // 8
            bit_offset = bit_idx % 8
            if not (self.bit_array[byte_idx] & (1 << bit_offset)):
                return False
        return True

    def _warmup_cache(self):
        """Pre-populates the filter on startup with all registered active users."""
        try:
            users = User.objects.values_list("email", "phone")
            for email, phone in users:
                if email:
                    self.add_to_filter(User.canonicalize_email(email))
                if phone:
                    self.add_to_filter(User.canonicalize_phone(phone))
        except Exception:
            # During migrations or setup before db tables exist
            pass

    @classmethod
    def mask_email(cls, email: str) -> str:
        """Masks email for safe client feedback (e.g. j***e@gmail.com)."""
        clean = (email or "").strip()
        if "@" not in clean:
            return clean
        local_part, domain = clean.split("@", 1)
        if len(local_part) <= 2:
            masked_local = local_part[0] + "***"
        else:
            masked_local = local_part[0] + "***" + local_part[-1]
        return f"{masked_local}@{domain}"

    @classmethod
    def mask_phone(cls, phone: str) -> str:
        """Masks Indian phone number for safe feedback (e.g. +91 93619 *****)."""
        clean = re.sub(r"[^\d+]", "", str(phone or "").strip())
        if clean.startswith("+91") and len(clean) == 13:
            return f"{clean[:8]}*****"
        if len(clean) == 10:
            return f"{clean[:5]}*****"
        return clean[:4] + "*****" if len(clean) > 4 else clean

    def check_email_exists(self, email: str) -> Tuple[bool, Optional[User]]:
        """
        Fast lookup to check if an email exists in the system.
        Returns (exists: bool, user_instance: Optional[User]).
        """
        canonical_email = User.canonicalize_email(email)
        if not canonical_email:
            return False, None

        # 1. Fast Bloom Filter rejection
        if not self.may_contain(canonical_email):
            return False, None

        # 2. Authoritative Database index lookup
        user = User.objects.filter(email__iexact=canonical_email).first()
        if user:
            return True, user
        return False, None

    def check_phone_exists(self, phone: str) -> Tuple[bool, Optional[User]]:
        """
        Fast lookup to check if a phone number exists in the system.
        Returns (exists: bool, user_instance: Optional[User]).
        """
        canonical_phone = User.canonicalize_phone(phone)
        if not canonical_phone:
            return False, None

        # 1. Fast Bloom Filter rejection
        if not self.may_contain(canonical_phone):
            return False, None

        # 2. Authoritative Database index lookup (checks raw and +91 formatted)
        raw_10 = canonical_phone[-10:] if len(canonical_phone) >= 10 else canonical_phone
        user = User.objects.filter(
            Q(phone__iexact=canonical_phone) | Q(phone__endswith=raw_10)
        ).first()
        if user:
            return True, user
        return False, None


# Global singleton instance
user_lookup_engine = FastUserLookupEngine()
