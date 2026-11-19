"""Application settings.

Version: 2.0.0 (2026-11-20)
Changed from 1.x:
  - SECRET_KEY and GODMODE_SECRET no longer ship with usable defaults. In any
    environment other than `local`, startup fails if they are unset. Previously
    SECRET_KEY defaulted to "changeme-in-production", which meant a missing env
    var silently made every JWT in the system forgeable by anyone who had read
    this public repo.
  - ENVIRONMENT added. God mode is refused outside `local`/`dev`.
  - CORS_ORIGINS added so main.py no longer needs allow_origins=["*"].
  - ALLOW_FAKE_REDIS added. The fakeredis fallback is a local-development
    convenience and is now opt-in; in production a Redis outage must surface as
    an error rather than silently splitting workers onto private in-memory
    stores (which desynchronises the auction across pods).
"""
from typing import Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Environment ───────────────────────────────────────────────────────────
    # `local`  — developer machine; permissive defaults, fakeredis allowed
    # `dev`    — shared dev/staging deployment
    # `prod`   — production
    ENVIRONMENT: Literal["local", "dev", "prod"] = "local"

    # ── App secrets ───────────────────────────────────────────────────────────
    # No usable default outside `local`. See _validate_secrets below.
    SECRET_KEY: Optional[str] = None
    GODMODE_SECRET: Optional[str] = None

    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # 12h (was 24h)
    UPLOAD_DIR: str = "./uploads"

    # Site URL for email links (no trailing slash)
    SITE_URL: str = "http://localhost"

    # Comma-separated list of allowed browser origins, e.g.
    #   CORS_ORIGINS=https://auction.example.com,https://www.auction.example.com
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost"

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./auction.db"

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"
    # Opt-in in-memory Redis substitute for local development only.
    ALLOW_FAKE_REDIS: bool = False

    # ── Auction defaults ──────────────────────────────────────────────────────
    # Base countdown for a player, in seconds. The old value was 180s and every
    # bid reset it to the full duration, so a contested player could run for
    # twenty minutes. See SOFT_CLOSE_* below.
    AUCTION_TIMER_SECONDS: int = Field(default=30, ge=5, le=600)
    # When a bid lands with this many seconds or fewer remaining, the clock is
    # extended by SOFT_CLOSE_EXTENSION_SECONDS rather than reset to full.
    SOFT_CLOSE_THRESHOLD_SECONDS: int = Field(default=10, ge=0, le=120)
    SOFT_CLOSE_EXTENSION_SECONDS: int = Field(default=10, ge=1, le=120)
    # Each successive extension on the same player shrinks by this factor, so a
    # two-team bidding war still terminates. 1.0 disables the shrink.
    SOFT_CLOSE_SHRINK_FACTOR: float = Field(default=0.8, gt=0.0, le=1.0)
    SOFT_CLOSE_MIN_EXTENSION_SECONDS: int = Field(default=3, ge=1, le=60)

    # ── AWS SES ───────────────────────────────────────────────────────────────
    AWS_MAIL_ACCESS_KEY_ID: Optional[str] = None
    AWS_MAIL_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "ap-south-1"
    EMAIL_FROM: str = "auction@yourdomain.com"

    # ── AWS S3 ────────────────────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_BUCKET_NAME: Optional[str] = None

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "prod"

    @property
    def godmode_enabled(self) -> bool:
        """God mode is a development shortcut and must never be live in prod."""
        return self.ENVIRONMENT == "local" and bool(self.GODMODE_SECRET)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def email_enabled(self) -> bool:
        return bool(self.AWS_MAIL_ACCESS_KEY_ID and self.AWS_MAIL_SECRET_ACCESS_KEY)

    @property
    def s3_enabled(self) -> bool:
        key = self.AWS_ACCESS_KEY_ID or self.AWS_MAIL_ACCESS_KEY_ID
        secret = self.AWS_SECRET_ACCESS_KEY or self.AWS_MAIL_SECRET_ACCESS_KEY
        return bool(key and secret and self.AWS_BUCKET_NAME)

    @property
    def fake_redis_allowed(self) -> bool:
        return self.ALLOW_FAKE_REDIS and self.ENVIRONMENT == "local"

    # ── Validation ────────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _validate_secrets(self) -> "Settings":
        if self.ENVIRONMENT == "local":
            # Local development: generate ephemeral secrets so `uvicorn app.main:app`
            # works with no .env at all. Tokens do not survive a restart, which is
            # correct for a dev machine and useless to an attacker.
            if not self.SECRET_KEY:
                import secrets as _secrets

                self.SECRET_KEY = _secrets.token_hex(32)
            return self

        missing = [
            name
            for name in ("SECRET_KEY",)
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                f"{', '.join(missing)} must be set when ENVIRONMENT={self.ENVIRONMENT}. "
                "Generate one with: openssl rand -hex 32"
            )

        weak = {"changeme-in-production", "supersecretkey-change-in-production", "GODMODE_CHANGEME"}
        if self.SECRET_KEY in weak:
            raise ValueError(
                "SECRET_KEY is still set to the example value from .env.example. "
                "Generate a real one with: openssl rand -hex 32"
            )
        if len(self.SECRET_KEY) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters.")

        if self.GODMODE_SECRET:
            raise ValueError(
                f"GODMODE_SECRET must not be set when ENVIRONMENT={self.ENVIRONMENT}. "
                "God mode is a local-development shortcut only."
            )
        return self

    class Config:
        env_file = ".env"


settings = Settings()
