from app.core.enums import DerivedPaperStatus, StepName, StepStatus


def compute_paper_status(steps) -> DerivedPaperStatus:
    """Compute derived paper status from step statuses.

    Priority: error > processing > enriched > readable > pending
    """
    if not steps:
        return DerivedPaperStatus.PENDING

    step_map: dict[str, str] = {}
    for s in steps:
        name = s.step if hasattr(s, "step") else s["step"]
        status = s.status if hasattr(s, "status") else s["status"]
        step_map[str(name)] = str(status)

    if any(v == StepStatus.ERROR for v in step_map.values()):
        return DerivedPaperStatus.ERROR
    if any(v == StepStatus.PROCESSING for v in step_map.values()):
        return DerivedPaperStatus.PROCESSING

    non_crossref = {
        k: v for k, v in step_map.items() if k != StepName.CROSSREFING
    }
    if non_crossref and all(v == StepStatus.DONE for v in non_crossref.values()):
        return DerivedPaperStatus.ENRICHED
    if step_map.get(StepName.SUMMARIZING) == StepStatus.DONE:
        return DerivedPaperStatus.READABLE

    return DerivedPaperStatus.PENDING
