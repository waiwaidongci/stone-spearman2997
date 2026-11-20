Schema v2 (this change): `observations.sequence` (nullable integer, unique per study with `source_key`), `runs.progress` and `runs.row_count`.

`Base.metadata.create_all` at startup creates new tables but never alters existing ones. An existing v1 MySQL volume keeps working for reads but will not gain the new columns. For a database that must keep its v1 data, apply:

```sql
ALTER TABLE observations ADD COLUMN sequence INT NULL;
CREATE UNIQUE INDEX uq_observations_study_source ON observations (study_id, source_key);
CREATE UNIQUE INDEX uq_observations_study_sequence ON observations (study_id, sequence);
ALTER TABLE runs ADD COLUMN progress JSON NULL;
ALTER TABLE runs ADD COLUMN row_count INT NOT NULL DEFAULT 0;
```

MySQL allows repeated NULLs in a unique index, so legacy rows without a production sequence coexist; they are rejected at run creation until a sequence is entered.

The demonstration volume is disposable: `docker compose down -v` and re-seed for v2. No destructive migration is run automatically.
