from fastapi import APIRouter

from app.routers import auth_public, auth_recovery

router = APIRouter(tags=["auth"])
router.include_router(auth_public.router)
router.include_router(auth_recovery.router)
