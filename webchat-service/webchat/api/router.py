"""Public chat API assembled from capability-focused route modules."""

from fastapi import APIRouter

from .conversations import router as conversation_router
from .drafts import router as draft_router
from .enquiries import router as enquiry_router
from .workshop import router as workshop_router

router = APIRouter(prefix="/api/chat/v1")
router.include_router(conversation_router)
router.include_router(enquiry_router)
router.include_router(workshop_router)
router.include_router(draft_router)
