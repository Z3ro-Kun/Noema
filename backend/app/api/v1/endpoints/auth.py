"""Account and session endpoints.

Minimum viable: register, log in, log out, identify yourself. No OAuth, no
email verification, no password reset, no profile editing. Those are real
features with real failure modes and none of them are needed to attribute an
interaction to a user, which is all this phase requires.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_token, get_current_user, get_db
from app.models import User
from app.schemas.library import SessionRead, UserLogin, UserRead, UserRegister
from app.services import auth_service

router = APIRouter(prefix="/auth")


@router.post("/register", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegister, db: AsyncSession = Depends(get_db)) -> SessionRead:
    """Create an account and return a session for it."""
    try:
        user = await auth_service.register_user(
            db,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
        )
    except auth_service.EmailAlreadyRegisteredError:
        # 409 rather than a 200 that pretends to have registered: this
        # endpoint is authenticated-by-nobody and a caller genuinely needs
        # to know the address is taken in order to log in instead.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="that email address is already registered",
        ) from None
    except (auth_service.WeakPasswordError, auth_service.InvalidEmailError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None

    session_row, token = await auth_service.create_session(db, user)
    await db.commit()

    return SessionRead(
        access_token=token,
        expires_at=session_row.expires_at,
        user=UserRead.model_validate(user),
    )


@router.post("/login", response_model=SessionRead)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)) -> SessionRead:
    try:
        user = await auth_service.authenticate(
            db, email=payload.email, password=payload.password
        )
    except auth_service.InvalidCredentialsError:
        # One message for every failure mode -- wrong password, no such
        # account, disabled account -- so this cannot enumerate users.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    session_row, token = await auth_service.create_session(db, user)
    await db.commit()

    return SessionRead(
        access_token=token,
        expires_at=session_row.expires_at,
        user=UserRead.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    token: str = Depends(get_current_token),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Revoke this session server-side, so the token stops working immediately."""
    await auth_service.revoke_session(db, token)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserRead)
async def read_me(user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(user)
