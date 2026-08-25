import pytest
from pydantic import ValidationError

from jobradar.config import Settings


def test_cors_origins_are_normalized_and_deduplicated() -> None:
    settings = Settings(
        cors_allowed_origins=(
            " http://localhost:5173/;https://jobradar.vercel.app;https://jobradar.vercel.app "
        )
    )

    assert settings.cors_origins == (
        "http://localhost:5173",
        "https://jobradar.vercel.app",
    )


@pytest.mark.parametrize(
    "origin",
    (
        "*",
        "https://jobradar.vercel.app/path",
        "https://user:password@jobradar.vercel.app",
        "ftp://jobradar.vercel.app",
    ),
)
def test_cors_origins_reject_unsafe_values(origin: str) -> None:
    with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS"):
        Settings(cors_allowed_origins=origin)
