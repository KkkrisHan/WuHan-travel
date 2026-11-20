from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)
    amap_web_service_key: str = ""
    amap_js_security_code: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    ai_provider: str = "openai"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    frontend_origin: str = "http://localhost:5173,http://127.0.0.1:5173"
    task_database: str = "./tasks.sqlite3"


settings = Settings()
if not settings.deepseek_api_key:
    import os
    settings.deepseek_api_key = os.environ.get("wuhantravel", "")
