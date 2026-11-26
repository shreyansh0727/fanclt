"""Account links shown in the app: which voice assistants can control this
account, and the button that cuts them off."""

from fastapi import APIRouter, Depends

from ..errors import not_found
from ..models import AssistantLinkOut
from ..services import assistants
from ..security import User
from .deps import current_user, get_ctx

router = APIRouter(prefix="/assistants", tags=["assistants"])


@router.get("/links", response_model=list[AssistantLinkOut])
async def list_links(user: User = Depends(current_user), ctx=Depends(get_ctx)):
    return await assistants.list_owner_links(ctx, user.uid)


@router.delete("/links/{link_id}", status_code=204)
async def remove_link(link_id: str, user: User = Depends(current_user), ctx=Depends(get_ctx)):
    link = await ctx.repo.get_link(link_id)
    if not link or link.get("owner_id") != user.uid:
        raise not_found("link")
    await ctx.repo.delete_link(link_id)
