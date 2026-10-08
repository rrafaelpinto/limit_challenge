from django.urls import path
from rest_framework.routers import SimpleRouter

from fleet.views import (
    MaintenanceRecordViewSet,
    MaintenanceTypeViewSet,
    MechanicViewSet,
    OfficeViewSet,
    TransferActiveVehiclesAPIView,
    VehicleMakeViewSet,
    VehicleModelViewSet,
    VehicleViewSet,
)

router = SimpleRouter()
router.register("offices", OfficeViewSet)
router.register("vehicle-makes", VehicleMakeViewSet)
router.register("vehicle-models", VehicleModelViewSet)
router.register("vehicles", VehicleViewSet)
router.register("mechanics", MechanicViewSet)
router.register("maintenance-types", MaintenanceTypeViewSet)
router.register("maintenance-records", MaintenanceRecordViewSet)

urlpatterns = [
    path(
        "operations/transfer-active-vehicles/",
        TransferActiveVehiclesAPIView.as_view(),
        name="transfer-active-vehicles",
    ),
] + router.urls
