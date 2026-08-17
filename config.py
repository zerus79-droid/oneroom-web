import os

# MariaDB settings - read from environment or use defaults
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "sinbee0")
DB_NAME = os.getenv("DB_NAME", "sinbee")

SECRET_KEY = "oneroom-local-dev-key-change-later"
