# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
import enum
from src.db import Base


class TaskStatus(enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    archived = "archived"


class TaskPriority(enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Enum(TaskStatus), nullable=False, default=TaskStatus.pending)
    priority = Column(Enum(TaskPriority), nullable=False, default=TaskPriority.medium)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    is_public = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    due_date = Column(DateTime, nullable=True)

    owner = relationship("User", back_populates="tasks")
    comments = relationship("Comment", back_populates="task", lazy="select")

    def __repr__(self):
        return f"<Task id={self.id} title={self.title!r} status={self.status.value}>"

    def to_dict(self, include_comments=False):
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "owner_id": self.owner_id,
            "is_public": self.is_public,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "due_date": self.due_date.isoformat() if self.due_date else None,
        }
        if include_comments:
            data["comments"] = [c.to_dict() for c in self.comments]
        return data

    def is_overdue(self):
        if self.due_date is None:
            return False
        return datetime.utcnow() > self.due_date and self.status != TaskStatus.done
