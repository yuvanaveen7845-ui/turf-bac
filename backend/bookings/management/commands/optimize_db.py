"""
Database performance optimization management command for Friends Turf.
Performs routine maintenance operations to keep query performance at peak:
- SQLite: VACUUM, ANALYZE, integrity check
- PostgreSQL: VACUUM ANALYZE on hot tables, pg_stat_user_tables diagnostics
"""
import time
import logging
from django.core.management.base import BaseCommand
from django.db import connection

logger = logging.getLogger(__name__)

# Tables that receive the most writes and benefit most from ANALYZE
HOT_TABLES = [
    "turfs_timeslot",
    "bookings_booking",
    "bookings_booking_slots",
    "payments_payment",
    "payments_refund",
    "notifications_notification",
    "audit_auditlog",
    "wallet_wallettransaction",
    "accounts_businesssetting",
]


class Command(BaseCommand):
    help = "Run database performance maintenance: VACUUM, ANALYZE, and diagnostic checks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--full",
            action="store_true",
            help="Run VACUUM FULL (rewrites entire table, acquires exclusive lock). Use during low-traffic windows only.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Run integrity/diagnostic checks without modifying data.",
        )

    def handle(self, *args, **options):
        vendor = connection.vendor
        is_full = options.get("full", False)
        is_check = options.get("check", False)

        self.stdout.write(f"Database vendor: {vendor}")
        self.stdout.write(f"Database name: {connection.settings_dict.get('NAME', 'unknown')}")
        self.stdout.write("")

        if is_check:
            self._run_diagnostics(vendor)
            return

        start = time.monotonic()

        if vendor == "sqlite":
            self._optimize_sqlite(is_full)
        elif vendor == "postgresql":
            self._optimize_postgres(is_full)
        else:
            self.stdout.write(self.style.WARNING(f"Unsupported vendor: {vendor}"))
            return

        elapsed = time.monotonic() - start
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"[OK] Database optimization completed in {elapsed:.2f}s")
        )

    def _optimize_sqlite(self, is_full):
        with connection.cursor() as cursor:
            # 1. Run ANALYZE to update query planner statistics
            self.stdout.write("Running ANALYZE...")
            cursor.execute("ANALYZE;")
            self.stdout.write(self.style.SUCCESS("  [v] ANALYZE complete"))

            # 2. Run VACUUM to defragment and reclaim space
            if is_full:
                self.stdout.write("Running VACUUM (full database rewrite)...")
            else:
                self.stdout.write("Running VACUUM...")
            # SQLite VACUUM must run outside a transaction
            cursor.execute("VACUUM;")
            self.stdout.write(self.style.SUCCESS("  [v] VACUUM complete"))

            # 3. Verify WAL mode is active
            cursor.execute("PRAGMA journal_mode;")
            mode = cursor.fetchone()[0]
            if mode.upper() != "WAL":
                self.stdout.write(self.style.WARNING(f"  [WARN] journal_mode is {mode}, expected WAL"))
                cursor.execute("PRAGMA journal_mode = WAL;")
                self.stdout.write(self.style.SUCCESS("  [v] Switched to WAL mode"))
            else:
                self.stdout.write(f"  journal_mode: {mode}")

            # 4. WAL checkpoint to merge WAL back into main database
            self.stdout.write("Running WAL checkpoint (TRUNCATE)...")
            cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            result = cursor.fetchone()
            self.stdout.write(
                self.style.SUCCESS(
                    f"  [v] WAL checkpoint: busy={result[0]}, log_pages={result[1]}, checkpointed={result[2]}"
                )
            )

            # 5. Report database size
            cursor.execute("PRAGMA page_count;")
            page_count = cursor.fetchone()[0]
            cursor.execute("PRAGMA page_size;")
            page_size = cursor.fetchone()[0]
            db_size_mb = (page_count * page_size) / (1024 * 1024)
            self.stdout.write(f"  Database size: {db_size_mb:.2f} MB ({page_count} pages × {page_size} bytes)")

            # 6. Integrity check
            self.stdout.write("Running integrity check...")
            cursor.execute("PRAGMA integrity_check;")
            result = cursor.fetchone()[0]
            if result == "ok":
                self.stdout.write(self.style.SUCCESS("  [v] Integrity check: OK"))
            else:
                self.stdout.write(self.style.ERROR(f"  [x] Integrity check FAILED: {result}"))

    def _optimize_postgres(self, is_full):
        with connection.cursor() as cursor:
            vacuum_cmd = "VACUUM FULL ANALYZE" if is_full else "VACUUM ANALYZE"

            for table in HOT_TABLES:
                try:
                    self.stdout.write(f"Running {vacuum_cmd} on {table}...")
                    # VACUUM cannot run inside a transaction block
                    cursor.execute(f"{vacuum_cmd} {table};")
                    self.stdout.write(self.style.SUCCESS(f"  [v] {table}"))
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  [WARN] {table}: {e}"))

            # Run general ANALYZE for any tables not in HOT_TABLES
            self.stdout.write("Running global ANALYZE...")
            cursor.execute("ANALYZE;")
            self.stdout.write(self.style.SUCCESS("  [v] Global ANALYZE complete"))

    def _run_diagnostics(self, vendor):
        self.stdout.write(self.style.MIGRATE_HEADING("Database Performance Diagnostics"))
        self.stdout.write("")

        with connection.cursor() as cursor:
            if vendor == "sqlite":
                checks = [
                    ("journal_mode", "PRAGMA journal_mode;"),
                    ("synchronous", "PRAGMA synchronous;"),
                    ("cache_size", "PRAGMA cache_size;"),
                    ("mmap_size", "PRAGMA mmap_size;"),
                    ("page_size", "PRAGMA page_size;"),
                    ("page_count", "PRAGMA page_count;"),
                    ("busy_timeout", "PRAGMA busy_timeout;"),
                    ("foreign_keys", "PRAGMA foreign_keys;"),
                    ("temp_store", "PRAGMA temp_store;"),
                    ("wal_autocheckpoint", "PRAGMA wal_autocheckpoint;"),
                ]
                for name, sql in checks:
                    cursor.execute(sql)
                    val = cursor.fetchone()[0]
                    self.stdout.write(f"  {name}: {val}")

                # Table sizes
                self.stdout.write("")
                self.stdout.write("  Table row counts:")
                for table in HOT_TABLES:
                    try:
                        cursor.execute(f"SELECT COUNT(*) FROM {table};")
                        count = cursor.fetchone()[0]
                        self.stdout.write(f"    {table}: {count:,} rows")
                    except Exception:
                        pass

            elif vendor == "postgresql":
                # Connection pool status
                cursor.execute(
                    "SELECT state, COUNT(*) FROM pg_stat_activity GROUP BY state ORDER BY count DESC;"
                )
                self.stdout.write("  Connection states:")
                for row in cursor.fetchall():
                    self.stdout.write(f"    {row[0] or 'NULL'}: {row[1]}")

                # Table statistics
                self.stdout.write("")
                self.stdout.write("  Table statistics (hot tables):")
                for table in HOT_TABLES:
                    try:
                        cursor.execute(
                            "SELECT n_live_tup, n_dead_tup, last_vacuum, last_analyze "
                            "FROM pg_stat_user_tables WHERE relname = %s;",
                            [table],
                        )
                        row = cursor.fetchone()
                        if row:
                            self.stdout.write(
                                f"    {table}: live={row[0]:,} dead={row[1]:,} "
                                f"last_vacuum={row[2] or 'never'} last_analyze={row[3] or 'never'}"
                            )
                    except Exception:
                        pass

                # Index usage
                self.stdout.write("")
                self.stdout.write("  Unused indexes (idx_scan = 0, potential overhead):")
                cursor.execute(
                    "SELECT relname, indexrelname, idx_scan "
                    "FROM pg_stat_user_indexes "
                    "WHERE idx_scan = 0 AND indexrelname NOT LIKE '%_pkey' "
                    "ORDER BY pg_relation_size(indexrelid) DESC LIMIT 10;"
                )
                for row in cursor.fetchall():
                    self.stdout.write(f"    {row[0]}.{row[1]} (scans: {row[2]})")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("[OK] Diagnostics complete"))
