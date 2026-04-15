from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.schemas import ErrorResponse
from app.graph import service
from app.graph.dependencies import get_ego_depth, get_graph_filters
from app.graph.schemas import GraphData, GraphFilters
from app.papers.dependencies import get_paper_or_404
from app.papers.models import Paper
from app.ratelimit import limiter

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get(
    "",
    response_model=GraphData,
    status_code=status.HTTP_200_OK,
    summary="Get global paper graph",
    description=(
        "Build a global graph of papers (nodes) and cross-references (edges) "
        "with optional filters. Returns 413 if clamps exceeded."
    ),
    responses={
        304: {"description": "ETag matches If-None-Match; not modified"},
        413: {"model": ErrorResponse, "description": "Graph exceeds clamps"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("30/minute")
async def get_graph(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    filters: GraphFilters = Depends(get_graph_filters),
):
    etag = await service.compute_graph_etag(db, filters)
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip('"') == etag:
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})

    graph = await service.build_graph(db, filters)
    response.headers["ETag"] = f'"{etag}"'
    return graph


@router.get(
    "/paper/{paper_id}",
    response_model=GraphData,
    status_code=status.HTTP_200_OK,
    summary="Get paper ego network",
    description=(
        "Build an ego network centered on `paper_id` (BFS up to `depth` hops)."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Paper not found"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("60/minute")
async def get_paper_ego_network(
    request: Request,
    paper: Paper = Depends(get_paper_or_404),
    db: AsyncSession = Depends(get_db),
    depth: int = Depends(get_ego_depth),
    filters: GraphFilters = Depends(get_graph_filters),
):
    return await service.build_ego_network(db, paper.id, depth, filters)
