import os

# Settings has no default for the database URI. Give the suite one before any test
# imports the app; no database is contacted.
os.environ.setdefault("FREYJA_DATABASE_URI", "postgresql://localhost:5432/freyja_test")
