"""Business operations that change fleet state."""

from dataclasses import dataclass

from django.db import transaction

from fleet.models import Office, Vehicle


@dataclass(frozen=True)
class ActiveVehicleTransferResult:
    """The outcome of moving active vehicles between offices."""

    moved_vehicle_count: int


def transfer_active_vehicles(
    *,
    source_office: Office,
    target_office: Office,
) -> ActiveVehicleTransferResult:
    """Move every active vehicle from one office to another as one operation."""
    if source_office == target_office:
        raise ValueError("Source and target offices must be different.")

    with transaction.atomic():
        # Evaluate the ids while holding row locks, then update exactly those rows in bulk.
        vehicle_ids = list(
            Vehicle.objects.filter(
                office=source_office,
                active=True,
            )
            .select_for_update()
            .values_list("pk", flat=True)
        )
        moved_vehicle_count = Vehicle.objects.filter(pk__in=vehicle_ids).update(
            office=target_office
        )

    return ActiveVehicleTransferResult(moved_vehicle_count=moved_vehicle_count)
