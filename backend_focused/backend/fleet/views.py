from django.db.models import Prefetch
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from fleet import selectors, services
from fleet.models import (
    MaintenanceRecord,
    MaintenanceType,
    Mechanic,
    Office,
    Vehicle,
    VehicleMake,
    VehicleModel,
)
from fleet.serializers import (
    MaintenanceRecordSerializer,
    MaintenanceTypeSerializer,
    MechanicSerializer,
    MechanicWorkloadSerializer,
    OfficeFleetTransferResponseSerializer,
    OfficeFleetTransferSerializer,
    OfficeSerializer,
    OfficeSummarySerializer,
    VehicleDetailSerializer,
    VehicleDuplicateCheckParamsSerializer,
    VehicleMaintenanceDueSerializer,
    VehicleMaintenanceRecordSerializer,
    VehicleMakeSerializer,
    VehicleModelSerializer,
    VehicleOfficeAssignmentSerializer,
    VehicleSearchParamsSerializer,
    VehicleSerializer,
)

protected_destroy_schema = extend_schema_view(
    destroy=extend_schema(
        responses={
            204: None,
            409: OpenApiResponse(
                response=inline_serializer(
                    name="ProtectedDeleteError",
                    fields={"detail": serializers.CharField()},
                ),
                description="The object is referenced by other records and cannot be deleted.",
            ),
        },
    ),
)


class TransferActiveVehiclesAPIView(APIView):
    """Move every active vehicle from one office to another."""

    @extend_schema(
        request=OfficeFleetTransferSerializer,
        responses={
            200: OfficeFleetTransferResponseSerializer,
            400: OpenApiResponse(
                description="Invalid office ids or identical source and target offices."
            ),
        },
    )
    def post(self, request):
        serializer = OfficeFleetTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        source_office = serializer.validated_data["source_office"]
        target_office = serializer.validated_data["target_office"]
        result = services.transfer_active_vehicles(
            source_office=source_office,
            target_office=target_office,
        )
        response_data = {
            "source_office_id": source_office.pk,
            "target_office_id": target_office.pk,
            "moved_vehicle_count": result.moved_vehicle_count,
        }
        return Response(OfficeFleetTransferResponseSerializer(response_data).data)


@protected_destroy_schema
class OfficeViewSet(viewsets.ModelViewSet):
    queryset = Office.objects.order_by("name", "id")
    serializer_class = OfficeSerializer

    @extend_schema(responses=OfficeSummarySerializer(many=True))
    @action(detail=False, methods=["get"], pagination_class=None)
    def summary(self, request):
        """Return every office with its active vehicle count and maintenance totals.

        The cost covers the last 12 months up to today, both ends inclusive. Maintenance counts
        for the vehicle's current office.
        """
        offices = selectors.office_summaries(today=timezone.localdate())
        return Response(OfficeSummarySerializer(offices, many=True).data)


@protected_destroy_schema
class VehicleMakeViewSet(viewsets.ModelViewSet):
    queryset = VehicleMake.objects.order_by("name", "id")
    serializer_class = VehicleMakeSerializer


@protected_destroy_schema
class VehicleModelViewSet(viewsets.ModelViewSet):
    queryset = VehicleModel.objects.select_related("make").order_by(
        "make__name",
        "name",
        "id",
    )
    serializer_class = VehicleModelSerializer


@extend_schema_view(
    list=extend_schema(
        parameters=[VehicleSearchParamsSerializer],
        responses={
            200: VehicleSerializer(many=True),
            400: OpenApiResponse(
                description=(
                    "Invalid search parameters, unknown office, make or model ids, or a model "
                    "that does not belong to the given make."
                ),
            ),
        },
    ),
)
class VehicleViewSet(viewsets.ModelViewSet):
    queryset = Vehicle.objects.select_related("office", "model__make").order_by("id")
    serializer_class = VehicleSerializer

    def list(self, request, *args, **kwargs):
        """List vehicles filtered by any combination of the search parameters.

        ``make`` and ``model`` are ids. The maintenance date range and mechanic certification
        must match the same maintenance record.
        """
        params = VehicleSearchParamsSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        vehicles = selectors.filter_vehicles(self.get_queryset(), **params.validated_data)
        page = self.paginate_queryset(vehicles)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def get_queryset(self):
        # Only the detail view returns the complete history. List, writes and the paginated
        # history action must not load every record of the vehicle.
        queryset = super().get_queryset()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch(
                    "maintenance_records",
                    queryset=MaintenanceRecord.objects.select_related(
                        "mechanic",
                        "type",
                    ).order_by("-performed_on", "-id"),
                )
            )
        return queryset

    def get_serializer_class(self):
        # Write responses keep the CRUD shape, without the history.
        if self.action == "retrieve":
            return VehicleDetailSerializer
        return super().get_serializer_class()

    @extend_schema(responses=VehicleMaintenanceRecordSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="maintenance-records")
    def maintenance_records(self, request, pk=None):
        """Return the vehicle's maintenance history, newest first, paginated."""
        vehicle = self.get_object()
        records = (
            MaintenanceRecord.objects.filter(vehicle=vehicle)
            .select_related("mechanic", "type")
            .order_by("-performed_on", "-id")
        )
        page = self.paginate_queryset(records)
        serializer = VehicleMaintenanceRecordSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(
        request=VehicleOfficeAssignmentSerializer,
        responses={
            200: VehicleSerializer,
            400: OpenApiResponse(description="Missing or unknown office_id."),
        },
    )
    @action(detail=True, methods=["put"], url_path="office")
    def assign_office(self, request, pk=None):
        """Move the vehicle to another office.

        Only the current office is stored, with no assignment history. Sending the current
        office again is a no-op.
        """
        vehicle = self.get_object()
        assignment = VehicleOfficeAssignmentSerializer(data=request.data)
        assignment.is_valid(raise_exception=True)
        vehicle.office = assignment.validated_data["office"]
        # Write only the office so a concurrent edit to other fields is not overwritten.
        vehicle.save(update_fields=["office"])
        return Response(VehicleSerializer(vehicle).data)

    @extend_schema(responses=VehicleMaintenanceDueSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="maintenance-due")
    def maintenance_due(self, request):
        """Return active vehicles never maintained or last maintained more than 365 days ago.

        Never-maintained vehicles come first, then the oldest maintenance. Maintenance dated after
        today is ignored.
        """
        vehicles = selectors.vehicles_needing_maintenance(today=timezone.localdate())
        page = self.paginate_queryset(vehicles)
        serializer = VehicleMaintenanceDueSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(
        parameters=[VehicleDuplicateCheckParamsSerializer],
        responses={
            200: inline_serializer(
                name="VehicleConflicts",
                fields={
                    "conflicts": serializers.ListField(
                        child=serializers.ChoiceField(choices=("vin", "license_plate")),
                    ),
                },
            ),
            400: OpenApiResponse(
                description="Missing, blank or too long vin or license_plate.",
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="duplicate-check")
    def duplicate_check(self, request):
        """Return which of the given VIN and license plate clash with existing vehicles.

        A VIN clashes with any vehicle, a license plate only with an active one. Values are
        trimmed like on create and update, and matching is case-sensitive.
        """
        params = VehicleDuplicateCheckParamsSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        return Response({"conflicts": selectors.vehicle_conflicts(**params.validated_data)})


@protected_destroy_schema
class MechanicViewSet(viewsets.ModelViewSet):
    queryset = Mechanic.objects.order_by("name", "id")
    serializer_class = MechanicSerializer

    @extend_schema(responses=MechanicWorkloadSerializer(many=True))
    @action(detail=False, methods=["get"], pagination_class=None)
    def workload(self, request):
        """Return every mechanic with current-year maintenance count and cost, busiest first.

        The year runs from January 1 to today. Mechanics without work this year are included with
        zero totals.
        """
        mechanics = selectors.mechanic_workload(today=timezone.localdate())
        return Response(MechanicWorkloadSerializer(mechanics, many=True).data)


@protected_destroy_schema
class MaintenanceTypeViewSet(viewsets.ModelViewSet):
    queryset = MaintenanceType.objects.order_by("name", "id")
    serializer_class = MaintenanceTypeSerializer


class MaintenanceRecordViewSet(viewsets.ModelViewSet):
    queryset = MaintenanceRecord.objects.select_related(
        "vehicle__model__make",
        "mechanic",
        "type",
    )
    serializer_class = MaintenanceRecordSerializer
