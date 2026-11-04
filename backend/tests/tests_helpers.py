def reset_settings():
    from conftest import _reset
    from app.core.db import init_db

    _reset()
    init_db()
