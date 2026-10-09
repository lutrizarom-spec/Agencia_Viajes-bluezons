"""Pruebas de persistencia, exclusión y entrega de notificaciones outbox."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from dao.paquete_dao import PaqueteDao
from model.paquete_nacional import Paquete_Nacional
from services.compra_service import (
    CompraService,
    PaymentIdempotencyConflictError,
)
from services.notification_outbox import (
    LostOutboxClaimError,
    MailDeliveryError,
    OutboxRepository,
    OutboxWorker,
)


class RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, *, recipient: str, subject: str, body: str, message_id: str) -> None:
        self.sent.append(recipient)


class NotificationOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "outbox.db"
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                PaqueteDao(connection).crear_tabla()
                PaqueteDao(connection).insertar_paquete(
                    Paquete_Nacional(701, "Ruta de prueba", 3, 25000)
                )
        self.service = CompraService(self.database_path)
        self.travel_date = date(2027, 1, 15)
        self.service.configure_capacity(701, self.travel_date, 2)
        result = self.service.purchase_idempotently(
            "10.000.013-K",
            701,
            1,
            "notification-test-key",
            travel_date=self.travel_date,
            notification_email="cliente@example.com",
        )
        self.reservation_id = result.receipt.reservation_id

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_purchase_and_notification_are_idempotent(self) -> None:
        self.service.purchase_idempotently(
            "10.000.013-K",
            701,
            1,
            "notification-test-key",
            travel_date=self.travel_date,
            notification_email="cliente@example.com",
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM notification_outbox"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_api_validates_email_and_queues_with_the_reservation(self) -> None:
        from fastapi.testclient import TestClient

        from main_api import create_app
        from services.auth_service import UserRole

        app = create_app(
            self.database_path,
            jwt_secret="outbox-test-secret-that-is-at-least-32-bytes",
        )
        app.state.auth_service.create_user(
            "12.345.678-5", "a-secure-test-password", UserRole.CLIENTE
        )
        client = TestClient(app)
        try:
            token_response = client.post(
                "/auth/login",
                json={"rut": "12.345.678-5", "password": "a-secure-test-password"},
            )
            self.assertEqual(token_response.status_code, 200)
            response = client.post(
                "/reservas",
                json={
                    "package_code": 701,
                    "quantity": 1,
                    "travel_date": "2027-01-15",
                    "notification_email": "second@example.com",
                },
                headers={
                    "Authorization": f"Bearer {token_response.json()['access_token']}",
                    "X-Idempotency-Key": "api-notification-test",
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 201)
        with closing(sqlite3.connect(self.database_path)) as connection:
            event = connection.execute(
                "SELECT payload_json FROM notification_outbox WHERE event_key LIKE 'reservation-created:%' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIn("second@example.com", event[0])

    def test_competing_workers_only_send_one_claimed_message(self) -> None:
        started = threading.Event()
        release = threading.Event()
        sent: list[str] = []

        class BlockingMailer:
            def send(self, *, recipient: str, subject: str, body: str, message_id: str) -> None:
                started.set()
                if not release.wait(timeout=5):
                    raise MailDeliveryError("Timed out waiting for the test.")
                sent.append(recipient)

        worker_one = OutboxWorker(self.database_path, BlockingMailer())
        worker_two = OutboxWorker(self.database_path, RecordingMailer())
        first_result: list[dict[str, int]] = []
        thread = threading.Thread(
            target=lambda: first_result.append(worker_one.process_batch())
        )
        thread.start()
        self.assertTrue(started.wait(timeout=5))
        second_result = worker_two.process_batch()
        release.set()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(second_result["processed"], 0)
        self.assertEqual(first_result[0]["sent"], 1)
        self.assertEqual(sent, ["cliente@example.com"])

    def test_expired_claim_cannot_update_reclaimed_message(self) -> None:
        repository = OutboxRepository(self.database_path)
        first = repository.claim_batch(lease_seconds=1)[0]
        old_claim = first
        from datetime import datetime, timedelta, timezone

        later = datetime.now(timezone.utc) + timedelta(seconds=2)
        second = repository.claim_batch(lease_seconds=1, now=later)[0]
        with self.assertRaises(LostOutboxClaimError):
            repository.mark_sent(old_claim, now=later)
        repository.mark_sent(second, now=later)

    def test_additional_payment_replays_by_key_and_rejects_changed_amount(self) -> None:
        self.service.transition_payment(self.reservation_id, "confirmed")
        first = self.service.create_payment(
            self.reservation_id,
            "10.000.013-K",
            5000,
            idempotency_key="payment-attempt-1",
        )
        replay = self.service.create_payment(
            self.reservation_id,
            "10.000.013-K",
            5000,
            idempotency_key="payment-attempt-1",
        )
        self.assertEqual(replay.payment_id, first.payment_id)

        with self.assertRaises(PaymentIdempotencyConflictError):
            self.service.create_payment(
                self.reservation_id,
                "10.000.013-K",
                6000,
                idempotency_key="payment-attempt-1",
            )


if __name__ == "__main__":
    unittest.main()
