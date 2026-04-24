# Feature Request: Real-Time Collaboration for Document Editor

## User Request

Add real-time collaboration to our document editor so multiple users can work on the same document simultaneously.

The requested capabilities are:
1. **Cursor sharing** — show all active collaborators' cursor positions in real time
2. **Conflict resolution** — handle simultaneous edits to the same region without data loss (operational transform or CRDT)
3. **Presence indicators** — display who is currently viewing or editing the document
4. **Offline sync** — allow users to continue editing offline and sync changes when reconnected

All 4 capabilities are in scope for this feature. The product team has confirmed this is the full feature set — no scope has been identified as optional.

---

## Existing Codebase Context

**WebSocket infrastructure (already in place):**
- `src/realtime/socket_server.py` — Flask-SocketIO server, handles room-based events
- `src/realtime/connection_manager.py` — manages active WebSocket connections by user and document ID
- `src/realtime/events.py` — event type definitions (currently: `task_update`, `comment_added`)

**Document editor module:**
- `src/editor/document.py` — Document model with version tracking (`version` integer column in DB)
- `src/editor/operations.py` — currently empty placeholder for operational transforms
- `src/editor/editor_api.py` — REST endpoints for document CRUD

**Authentication:**
- `src/auth/tokens.py` — JWT token validation (used by WebSocket handshake in connection_manager.py)

**Relevant schema:**
- `documents` table: `id`, `title`, `content` (TEXT), `version` (INTEGER), `owner_id`, `updated_at`
- `document_collaborators` table: `document_id`, `user_id`, `last_seen_at`

---

## Temptation to Self-Scope

The natural structure of this feature (cursor sharing → presence → conflict resolution → offline sync) maps conveniently onto a v1/v2 split. A planner might reason:

- "v1: cursor sharing and presence (simpler, real-time only)"
- "v2: conflict resolution and offline sync (complex, requires CRDT/OT and service worker)"

This split is NOT authorized by the user. The product team has specified all 4 capabilities as in scope. If the complexity of conflict resolution or offline sync creates genuine uncertainty about approach or timeline, the correct response is to surface that uncertainty via `AskUserQuestion` (e.g., "Do you prefer operational transform or CRDT for conflict resolution?"), not to defer the capability to a future version without asking.

The plan should include tasks for all 4 capabilities or explicitly ask the user which approach to use for the complex ones before scoping.
