from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行配置。优先读环境变量，其次读项目根目录的 .env 文件。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "survey_system"
    jwt_secret: str = "dev-only-secret-please-change-in-dot-env-file"
    jwt_expire_minutes: int = 60 * 24
    # bcrypt 计算强度；12 约 0.2 秒一次，测试中调低以加快速度
    bcrypt_rounds: int = 12


settings = Settings()
