from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status

from fleet.models import Office, Vehicle, VehicleMake, VehicleModel
from fleet.tests.test_crud_api import FleetApiTestCase


class TransferActiveVehiclesApiTests(FleetApiTestCase):
    url = "/api/v1/operations/transfer-active-vehicles/"

    def transfer_payload(self, **overrides):
        payload = {
            "source_office_id": self.office.pk,
            "target_office_id": self.other_office.pk,
        }
        payload.update(overrides)
        return payload

    def test_transfers_all_active_vehicles_and_keeps_inactive_vehicles(self):
        first_active = self.create_vehicle(
            vin="1FTBR1C80NKA00001",
            license_plate="AB-1001",
        )
        second_active = self.create_vehicle(
            vin="1FTBR1C80NKA00002",
            license_plate="AB-1002",
        )
        inactive = self.create_vehicle(
            vin="1FTBR1C80NKA00003",
            license_plate="AB-1003",
            active=False,
        )

        response = self.client.post(self.url, self.transfer_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "source_office_id": self.office.pk,
                "target_office_id": self.other_office.pk,
                "moved_vehicle_count": 2,
            },
        )
        first_active.refresh_from_db()
        second_active.refresh_from_db()
        inactive.refresh_from_db()
        self.assertEqual(first_active.office_id, self.other_office.pk)
        self.assertEqual(second_active.office_id, self.other_office.pk)
        self.assertEqual(inactive.office_id, self.office.pk)

    def test_rejects_identical_source_and_target_offices(self):
        response = self.client.post(
            self.url,
            self.transfer_payload(target_office_id=self.office.pk),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_an_unknown_source_or_target_office(self):
        for field_name in ("source_office_id", "target_office_id"):
            with self.subTest(field_name=field_name):
                response = self.client.post(
                    self.url,
                    self.transfer_payload(**{field_name: 999999}),
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_returns_zero_when_source_has_no_active_vehicles(self):
        response = self.client.post(self.url, self.transfer_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["moved_vehicle_count"], 0)


class TransferActiveVehiclesServiceTests(TestCase):
    def setUp(self):
        self.source = Office.objects.create(name="Calgary", city="Calgary")
        self.target = Office.objects.create(name="Edmonton", city="Edmonton")
        make = VehicleMake.objects.create(name="Ford")
        self.model = VehicleModel.objects.create(make=make, name="Transit")

    def create_vehicle(self, index, *, active=True):
        return Vehicle.objects.create(
            vin=f"1FTBR1C80NKA00{index:03d}",
            license_plate=f"AB-{index:04d}",
            model=self.model,
            year=2022,
            office=self.source,
            active=active,
        )

    def test_can_be_called_directly(self):
        first_active = self.create_vehicle(1)
        second_active = self.create_vehicle(2)

        from fleet.services import transfer_active_vehicles

        result = transfer_active_vehicles(
            source_office=self.source,
            target_office=self.target,
        )

        self.assertEqual(result.moved_vehicle_count, 2)
        first_active.refresh_from_db()
        second_active.refresh_from_db()
        self.assertEqual(first_active.office_id, self.target.pk)
        self.assertEqual(second_active.office_id, self.target.pk)

    def test_rejects_identical_offices_when_called_directly(self):
        from fleet.services import transfer_active_vehicles

        with self.assertRaises(ValueError):
            transfer_active_vehicles(
                source_office=self.source,
                target_office=self.source,
            )

    def test_updates_many_vehicles_without_per_vehicle_queries(self):
        for index in range(1, 7):
            self.create_vehicle(index)

        from fleet.services import transfer_active_vehicles

        # A lock/select plus a bulk UPDATE keeps query count independent of fleet size.
        with CaptureQueriesContext(connection) as queries:
            result = transfer_active_vehicles(
                source_office=self.source,
                target_office=self.target,
            )

        update_queries = [
            query
            for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith("UPDATE")
        ]
        self.assertLessEqual(len(queries), 4)
        self.assertEqual(len(update_queries), 1)
        self.assertEqual(result.moved_vehicle_count, 6)
        self.assertEqual(
            Vehicle.objects.filter(office=self.target, active=True).count(),
            6,
        )
