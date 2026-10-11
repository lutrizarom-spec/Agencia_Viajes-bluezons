"""Outbox transaccional y worker de notificaciones por correo."""

from __future__ import annotations

import json
import math
import smtplib
import sqlite3
import ssl
import uuid
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from hashlib import sha256
from os import environ
from pathlib import Path
from typing import Protocol

BACKOFF_SECONDS = (1, 5, 30, 300)


class MailDeliveryError(Exception):
    """Fallo temporal al enviar un correo."""


class LostOutboxClaimError(RuntimeError):
    """La reclamación del evento venció o fue tomada por otro worker."""


class OutboxEventNotFoundError(LookupError):
    """No existe un evento de outbox con el identificador solicitado."""


class Mailer(Protocol):
    def send(
        self, *, recipient: str, subject: str, body: str, message_id: str
    ) -> None: ...


@dataclass(frozen=True)
class OutboxMessage:
    id: int
    event_key: str
    payload: Mapping[str, str]
    attempts: int
    claim_token: str


class SmtpMailer:
    """Entrega correo por SMTP con TLS y tiempos de espera acotados."""

    def __init__(
        self,
        *,
        host: str,
        sender: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        use_ssl: bool = False,
        timeout: float = 10,
    ) -> None:
        if not host.strip() or not sender.strip():
            raise ValueError("SMTP_HOST y SMTP_SENDER son obligatorios.")
        if (username is None) != (password is None):
            raise ValueError("SMTP_USERNAME y SMTP_PASSWORD deben configurarse juntos.")
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("El puerto o timeout SMTP no es válido.")
        self.host = host
        self.port = port
        self.sender = sender
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.timeout = timeout

    def send(
        self, *, recipient: str, subject: str, body: str, message_id: str
    ) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message.set_content(body)
        try:
            if self.use_ssl:
                with smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout, context=ssl.create_default_context()
                ) as smtp:
                    self._deliver(smtp, message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                    smtp.starttls(context=ssl.create_default_context())
                    self._deliver(smtp, message)
        except (OSError, smtplib.SMTPException) as error:
            raise MailDeliveryError("No se pudo entregar la notificación SMTP.") from error

    def _deliver(self, smtp: smtplib.SMTP, message: EmailMessage) -> None:
        if self.username is not None and self.password is not None:
            smtp.login(self.username, self.password)
        smtp.send_message(message)

    @classmethod
    def from_environment(cls) -> SmtpMailer | None:
        host = environ.get("AGENCIA_SMTP_HOST")
        sender = environ.get("AGENCIA_SMTP_SENDER")
        if not host and not sender:
            return None
        if not host or not sender:
            raise ValueError("Configura AGENCIA_SMTP_HOST y AGENCIA_SMTP_SENDER juntos.")
        username = environ.get("AGENCIA_SMTP_USERNAME")
        password = environ.get("AGENCIA_SMTP_PASSWORD")
        raw_port = environ.get("AGENCIA_SMTP_PORT", "587")
        try:
            port = int(raw_port)
            timeout = float(environ.get("AGENCIA_SMTP_TIMEOUT", "10"))
        except ValueError as error:
            raise ValueError("AGENCIA_SMTP_PORT/TIMEOUT deben ser numéricos.") from error
        raw_use_ssl = environ.get("AGENCIA_SMTP_USE_SSL", "false").lower()
        if raw_use_ssl not in {"1", "true", "yes", "0", "false", "no"}:
            raise ValueError("AGENCIA_SMTP_USE_SSL debe ser true/false, yes/no o 1/0.")
        use_ssl = raw_use_ssl in {"1", "true", "yes"}
        return cls(
            host=host,
            sender=sender,
            port=port,
            username=username,
            password=password,
            use_ssl=use_ssl,
            timeout=timeout,
        )


class OutboxRepository:
    """Persiste y reclama mensajes sin mantener locks durante el envío."""

    @staticmethod
    def ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'processing', 'failed', 'sent', 'dead')
                ),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                next_attempt_at TEXT NOT NULL,
                claim_token TEXT,
                claim_until TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notification_outbox_ready
            ON notification_outbox (status, next_attempt_at, claim_until)
            """
        )

    @staticmethod
    def enqueue(
        connection: sqlite3.Connection,
        *,
        event_key: str,
        payload: Mapping[str, str],
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO notification_outbox (
                event_key, payload_json, status, next_attempt_at, created_at
            ) VALUES (?, ?, 'pending', ?, ?)
            """,
            (
                event_key,
                json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")),
                created_at,
                created_at,
            ),
        )

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)

    def list_events(self, *, limit: int = 100) -> list[dict[str, object]]:
        """Lista metadatos operativos sin exponer destinatarios ni contenido."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit debe ser un entero entre 1 y 1000.")
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, event_key, status, attempts, next_attempt_at,
                       claim_until, last_error, created_at, processed_at
                FROM notification_outbox
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def retry_dead_event(self, event_id: int, *, now: datetime | None = None) -> None:
        """Reencola explícitamente un evento agotado sin reabrir otros estados."""
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 1:
            raise ValueError("event_id debe ser un entero positivo.")
        current_iso = (
            now or datetime.now(UTC)
        ).astimezone(UTC).isoformat()
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            with connection:
                updated = connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'pending', attempts = 0, next_attempt_at = ?,
                        claim_token = NULL, claim_until = NULL, last_error = NULL,
                        processed_at = NULL
                    WHERE id = ? AND status = 'dead'
                    """,
                    (current_iso, event_id),
                ).rowcount
        if updated != 1:
            raise OutboxEventNotFoundError(
                f"No existe un evento dead-letter con id {event_id}."
            )

    def claim_batch(
        self,
        *,
        limit: int = 20,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> list[OutboxMessage]:
        if limit < 1 or lease_seconds < 1:
            raise ValueError("limit y lease_seconds deben ser positivos.")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        current_iso = current.isoformat()
        lease_until = (current + timedelta(seconds=lease_seconds)).isoformat()
        claimed: list[OutboxMessage] = []
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """
                    SELECT id, event_key, payload_json, attempts
                    FROM notification_outbox
                    WHERE (status IN ('pending', 'failed') AND next_attempt_at <= ?)
                       OR (status = 'processing' AND claim_until <= ?)
                    ORDER BY id
                    LIMIT ?
                    """,
                    (current_iso, current_iso, limit),
                ).fetchall()
                for event_id, event_key, payload_json, attempts in rows:
                    token = uuid.uuid4().hex
                    updated = connection.execute(
                        """
                        UPDATE notification_outbox
                        SET status = 'processing', claim_token = ?, claim_until = ?
                        WHERE id = ?
                          AND (
                              (status IN ('pending', 'failed') AND next_attempt_at <= ?)
                              OR (status = 'processing' AND claim_until <= ?)
                          )
                        """,
                        (token, lease_until, event_id, current_iso, current_iso),
                    ).rowcount
                    if updated == 1:
                        claimed.append(
                            OutboxMessage(
                                id=event_id,
                                event_key=event_key,
                                payload=json.loads(payload_json),
                                attempts=attempts,
                                claim_token=token,
                            )
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return claimed

    def mark_sent(self, message: OutboxMessage, *, now: datetime | None = None) -> None:
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            with connection:
                updated = connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'sent', claim_token = NULL, claim_until = NULL,
                        last_error = NULL, processed_at = ?
                    WHERE id = ? AND status = 'processing' AND claim_token = ?
                    """,
                    (
                        (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
                        message.id,
                        message.claim_token,
                    ),
                ).rowcount
        if updated != 1:
            raise LostOutboxClaimError(f"Se perdió la reclamación del evento {message.id}.")

    def mark_failed(
        self,
        message: OutboxMessage,
        error: MailDeliveryError,
        *,
        max_attempts: int,
        backoff_seconds: tuple[int, ...],
        now: datetime | None = None,
    ) -> bool:
        attempts = message.attempts + 1
        dead = attempts >= max_attempts
        delay = backoff_seconds[min(attempts - 1, len(backoff_seconds) - 1)]
        current = (now or datetime.now(UTC)).astimezone(UTC)
        next_attempt = current + timedelta(seconds=0 if dead else delay)
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            with connection:
                updated = connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = ?, attempts = ?, next_attempt_at = ?,
                        claim_token = NULL, claim_until = NULL, last_error = ?,
                        processed_at = ?
                    WHERE id = ? AND status = 'processing' AND claim_token = ?
                    """,
                    (
                        "dead" if dead else "failed",
                        attempts,
                        next_attempt.isoformat(),
                        str(error)[:1000],
                        current.isoformat(),
                        message.id,
                        message.claim_token,
                    ),
                ).rowcount
        if updated != 1:
            raise LostOutboxClaimError(f"Se perdió la reclamación del evento {message.id}.")
        return dead


class OutboxWorker:
    """Despacha correo fuera de las transacciones de compra, con lease y reintentos."""

    def __init__(
        self,
        database_path: str | Path,
        mailer: Mailer,
        *,
        max_attempts: int = 5,
        backoff_seconds: tuple[int, ...] = BACKOFF_SECONDS,
        lease_seconds: int = 60,
    ) -> None:
        if (
            max_attempts < 1
            or lease_seconds < 1
            or not backoff_seconds
            or any(delay < 0 for delay in backoff_seconds)
        ):
            raise ValueError("La política de reintentos outbox no es válida.")
        self.repository = OutboxRepository(database_path)
        self.mailer = mailer
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.lease_seconds = lease_seconds

    def process_batch(
        self,
        *,
        limit: int = 20,
        now: datetime | None = None,
    ) -> dict[str, int]:
        messages = self.repository.claim_batch(
            limit=limit, lease_seconds=self.lease_seconds, now=now
        )
        summary = {"processed": 0, "sent": 0, "failed": 0, "dead": 0}
        for message in messages:
            summary["processed"] += 1
            payload = message.payload
            try:
                message_id = f"<{sha256(message.event_key.encode()).hexdigest()}@agencia.local>"
                self.mailer.send(
                    recipient=payload["recipient"],
                    subject=payload["subject"],
                    body=payload["body"],
                    message_id=message_id,
                )
            except MailDeliveryError as error:
                dead = self.repository.mark_failed(
                    message,
                    error,
                    max_attempts=self.max_attempts,
                    backoff_seconds=self.backoff_seconds,
                    now=now,
                )
                summary["dead" if dead else "failed"] += 1
            else:
                self.repository.mark_sent(message, now=now)
                summary["sent"] += 1
        return summary
