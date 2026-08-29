import os
import secrets

# MariaDB settings - read from environment or use defaults
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "sinbee")

# Keep credentials out of source control.  Set SECRET_KEY in the environment
# for a stable value; the process-local fallback is only for development and
# invalidates sessions whenever the application restarts.
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(48)
