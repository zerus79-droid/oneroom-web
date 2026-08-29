import os
import secrets

# Load the local, git-ignored .env file without requiring an extra package.
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _env_file:
        for _line in _env_file:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _value = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))

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
