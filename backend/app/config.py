from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_id: str = "legion-dashboard"
    app_name: str = "LEGION Dashboard"
    app_version: str = "0.1.0"
    database_url: str = "sqlite:///./legion_dashboard.db"

    class Config:
        env_file = ".env"


settings = Settings()
