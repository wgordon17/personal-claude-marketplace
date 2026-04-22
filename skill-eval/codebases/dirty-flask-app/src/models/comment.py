# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from datetime import datetime
from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from src.db import Base


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    body = Column(Text, nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    task = relationship("Task", back_populates="comments")
    author = relationship("User", back_populates="comments")

    def __repr__(self):
        return f"<Comment id={self.id} task_id={self.task_id} author_id={self.author_id}>"

    def to_dict(self):
        return {
            "id": self.id,
            "body": self.body,
            "task_id": self.task_id,
            "author_id": self.author_id,
            "is_deleted": self.is_deleted,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def soft_delete(self):
        self.is_deleted = True
        self.body = "[deleted]"

    @staticmethod
    def visible_for_task(session, task_id):
        return (
            session.query(Comment)
            .filter(Comment.task_id == task_id, Comment.is_deleted == False)  # noqa: E712
            .order_by(Comment.created_at)
            .all()
        )
