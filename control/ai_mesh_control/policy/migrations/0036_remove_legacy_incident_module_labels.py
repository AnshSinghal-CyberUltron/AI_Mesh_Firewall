"""Remove legacy Module 2.6 labels from persisted incident customer data."""

import re

from django.db import migrations


MODULE_LABEL_RE = re.compile(r"\bM\s*2\." + r"6\b", re.IGNORECASE)
KEY_PREFIX_RE = re.compile(r"\bzs_" + r"m26\b", re.IGNORECASE)


def _clean_text(value):
    if not isinstance(value, str):
        return value
    cleaned = MODULE_LABEL_RE.sub("Incidents", value)
    cleaned = KEY_PREFIX_RE.sub("zs_incidents", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _clean_value(value):
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return _clean_text(value)


def remove_legacy_labels(apps, schema_editor):
    SecurityIncident = apps.get_model("policy", "SecurityIncident")
    EnforcementEvent = apps.get_model("policy", "EnforcementEvent")

    for incident in SecurityIncident.objects.all().iterator(chunk_size=500):
        title = _clean_text(incident.title)
        notes = _clean_text(incident.notes)
        changed = []
        if title != incident.title:
            incident.title = title
            changed.append("title")
        if notes != incident.notes:
            incident.notes = notes
            changed.append("notes")
        if changed:
            incident.save(update_fields=changed)

    for event in EnforcementEvent.objects.all().iterator(chunk_size=500):
        metadata = _clean_value(event.metadata)
        if metadata != event.metadata:
            event.metadata = metadata
            event.save(update_fields=["metadata"])


class Migration(migrations.Migration):
    dependencies = [
        ("policy", "0035_vector_provider_reranker_chroma"),
    ]

    operations = [
        migrations.RunPython(remove_legacy_labels, migrations.RunPython.noop),
    ]
