from rest_framework import serializers

from fleet.models import (
    MaintenanceRecord,
    MaintenanceType,
    Mechanic,
    Office,
    Vehicle,
    VehicleMake,
    VehicleModel,
)


class OfficeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Office
        fields = ("id", "name", "city")


class OfficeFleetTransferSerializer(serializers.Serializer):
    """Validate the offices that participate in an active-fleet transfer."""

    source_office_id = serializers.PrimaryKeyRelatedField(
        source="source_office",
        queryset=Office.objects.all(),
    )
    target_office_id = serializers.PrimaryKeyRelatedField(
        source="target_office",
        queryset=Office.objects.all(),
    )

    def validate(self, attrs):
        if attrs["source_office"] == attrs["target_office"]:
            raise serializers.ValidationError(
                {"target_office_id": ["Must be different from source_office_id."]}
            )
        return attrs


class OfficeFleetTransferResponseSerializer(serializers.Serializer):
    source_office_id = serializers.IntegerField()
    target_office_id = serializers.IntegerField()
    moved_vehicle_count = serializers.IntegerField()


class MechanicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mechanic
        fields = ("id", "name", "certification_number", "active")


class MaintenanceTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaintenanceType
        fields = ("id", "name")


class VehicleMakeSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleMake
        fields = ("id", "name")


class VehicleModelSerializer(serializers.ModelSerializer):
    make = VehicleMakeSerializer(read_only=True)
    make_id = serializers.PrimaryKeyRelatedField(
        source="make",
        queryset=VehicleMake.objects.all(),
        write_only=True,
    )

    class Meta:
        model = VehicleModel
        fields = ("id", "name", "make", "make_id")


class VehicleModelSummarySerializer(serializers.ModelSerializer):
    """Model without its make, for vehicle reads that already show the make next to it."""

    class Meta:
        model = VehicleModel
        fields = ("id", "name")


class VehicleSerializer(serializers.ModelSerializer):
    # Vehicle stores only the model. The make is read from it and cannot be written.
    make = VehicleMakeSerializer(source="model.make", read_only=True)
    model = VehicleModelSummarySerializer(read_only=True)
    model_id = serializers.PrimaryKeyRelatedField(
        source="model",
        # The make is loaded with the model so the write response needs no extra query.
        queryset=VehicleModel.objects.select_related("make"),
        write_only=True,
    )
    office = OfficeSerializer(read_only=True)
    office_id = serializers.PrimaryKeyRelatedField(
        source="office",
        queryset=Office.objects.all(),
        write_only=True,
    )

    class Meta:
        model = Vehicle
        fields = (
            "id",
            "vin",
            "license_plate",
            "make",
            "model",
            "model_id",
            "year",
            "active",
            "office",
            "office_id",
        )
        # The automatic validator only sees the conditional constraint partially,
        # so the active-plate rule is checked in validate() instead.
        extra_kwargs = {"license_plate": {"validators": []}}

    # Input keys that look writable but are not. Rejecting them avoids a silent no-op.
    rejected_input_errors = {
        "make": "Make is read-only and is derived from model_id.",
        "model": "Use model_id to set the vehicle model.",
        "make_id": "Make is derived from model_id and cannot be set directly.",
    }

    def to_internal_value(self, data):
        """Reject ``make``, ``model`` and ``make_id`` input next to the regular field errors."""
        rejected = {
            key: [message]
            for key, message in self.rejected_input_errors.items()
            if key in data
        }
        try:
            attrs = super().to_internal_value(data)
        except serializers.ValidationError as exc:
            if isinstance(exc.detail, dict):
                raise serializers.ValidationError({**exc.detail, **rejected}) from exc
            raise
        if rejected:
            raise serializers.ValidationError(rejected)
        return attrs

    def validate(self, attrs):
        """Reject the vehicle when it would share its plate with another active vehicle.

        Fields omitted from a partial update fall back to the current instance values,
        so the check always runs against the resulting vehicle state.
        """
        license_plate = attrs.get(
            "license_plate",
            getattr(self.instance, "license_plate", None),
        )
        active = attrs.get("active", getattr(self.instance, "active", True))

        if active:
            conflicts = Vehicle.objects.filter(
                active=True,
                license_plate=license_plate,
            )
            if self.instance is not None:
                conflicts = conflicts.exclude(pk=self.instance.pk)
            if conflicts.exists():
                raise serializers.ValidationError(
                    {
                        "license_plate": [
                            "An active vehicle with this license plate already exists."
                        ]
                    }
                )

        return attrs


class VehicleSummarySerializer(serializers.ModelSerializer):
    make = VehicleMakeSerializer(source="model.make", read_only=True)
    model = VehicleModelSummarySerializer(read_only=True)

    class Meta:
        model = Vehicle
        fields = ("id", "vin", "license_plate", "make", "model")


class MaintenanceRecordSerializer(serializers.ModelSerializer):
    vehicle = VehicleSummarySerializer(read_only=True)
    vehicle_id = serializers.PrimaryKeyRelatedField(
        source="vehicle",
        queryset=Vehicle.objects.all(),
        write_only=True,
    )
    mechanic = MechanicSerializer(read_only=True)
    mechanic_id = serializers.PrimaryKeyRelatedField(
        source="mechanic",
        queryset=Mechanic.objects.all(),
        write_only=True,
    )
    type = MaintenanceTypeSerializer(read_only=True)
    type_id = serializers.PrimaryKeyRelatedField(
        source="type",
        queryset=MaintenanceType.objects.all(),
        write_only=True,
    )

    class Meta:
        model = MaintenanceRecord
        fields = (
            "id",
            "vehicle",
            "vehicle_id",
            "mechanic",
            "mechanic_id",
            "type",
            "type_id",
            "performed_on",
            "cost",
            "notes",
        )


class OfficeSummarySerializer(serializers.ModelSerializer):
    active_vehicle_count = serializers.IntegerField(read_only=True)
    maintenance_cost_last_year = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    last_maintenance = serializers.DateField(read_only=True, allow_null=True)

    class Meta:
        model = Office
        fields = (
            "id",
            "name",
            "city",
            "active_vehicle_count",
            "maintenance_cost_last_year",
            "last_maintenance",
        )


class MechanicWorkloadSerializer(serializers.ModelSerializer):
    maintenance_count = serializers.IntegerField(read_only=True)
    total_cost = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = Mechanic
        fields = (
            "id",
            "name",
            "certification_number",
            "maintenance_count",
            "total_cost",
        )


class VehicleSearchParamsSerializer(serializers.Serializer):
    """Validate vehicle search query parameters.

    An empty value, as sent by a cleared form field, counts as not sent. Unknown ``office``,
    ``make`` or ``model`` ids are a 400 instead of an empty result, and so is a model that does
    not belong to the given make. ``mechanic_certification`` is a plain value filter, so an
    unknown certification just matches no vehicles.
    """

    office = serializers.PrimaryKeyRelatedField(
        queryset=Office.objects.all(),
        required=False,
    )
    # allow_null keeps a missing value as None instead of DRF's default False for query strings.
    active = serializers.BooleanField(required=False, allow_null=True)
    make = serializers.PrimaryKeyRelatedField(
        queryset=VehicleMake.objects.all(),
        required=False,
    )
    model = serializers.PrimaryKeyRelatedField(
        queryset=VehicleModel.objects.all(),
        required=False,
    )
    maintained_from = serializers.DateField(required=False)
    maintained_to = serializers.DateField(required=False)
    mechanic_certification = serializers.CharField(required=False, max_length=64)

    def validate(self, attrs):
        make = attrs.get("make")
        model = attrs.get("model")
        if make and model and model.make_id != make.pk:
            raise serializers.ValidationError(
                {"model": [f"Model {model.pk} does not belong to make {make.pk}."]}
            )
        maintained_from = attrs.get("maintained_from")
        maintained_to = attrs.get("maintained_to")
        if maintained_from and maintained_to and maintained_from > maintained_to:
            raise serializers.ValidationError(
                {"maintained_to": ["Must be on or after maintained_from."]}
            )
        return attrs


class VehicleMaintenanceRecordSerializer(serializers.ModelSerializer):
    mechanic = MechanicSerializer(read_only=True)
    type = MaintenanceTypeSerializer(read_only=True)

    class Meta:
        model = MaintenanceRecord
        fields = ("id", "performed_on", "type", "mechanic", "cost", "notes")
        read_only_fields = fields


class VehicleDetailSerializer(VehicleSerializer):
    maintenance_records = VehicleMaintenanceRecordSerializer(many=True, read_only=True)

    class Meta(VehicleSerializer.Meta):
        fields = VehicleSerializer.Meta.fields + ("maintenance_records",)


class VehicleOfficeAssignmentSerializer(serializers.Serializer):
    office_id = serializers.PrimaryKeyRelatedField(
        source="office",
        queryset=Office.objects.all(),
    )


class VehicleMaintenanceDueSerializer(VehicleSerializer):
    last_maintenance = serializers.DateField(read_only=True, allow_null=True)

    class Meta(VehicleSerializer.Meta):
        fields = VehicleSerializer.Meta.fields + ("last_maintenance",)


class VehicleDuplicateCheckParamsSerializer(serializers.Serializer):
    """Validate duplicate-check query parameters.

    Values are trimmed like in the create and update serializers, so the check sees the same
    value a save would store. Matching after that stays case-sensitive, like the constraints.
    """

    vin = serializers.CharField(max_length=17)
    license_plate = serializers.CharField(max_length=20)
