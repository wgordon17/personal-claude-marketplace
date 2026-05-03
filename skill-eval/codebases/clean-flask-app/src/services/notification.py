import logging

import requests
from src.utils.validation import validate_webhook_url

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 5


def send_webhook(url: str, payload: dict, headers: dict = None) -> bool:
    if not validate_webhook_url(url):
        logger.warning("Rejected webhook to disallowed URL: %s", url)
        return False

    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers or {},
            timeout=_DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.Timeout:
        logger.warning("Webhook timed out for URL: %s", url)
        return False
    except requests.exceptions.ConnectionError as exc:
        logger.warning("Webhook connection error for URL %s: %s", url, exc)
        return False
    except requests.exceptions.HTTPError as exc:
        logger.warning("Webhook HTTP error for URL %s: %s", url, exc)
        return False
    except Exception as exc:
        # Notifications are best-effort. Log and continue rather than propagating.
        logger.exception("Unexpected error sending webhook to %s: %s", url, exc)
        return False


def notify_ticket_created(webhook_url: str, ticket_id: int, project_id: int, title: str) -> None:
    send_webhook(
        webhook_url,
        {
            "event": "ticket.created",
            "ticket_id": ticket_id,
            "project_id": project_id,
            "title": title,
        },
    )


def notify_project_member_added(webhook_url: str, project_id: int, user_id: int) -> None:
    send_webhook(
        webhook_url,
        {
            "event": "project.member_added",
            "project_id": project_id,
            "user_id": user_id,
        },
    )
