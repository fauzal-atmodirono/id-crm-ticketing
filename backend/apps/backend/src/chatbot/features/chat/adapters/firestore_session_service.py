from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import ListSessionsResponse
from google.cloud import firestore

if TYPE_CHECKING:
    from google.adk.sessions.session import Event

    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class FirestoreSessionService(BaseSessionService):
    """Firestore-backed session service for persisting ADK session state across container instances.

    Underlying storage keeps one document per session ID under a configured Firestore collection.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        self._collection_name = "adk_sessions"
        _log.info(
            "firestore_session_service_initialized",
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
            collection=self._collection_name,
        )

    def _collection(self) -> Any:
        return self._client.collection(self._collection_name)

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        sid = session_id or f"{app_name}-{user_id}"
        session = Session(
            id=sid,
            app_name=app_name,
            user_id=user_id,
            state=state or {},
            events=[],
            last_update_time=0.0,
        )

        def _write() -> None:
            data = session.model_dump(mode="json")
            self._collection().document(sid).set(data)

        await asyncio.to_thread(_write)
        _log.info("firestore_session_created", session_id=sid, user_id=user_id)
        return session

    async def get_session(
        self,
        *,
        app_name: str,  # noqa: ARG002
        user_id: str,  # noqa: ARG002
        session_id: str,
        config: Any = None,  # noqa: ARG002
    ) -> Session | None:
        def _read() -> Session | None:
            snap = self._collection().document(session_id).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            if not data:
                return None
            return Session.model_validate(data)

        session = await asyncio.to_thread(_read)
        if session:
            _log.info("firestore_session_loaded", session_id=session_id)
        return session

    async def delete_session(
        self,
        *,
        app_name: str,  # noqa: ARG002
        user_id: str,  # noqa: ARG002
        session_id: str,
    ) -> None:
        def _delete() -> None:
            self._collection().document(session_id).delete()

        await asyncio.to_thread(_delete)
        _log.info("firestore_session_deleted", session_id=session_id)

    async def append_event(self, session: Session, event: Event) -> Event:
        # Pydantic is mutable but we must append the event and rewrite to Firestore
        session.events.append(event)

        def _write() -> None:
            data = session.model_dump(mode="json")
            self._collection().document(session.id).set(data)

        await asyncio.to_thread(_write)
        _log.info("firestore_session_event_appended", session_id=session.id)
        return event

    async def list_sessions(
        self, *, app_name: str, user_id: str | None = None
    ) -> ListSessionsResponse:
        def _query() -> list[Session]:
            query = self._collection().where("app_name", "==", app_name)
            if user_id:
                query = query.where("user_id", "==", user_id)
            docs = query.stream()
            sessions = []
            for doc in docs:
                data = doc.to_dict()
                if data:
                    sessions.append(Session.model_validate(data))
            return sessions

        sessions = await asyncio.to_thread(_query)
        _log.info(
            "firestore_sessions_listed", app_name=app_name, user_id=user_id, count=len(sessions)
        )
        return ListSessionsResponse(sessions=sessions)

    async def get_user_state(self, *, app_name: str, user_id: str) -> dict[str, Any]:  # noqa: ARG002
        # No user-scoped state is used in current chatbot implementation
        return {}
