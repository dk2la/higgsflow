"""Actions on an existing Request of a Project: cancel and delete.

Both work on Requests the Project knows: the id must have a local record
(`request_not_found` otherwise), so a stray id never touches another Project's or
another tool's Request. The API call comes first; the record is updated only after
the API has accepted the action.
"""

from __future__ import annotations

from hfcore import clock
from hfcore.api import ApiClient
from hfcore.projects import Project
from hfcore.requests import RequestRecord, load_request, save_request


def cancel_request(client: ApiClient, project: Project, request_id: str) -> RequestRecord:
    """Cancel a queued Request and mark its record `canceled`."""
    record = load_request(project, request_id)
    client.cancel(request_id)
    record.status = "canceled"
    record.updated_at = clock.now()
    save_request(project, record)
    return record


def delete_request(client: ApiClient, project: Project, request_id: str) -> RequestRecord:
    """Delete a finished Request's data on the API side and record when; local files stay."""
    record = load_request(project, request_id)
    client.delete(request_id)
    now = clock.now()
    record.deleted_at = now
    record.updated_at = now
    save_request(project, record)
    return record
