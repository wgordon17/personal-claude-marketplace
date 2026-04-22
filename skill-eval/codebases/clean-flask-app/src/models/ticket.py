from dataclasses import dataclass
from enum import StrEnum

from src.db import execute_write, fetch_all, fetch_one


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Ticket:
    id: int
    project_id: int
    title: str
    description: str
    status: TicketStatus
    priority: TicketPriority
    assignee_id: int | None
    reporter_id: int


def get_ticket_by_id(session, ticket_id: int) -> Ticket | None:
    row = fetch_one(
        session,
        """
        SELECT id, project_id, title, description, status, priority, assignee_id, reporter_id
        FROM tickets WHERE id = :id
        """,
        {"id": ticket_id},
    )
    if row is None:
        return None
    return Ticket(
        **{
            **row,
            "status": TicketStatus(row["status"]),
            "priority": TicketPriority(row["priority"]),
        }
    )


def list_tickets_for_project(
    session, project_id: int, page: int = 1, per_page: int = 50
) -> list[Ticket]:
    offset = (page - 1) * per_page
    rows = fetch_all(
        session,
        """
        SELECT id, project_id, title, description, status, priority, assignee_id, reporter_id
        FROM tickets WHERE project_id = :project_id
        ORDER BY id DESC LIMIT :limit OFFSET :offset
        """,
        {"project_id": project_id, "limit": per_page, "offset": offset},
    )
    return [
        Ticket(
            **{**r, "status": TicketStatus(r["status"]), "priority": TicketPriority(r["priority"])}
        )
        for r in rows
    ]


def create_ticket(
    session,
    project_id: int,
    title: str,
    description: str,
    priority: TicketPriority,
    reporter_id: int,
    assignee_id: int | None = None,
) -> Ticket:
    execute_write(
        session,
        """
        INSERT INTO tickets
            (project_id, title, description, status, priority, reporter_id, assignee_id)
        VALUES
            (:project_id, :title, :description, :status, :priority, :reporter_id, :assignee_id)
        """,
        {
            "project_id": project_id,
            "title": title,
            "description": description,
            "status": TicketStatus.OPEN,
            "priority": priority,
            "reporter_id": reporter_id,
            "assignee_id": assignee_id,
        },
    )
    row = fetch_one(
        session,
        """
        SELECT id, project_id, title, description, status, priority,
               assignee_id, reporter_id
        FROM tickets ORDER BY id DESC LIMIT 1
        """,
        {},
    )
    return Ticket(
        **{
            **row,
            "status": TicketStatus(row["status"]),
            "priority": TicketPriority(row["priority"]),
        }
    )


def update_ticket_status(session, ticket_id: int, status: TicketStatus) -> bool:
    rows = execute_write(
        session,
        "UPDATE tickets SET status = :status WHERE id = :id",
        {"status": status, "id": ticket_id},
    )
    return rows > 0
