# v2/db

Placeholder for schema migrations once the model layer stabilises.

During scaffolding we use `init_db.create_all()` which is fine for throwaway
SQLite dev databases. Before milestone 3 (tenant isolation), swap in
**Alembic** migrations here:

```bash
alembic init migrations
# point sqlalchemy.url at v2/api/crypto_bot_v2.db
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

Structure (planned):

```
v2/db/
  alembic.ini
  migrations/
    env.py
    versions/
      0001_initial.py
      0002_add_*.py
```
