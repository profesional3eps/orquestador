from sqlalchemy import create_engine, text
from app.config.settings import get_settings

e = create_engine(get_settings().postgres_url)
with e.connect() as c:
    r = c.execute(text("SELECT * FROM administrativo.tb_cie10 LIMIT 1")).mappings().first()
    print(list(r.keys())[:15] if r else "empty")
