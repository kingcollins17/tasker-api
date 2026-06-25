from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # App Settings
    PROJECT_NAME: str = "Tasker API"
    VERSION: str = "1.0.0"
    
    # Security Settings
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    
    # Database Settings
    DATABASE_URL: str 
    # Redis / Celery Settings
    REDIS_URL: str 

    # Paystack Settings
    PAYSTACK_SECRET_KEY: str 
    PAYSTACK_BASE_URL: str 

    # OTP Settings
    OTP_EXPIRY_SECONDS: int 
    OTP_COOLDOWN_SECONDS: int 
    OTP_MAX_ATTEMPTS: int 

    
    # Enable reading from .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
    )

settings = Settings()
