"""Create all tables. Usage:  python scripts/init_db.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import db  # noqa: E402

if not db.enabled():
    sys.exit("DATABASE_URL is not set. Add it to .env first (see .env.example).")

db.init_schema()
db.close_pool()
print("Database schema is ready.")