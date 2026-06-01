# Schema migrations are owned by Alembic; this only bootstraps the first
# super-admin and is a no-op when ADMIN_INITIAL_PASSWORD is unset.
import datetime

import bcrypt
from sqlalchemy import select

from config import settings
from database import get_session
from models import User, UserRole


def create_default_admin():
    if settings.admin_initial_password is None:
        print(
            "ADMIN_INITIAL_PASSWORD not set; skipping default admin creation. "
            "Provision the super-admin manually."
        )
        return

    with get_session() as session:
        existing = session.scalar(select(User).where(User.role == UserRole.super_admin))
        if existing:
            print(f"Super admin already exists: {existing.email}")
            return

        password = settings.admin_initial_password.get_secret_value()
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        admin = User(
            email=settings.admin_email,
            password_hash=password_hash,
            first_name="Admin",
            last_name="Plateforme",
            role=UserRole.super_admin,
            is_active=True,
            sessions_invalidated_at=datetime.datetime.utcnow(),
        )
        session.add(admin)
        print(f"Default super admin created: {settings.admin_email}")


if __name__ == "__main__":
    create_default_admin()
