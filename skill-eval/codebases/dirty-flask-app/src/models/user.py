# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum
from sqlalchemy.orm import relationship
import enum
from src.db import Base


class UserRole(enum.Enum):
    admin = "admin"
    editor = "editor"
    viewer = "viewer"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(256), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.viewer)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    tasks = relationship("Task", back_populates="owner", lazy="select")
    comments = relationship("Comment", back_populates="author", lazy="select")

    def __repr__(self):
        return f"<User id={self.id} username={self.username!r} role={self.role.value}>"

    def is_admin(self):
        return self.role == UserRole.admin

    def can_edit(self):
        return self.role in (UserRole.admin, UserRole.editor)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role.value,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }

    @staticmethod
    def validate_role(role_str):
        try:
            return UserRole(role_str)
        except ValueError:
            return None
