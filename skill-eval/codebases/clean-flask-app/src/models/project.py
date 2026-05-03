from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from src.db import execute_write, fetch_all, fetch_one


@dataclass
class Project:
    id: int
    name: str
    description: str
    owner_id: int
    member_ids: list[int] = field(default_factory=list)


def get_project_by_id(session: Session, project_id: int) -> Project | None:
    row = fetch_one(
        session,
        "SELECT id, name, description, owner_id FROM projects WHERE id = :id",
        {"id": project_id},
    )
    if row is None:
        return None
    # Eager-load member IDs in a single query to avoid N+1
    member_rows = fetch_all(
        session,
        "SELECT user_id FROM project_members WHERE project_id = :project_id",
        {"project_id": project_id},
    )
    member_ids = [r["user_id"] for r in member_rows]
    return Project(**row, member_ids=member_ids)


def list_projects_for_user(
    session: Session, user_id: int, page: int = 1, per_page: int = 20
) -> list[Project]:
    offset = (page - 1) * per_page
    rows = fetch_all(
        session,
        """
        SELECT DISTINCT p.id, p.name, p.description, p.owner_id
        FROM projects p
        LEFT JOIN project_members pm ON pm.project_id = p.id
        WHERE p.owner_id = :user_id OR pm.user_id = :user_id
        ORDER BY p.id
        LIMIT :limit OFFSET :offset
        """,
        {"user_id": user_id, "limit": per_page, "offset": offset},
    )
    return [Project(**r, member_ids=[]) for r in rows]


def create_project(session: Session, name: str, description: str, owner_id: int) -> Project:
    execute_write(
        session,
        """
        INSERT INTO projects (name, description, owner_id)
        VALUES (:name, :description, :owner_id)
        """,
        {"name": name, "description": description, "owner_id": owner_id},
    )
    row = fetch_one(
        session,
        """
        SELECT id, name, description, owner_id FROM projects
        WHERE owner_id = :owner_id ORDER BY id DESC LIMIT 1
        """,
        {"owner_id": owner_id},
    )
    return Project(**row, member_ids=[owner_id])


def add_member(session: Session, project_id: int, user_id: int) -> bool:
    execute_write(
        session,
        """
        INSERT INTO project_members (project_id, user_id)
        VALUES (:project_id, :user_id) ON CONFLICT DO NOTHING
        """,
        {"project_id": project_id, "user_id": user_id},
    )
    return True


def delete_project(session: Session, project_id: int) -> bool:
    execute_write(
        session,
        "DELETE FROM project_members WHERE project_id = :project_id",
        {"project_id": project_id},
    )
    rows = execute_write(
        session,
        "DELETE FROM projects WHERE id = :id",
        {"id": project_id},
    )
    return rows > 0
