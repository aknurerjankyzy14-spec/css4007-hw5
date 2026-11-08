import json
from pathlib import Path
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def read_json(filename):
    return json.loads(
        (DATA_DIR / filename).read_text(encoding="utf-8")
    )


def normalize_topic(topic):
    topic = topic.strip().lower()
    data = read_json("helpdesk.json")

    for category, aliases in data["topic_aliases"].items():
        if topic == category or topic in aliases:
            return category

    raise ValueError("Supported topics: wifi and password.")


def find_instructions(topic):
    """Return instructions from the fictional local guide."""
    category = normalize_topic(topic)
    data = read_json("helpdesk.json")

    return {
        "category": category,
        **data["instructions"][category],
        "source": "data/helpdesk.json",
    }


def check_service_status(service, status_profile):
    """Read a service status from the selected local snapshot."""
    service = service.strip().lower()

    # Accept a topic such as password or a service such as
    # account_recovery.
    mapping = read_json("helpdesk.json")["topic_services"]

    if service != "account_recovery":
        category = normalize_topic(service)
        service = mapping[category]

    profiles = read_json("service_status.json")["profiles"]

    if status_profile not in profiles:
        raise ValueError(
            f"Unknown status profile: {status_profile}"
        )

    snapshot = profiles[status_profile]

    if service not in snapshot:
        raise ValueError(f"Unknown service: {service}")

    return {
        "service": service,
        "status_profile": status_profile,
        **snapshot[service],
        "source": "data/service_status.json",
    }


def prepare_ticket(
    *,
    user_id,
    thread_id,
    category,
    summary,
    steps_tried,
    status_profile,
    status_result=None,
):
    """Save a local draft; never send it to a helpdesk."""
    category = normalize_topic(category)

    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Ask the user for a problem description.")

    if not isinstance(steps_tried, list):
        raise ValueError("steps_tried must be a list.")

    if not steps_tried or any(
        not isinstance(step, str) or not step.strip()
        for step in steps_tried
    ):
        raise ValueError(
            "Ask what the user has tried. "
            "If nothing was tried, record that explicitly."
        )

    profiles = read_json("service_status.json")["profiles"]

    if status_profile not in profiles:
        raise ValueError("Unknown status profile.")

    data = read_json("helpdesk.json")
    expected_service = data["topic_services"][category]

    if status_result is not None:
        if (
            status_result.get("service") != expected_service
            or status_result.get("status_profile") != status_profile
        ):
            raise ValueError(
                "The status result does not match this ticket."
            )

        # Validate the result against the selected fixture.
        expected = check_service_status(
            expected_service,
            status_profile,
        )

        if status_result != expected:
            raise ValueError("Invalid service-status result.")

    ticket = read_json("ticket_template.json")["ticket"]
    ticket_id = f"DRAFT-{uuid4().hex}"

    ticket.update({
        "ticket_id": ticket_id,
        "status": "draft",
        "user_id": user_id,
        "thread_id": thread_id,
        "category": category,
        "summary": summary.strip(),
        "steps_tried": [
            step.strip() for step in steps_tried
        ],
        "service_status": (
            status_result["status"]
            if status_result is not None
            else "not_checked"
        ),
        "status_profile": status_profile,
        "reference_ids": (
            [data["instructions"][category]["instruction_id"]]
            if status_result is not None
            else []
        ),
    })

    # A guide is not evidence that the user followed its steps.
    # reference_ids is left empty unless a caller later supplies
    # references that were actually retrieved.
    ticket["reference_ids"] = []

    ticket_dir = BASE_DIR / "outputs" / "tickets"
    ticket_dir.mkdir(parents=True, exist_ok=True)

    filename = ticket_dir / f"{ticket_id}.json"
    filename.write_text(
        json.dumps(ticket, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ticket": ticket,
        "filename": str(filename.relative_to(BASE_DIR)),
        "sent": False,
        "source": "data/ticket_template.json",
    }