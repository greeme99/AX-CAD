from alembic import context

from backend.db.session import engine

with engine().connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
