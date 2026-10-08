from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from fleet.models import (
    MaintenanceRecord,
    Mechanic,
    Office,
    Vehicle,
    VehicleMake,
    VehicleModel,
)
from fleet.tests.test_crud_api import FleetApiTestCase


class AdvancedApiTestCase(FleetApiTestCase):
    def make_vehicle(self, license_plate, **fields):
        values = {
            "vin": f"VIN-{license_plate}",
            "license_plate": license_plate,
            "model": self.transit,
            "year": 2022,
            "office": self.office,
            "active": True,
        }
        values.update(fields)
        return Vehicle.objects.create(**values)

    def add_record(self, vehicle, performed_on, *, cost="100.00", mechanic=None):
        return MaintenanceRecord.objects.create(
            vehicle=vehicle,
            mechanic=mechanic or self.mechanic,
            type=self.maintenance_type,
            performed_on=performed_on,
            cost=Decimal(cost),
        )


class OfficeSummaryApiTests(AdvancedApiTestCase):
    url = "/api/v1/offices/summary/"

    def test_returns_every_office_as_plain_list(self):
        performed_on = timezone.localdate() - timedelta(days=10)
        vehicle = self.make_vehicle("AB-1001")
        self.add_record(vehicle, performed_on, cost="150.25")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            [
                {
                    "id": self.office.pk,
                    "name": "Calgary",
                    "city": "Calgary",
                    "active_vehicle_count": 1,
                    "maintenance_cost_last_year": "150.25",
                    "last_maintenance": performed_on.isoformat(),
                },
                {
                    "id": self.other_office.pk,
                    "name": "Edmonton",
                    "city": "Edmonton",
                    "active_vehicle_count": 0,
                    "maintenance_cost_last_year": "0.00",
                    "last_maintenance": None,
                },
            ],
        )

    def test_uses_one_query_for_any_number_of_offices(self):
        for index in range(3):
            office = Office.objects.create(name=f"Office {index}", city="City")
            vehicle = self.make_vehicle(f"AB-100{index}", office=office)
            self.add_record(vehicle, timezone.localdate())

        with self.assertNumQueries(1):
            response = self.client.get(self.url)

        self.assertEqual(len(response.data), 5)


class MechanicWorkloadApiTests(AdvancedApiTestCase):
    url = "/api/v1/mechanics/workload/"

    def test_returns_plain_list_with_current_year_totals(self):
        vehicle = self.make_vehicle("AB-1001")
        self.add_record(vehicle, timezone.localdate(), cost="80.00")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            [
                {
                    "id": self.mechanic.pk,
                    "name": "Alex Rivera",
                    "certification_number": "CERT-001",
                    "maintenance_count": 1,
                    "total_cost": "80.00",
                }
            ],
        )

    def test_uses_one_query_for_any_number_of_mechanics(self):
        vehicle = self.make_vehicle("AB-1001")
        for index in range(3):
            mechanic = Mechanic.objects.create(
                name=f"Mechanic {index}",
                certification_number=f"CERT-10{index}",
            )
            self.add_record(vehicle, timezone.localdate(), mechanic=mechanic)

        with self.assertNumQueries(1):
            response = self.client.get(self.url)

        self.assertEqual(len(response.data), 4)


class VehicleSearchApiTests(AdvancedApiTestCase):
    url = "/api/v1/vehicles/"

    def result_ids(self, response):
        return [row["id"] for row in response.data["results"]]

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.f150 = VehicleModel.objects.create(make=cls.ford, name="F-150")
        cls.ram = VehicleMake.objects.create(name="Ram")
        cls.promaster = VehicleModel.objects.create(make=cls.ram, name="ProMaster")

    def test_combines_all_filters(self):
        other_mechanic = Mechanic.objects.create(name="Sam Lee", certification_number="CERT-002")
        match = self.make_vehicle("AB-1001")
        inactive = self.make_vehicle("AB-1002", active=False)
        other_office = self.make_vehicle("AB-1003", office=self.other_office)
        other_make = self.make_vehicle("AB-1004", model=self.promaster)
        other_model = self.make_vehicle("AB-1006", model=self.f150)
        wrong_mechanic = self.make_vehicle("AB-1005")
        for vehicle in (match, inactive, other_office, other_make, other_model):
            self.add_record(vehicle, date(2026, 4, 10))
        self.add_record(wrong_mechanic, date(2026, 4, 10), mechanic=other_mechanic)

        response = self.client.get(
            self.url,
            {
                "office": self.office.pk,
                "active": "true",
                "make": self.ford.pk,
                "model": self.transit.pk,
                "maintained_from": "2026-04-01",
                "maintained_to": "2026-04-30",
                "mechanic_certification": "CERT-001",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(self.result_ids(response), [match.pk])

    def test_make_id_matches_every_model_of_that_make(self):
        transit = self.make_vehicle("AB-1001")
        f150 = self.make_vehicle("AB-1002", model=self.f150)
        self.make_vehicle("AB-1003", model=self.promaster)

        response = self.client.get(self.url, {"make": self.ford.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.result_ids(response), [transit.pk, f150.pk])

    def test_model_id_can_be_sent_without_make(self):
        self.make_vehicle("AB-1001")
        f150 = self.make_vehicle("AB-1002", model=self.f150)

        response = self.client.get(self.url, {"model": self.f150.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.result_ids(response), [f150.pk])

    def test_compatible_make_and_model_ids_are_combined(self):
        transit = self.make_vehicle("AB-1001")
        self.make_vehicle("AB-1002", model=self.f150)

        response = self.client.get(self.url, {"make": self.ford.pk, "model": self.transit.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.result_ids(response), [transit.pk])

    def test_model_from_another_make_returns_400(self):
        self.make_vehicle("AB-1001")

        response = self.client.get(self.url, {"make": self.ram.pk, "model": self.transit.pk})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {
                "model": [
                    f"Model {self.transit.pk} does not belong to make {self.ram.pk}."
                ]
            },
        )

    def test_make_and_model_names_are_not_accepted(self):
        self.make_vehicle("AB-1001")

        for params, field in (({"make": "Ford"}, "make"), ({"model": "Transit"}, "model")):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_active_false_returns_only_inactive_vehicles(self):
        self.make_vehicle("AB-1001")
        inactive = self.make_vehicle("AB-1002", active=False)

        response = self.client.get(self.url, {"active": "false"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.result_ids(response), [inactive.pk])

    def test_empty_values_are_treated_as_not_sent(self):
        vehicle = self.make_vehicle("AB-1001")

        response = self.client.get(
            self.url,
            {"make": "", "model": "", "office": "", "active": "", "maintained_from": ""},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.result_ids(response), [vehicle.pk])

    def test_invalid_parameters_return_400_with_field_errors(self):
        cases = (
            ({"active": "maybe"}, "active"),
            ({"office": "abc"}, "office"),
            ({"office": 999999}, "office"),
            ({"make": 999999}, "make"),
            ({"model": 999999}, "model"),
            ({"make": "1.5"}, "make"),
            ({"maintained_from": "2026-13-01"}, "maintained_from"),
            ({"maintained_to": "yesterday"}, "maintained_to"),
            ({"maintained_from": "2026-05-01", "maintained_to": "2026-04-01"}, "maintained_to"),
        )
        for params, field in cases:
            with self.subTest(params=params):
                response = self.client.get(self.url, params)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_filters_work_with_pagination(self):
        for index in range(12):
            self.make_vehicle(f"AB-{1000 + index}")
        self.make_vehicle("ZZ-0001", model=self.promaster)

        response = self.client.get(self.url, {"make": self.ford.pk, "page": 2})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 12)
        self.assertEqual(len(response.data["results"]), 2)

    def test_query_count_does_not_grow_with_rows(self):
        for index in range(6):
            model = (self.transit, self.f150)[index % 2]
            vehicle = self.make_vehicle(f"AB-100{index}", model=model)
            self.add_record(vehicle, date(2026, 4, 10))
            self.add_record(vehicle, date(2026, 4, 11))
        params = {
            "maintained_from": "2026-04-01",
            "mechanic_certification": "CERT-001",
        }

        # COUNT + page SELECT joined with offices, models and makes.
        with self.assertNumQueries(2):
            response = self.client.get(self.url, params)
        self.assertEqual(len(response.data["results"]), 6)

        # One more query validates each id parameter.
        with self.assertNumQueries(3):
            response = self.client.get(self.url, {**params, "make": self.ford.pk})
        self.assertEqual(len(response.data["results"]), 6)

        with self.assertNumQueries(5):
            response = self.client.get(
                self.url,
                {
                    **params,
                    "office": self.office.pk,
                    "make": self.ford.pk,
                    "model": self.f150.pk,
                },
            )
        self.assertEqual(len(response.data["results"]), 3)


def bulk_records(vehicle, mechanics, maintenance_type, total):
    MaintenanceRecord.objects.bulk_create(
        MaintenanceRecord(
            vehicle=vehicle,
            mechanic=mechanics[index % len(mechanics)],
            type=maintenance_type,
            performed_on=date(2025, 1, 1) + timedelta(days=index),
            cost=Decimal("10.00"),
        )
        for index in range(total)
    )


class VehicleDetailApiTests(AdvancedApiTestCase):
    def url(self, vehicle_id):
        return f"/api/v1/vehicles/{vehicle_id}/"

    def test_retrieve_includes_office_and_history_newest_first(self):
        vehicle = self.make_vehicle("AB-1001")
        older = self.add_record(vehicle, date(2026, 1, 10), cost="50.00")
        same_day_first = self.add_record(vehicle, date(2026, 2, 10))
        same_day_second = self.add_record(vehicle, date(2026, 2, 10))

        response = self.client.get(self.url(vehicle.pk))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["office"],
            {"id": self.office.pk, "name": "Calgary", "city": "Calgary"},
        )
        self.assertEqual(response.data["make"], {"id": self.ford.pk, "name": "Ford"})
        self.assertEqual(response.data["model"], {"id": self.transit.pk, "name": "Transit"})
        records = response.data["maintenance_records"]
        self.assertEqual(
            [record["id"] for record in records],
            [same_day_second.pk, same_day_first.pk, older.pk],
        )
        self.assertEqual(
            records[-1],
            {
                "id": older.pk,
                "performed_on": "2026-01-10",
                "type": {"id": self.maintenance_type.pk, "name": "Oil Change"},
                "mechanic": {
                    "id": self.mechanic.pk,
                    "name": "Alex Rivera",
                    "certification_number": "CERT-001",
                    "active": True,
                },
                "cost": "50.00",
                "notes": "",
            },
        )

    def test_retrieve_uses_two_queries_with_hundreds_of_records(self):
        vehicle = self.make_vehicle("AB-1001")
        other_mechanic = Mechanic.objects.create(name="Sam Lee", certification_number="CERT-002")
        bulk_records(vehicle, [self.mechanic, other_mechanic], self.maintenance_type, 300)

        # Vehicle joined with office, model and make, then all records joined with mechanic
        # and type.
        with self.assertNumQueries(2):
            response = self.client.get(self.url(vehicle.pk))

        self.assertEqual(len(response.data["maintenance_records"]), 300)
        self.assertEqual(response.data["make"]["name"], "Ford")

    def test_retrieve_query_count_is_constant_as_history_grows(self):
        vehicle = self.make_vehicle("AB-1001")
        mechanics = [
            Mechanic.objects.create(name=f"Mechanic {index}", certification_number=f"C-{index}")
            for index in range(5)
        ]

        for total in (1, 500):
            MaintenanceRecord.objects.filter(vehicle=vehicle).delete()
            bulk_records(vehicle, mechanics, self.maintenance_type, total)
            with self.subTest(records=total), self.assertNumQueries(2):
                response = self.client.get(self.url(vehicle.pk))
            self.assertEqual(len(response.data["maintenance_records"]), total)

    def test_write_responses_keep_crud_shape(self):
        vehicle = self.make_vehicle("AB-1001")
        self.add_record(vehicle, date(2026, 1, 10))

        response = self.client.patch(
            self.url(vehicle.pk),
            {"model_id": self.transit.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("maintenance_records", response.data)

    def test_unknown_vehicle_returns_404(self):
        response = self.client.get(self.url(999999))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class VehicleMaintenanceHistoryApiTests(AdvancedApiTestCase):
    def url(self, vehicle_id):
        return f"/api/v1/vehicles/{vehicle_id}/maintenance-records/"

    def test_returns_only_this_vehicle_newest_first(self):
        vehicle = self.make_vehicle("AB-1001")
        other_vehicle = self.make_vehicle("AB-1002")
        older = self.add_record(vehicle, date(2026, 1, 10))
        newer = self.add_record(vehicle, date(2026, 3, 10))
        self.add_record(other_vehicle, date(2026, 2, 10))

        response = self.client.get(self.url(vehicle.pk))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            [record["id"] for record in response.data["results"]],
            [newer.pk, older.pk],
        )
        self.assertNotIn("vehicle", response.data["results"][0])

    def test_same_day_records_are_newest_id_first(self):
        vehicle = self.make_vehicle("AB-1001")
        first = self.add_record(vehicle, date(2026, 2, 10))
        second = self.add_record(vehicle, date(2026, 2, 10))

        response = self.client.get(self.url(vehicle.pk))

        self.assertEqual(
            [record["id"] for record in response.data["results"]],
            [second.pk, first.pk],
        )

    def test_uses_three_queries_with_hundreds_of_records(self):
        vehicle = self.make_vehicle("AB-1001")
        bulk_records(vehicle, [self.mechanic], self.maintenance_type, 300)

        # Vehicle lookup for 404, COUNT, page SELECT joined with mechanic and type.
        with self.assertNumQueries(3):
            response = self.client.get(self.url(vehicle.pk))

        self.assertEqual(response.data["count"], 300)
        self.assertEqual(len(response.data["results"]), 10)
        self.assertEqual(response.data["results"][0]["performed_on"], "2025-10-27")

    def test_unknown_vehicle_returns_404(self):
        response = self.client.get(self.url(999999))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AssignVehicleOfficeApiTests(AdvancedApiTestCase):
    def url(self, vehicle_id):
        return f"/api/v1/vehicles/{vehicle_id}/office/"

    def test_moves_vehicle_to_new_office(self):
        vehicle = self.make_vehicle("AB-1001")

        # Vehicle lookup joined with office, model and make, office id validation, UPDATE of
        # office_id only.
        with self.assertNumQueries(3):
            response = self.client.put(
                self.url(vehicle.pk),
                {"office_id": self.other_office.pk},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "id": vehicle.pk,
                "vin": "VIN-AB-1001",
                "license_plate": "AB-1001",
                "make": {"id": self.ford.pk, "name": "Ford"},
                "model": {"id": self.transit.pk, "name": "Transit"},
                "year": 2022,
                "active": True,
                "office": {"id": self.other_office.pk, "name": "Edmonton", "city": "Edmonton"},
            },
        )
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.office, self.other_office)

    def test_same_office_is_idempotent(self):
        vehicle = self.make_vehicle("AB-1001")

        for _ in range(2):
            response = self.client.put(
                self.url(vehicle.pk),
                {"office_id": self.office.pk},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        vehicle.refresh_from_db()
        self.assertEqual(vehicle.office, self.office)

    def test_inactive_vehicle_can_be_reassigned(self):
        vehicle = self.make_vehicle("AB-1001", active=False)

        response = self.client.put(
            self.url(vehicle.pk),
            {"office_id": self.other_office.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_invalid_office_returns_400(self):
        vehicle = self.make_vehicle("AB-1001")

        for body in ({}, {"office_id": 999999}, {"office_id": "abc"}, {"office_id": None}):
            with self.subTest(body=body):
                response = self.client.put(self.url(vehicle.pk), body, format="json")

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("office_id", response.data)

    def test_unknown_vehicle_returns_404(self):
        response = self.client.put(self.url(999999), {"office_id": self.office.pk}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_only_put_is_allowed(self):
        vehicle = self.make_vehicle("AB-1001")

        for method in ("get", "patch", "post"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url(vehicle.pk))

                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_summary_follows_vehicle_to_new_office(self):
        vehicle = self.make_vehicle("AB-1001")
        self.add_record(vehicle, timezone.localdate(), cost="70.00")

        self.client.put(self.url(vehicle.pk), {"office_id": self.other_office.pk}, format="json")
        summary = {row["name"]: row for row in self.client.get("/api/v1/offices/summary/").data}

        self.assertEqual(summary["Calgary"]["maintenance_cost_last_year"], "0.00")
        self.assertEqual(summary["Edmonton"]["maintenance_cost_last_year"], "70.00")


class MaintenanceDueApiTests(AdvancedApiTestCase):
    url = "/api/v1/vehicles/maintenance-due/"

    def test_returns_paginated_vehicles_with_last_maintenance(self):
        today = timezone.localdate()
        stale = self.make_vehicle("AB-1001")
        self.add_record(stale, today - timedelta(days=400))
        fresh = self.make_vehicle("AB-1002")
        self.add_record(fresh, today)
        never = self.make_vehicle("AB-1003")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            [row["id"] for row in response.data["results"]],
            [never.pk, stale.pk],
        )
        self.assertIsNone(response.data["results"][0]["last_maintenance"])
        self.assertEqual(
            response.data["results"][1],
            {
                "id": stale.pk,
                "vin": "VIN-AB-1001",
                "license_plate": "AB-1001",
                "make": {"id": self.ford.pk, "name": "Ford"},
                "model": {"id": self.transit.pk, "name": "Transit"},
                "year": 2022,
                "active": True,
                "office": {"id": self.office.pk, "name": "Calgary", "city": "Calgary"},
                "last_maintenance": (today - timedelta(days=400)).isoformat(),
            },
        )

    def test_uses_two_queries(self):
        ram = VehicleMake.objects.create(name="Ram")
        models = (self.transit, VehicleModel.objects.create(make=ram, name="ProMaster"))
        for index in range(6):
            vehicle = self.make_vehicle(f"AB-100{index}", model=models[index % 2])
            self.add_record(vehicle, timezone.localdate() - timedelta(days=500))

        # COUNT + page SELECT with office, model and make joins and last-maintenance subquery.
        with self.assertNumQueries(2):
            response = self.client.get(self.url)

        self.assertEqual(len(response.data["results"]), 6)
        self.assertEqual(
            {row["make"]["name"] for row in response.data["results"]},
            {"Ford", "Ram"},
        )


class DuplicateCheckApiTests(AdvancedApiTestCase):
    url = "/api/v1/vehicles/duplicate-check/"

    def test_reports_conflicting_fields(self):
        vehicle = self.make_vehicle("AB-1001")

        with self.assertNumQueries(2):
            response = self.client.get(
                self.url,
                {"vin": vehicle.vin, "license_plate": "AB-1001"},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"conflicts": ["vin", "license_plate"]})

    def test_no_conflicts_returns_empty_list(self):
        response = self.client.get(self.url, {"vin": "VIN-NEW", "license_plate": "NEW-1"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"conflicts": []})

    def test_surrounding_whitespace_is_trimmed_like_create_and_update(self):
        self.make_vehicle("AB-1001")

        cases = (
            ({"vin": "VIN-NEW", "license_plate": " AB-1001 "}, ["license_plate"]),
            ({"vin": " VIN-AB-1001 ", "license_plate": "NEW-1"}, ["vin"]),
        )
        for params, conflicts in cases:
            with self.subTest(params=params):
                response = self.client.get(self.url, params)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data, {"conflicts": conflicts})

    def test_matching_is_case_sensitive(self):
        self.make_vehicle("AB-1001")

        response = self.client.get(self.url, {"vin": "vin-ab-1001", "license_plate": "ab-1001"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"conflicts": []})

    def test_missing_blank_or_too_long_parameters_return_400(self):
        cases = (
            ({"license_plate": "AB-1001"}, "vin"),
            ({"vin": "VIN-NEW"}, "license_plate"),
            ({"vin": "", "license_plate": "AB-1001"}, "vin"),
            ({"vin": "VIN-NEW", "license_plate": "   "}, "license_plate"),
            ({"vin": "X" * 18, "license_plate": "AB-1001"}, "vin"),
            ({"vin": "VIN-NEW", "license_plate": "X" * 21}, "license_plate"),
        )
        for params, field in cases:
            with self.subTest(params=params):
                response = self.client.get(self.url, params)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)


ADVANCED_ENDPOINTS = (
    ("get", "/api/v1/offices/summary/"),
    ("get", "/api/v1/mechanics/workload/"),
    ("get", "/api/v1/vehicles/maintenance-due/"),
    ("get", "/api/v1/vehicles/duplicate-check/?vin=VIN-NEW&license_plate=NEW-1"),
    ("get", "/api/v1/vehicles/1/maintenance-records/"),
    ("put", "/api/v1/vehicles/1/office/"),
    ("get", "/api/v1/vehicles/?make=1"),
)


class AdvancedAuthenticationTests(APITestCase):
    def test_advanced_endpoints_require_authentication(self):
        for method, url in ADVANCED_ENDPOINTS:
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url)

                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_transfer_active_vehicles_operation_requires_authentication(self):
        response = self.client.post(
            "/api/v1/operations/transfer-active-vehicles/",
            {"source_office_id": 1, "target_office_id": 2},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AdvancedSchemaTests(APITestCase):
    def get_schema(self):
        response = self.client.get("/api/v1/schema/", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.json()

    def test_advanced_paths_are_documented_and_protected(self):
        paths = self.get_schema()["paths"]
        expected = {
            "/api/v1/offices/summary/": {"get"},
            "/api/v1/mechanics/workload/": {"get"},
            "/api/v1/vehicles/maintenance-due/": {"get"},
            "/api/v1/vehicles/duplicate-check/": {"get"},
            "/api/v1/vehicles/{id}/maintenance-records/": {"get"},
            "/api/v1/vehicles/{id}/office/": {"put"},
        }
        for url, methods in expected.items():
            with self.subTest(url=url):
                self.assertEqual(set(paths[url]), methods)
                for operation in paths[url].values():
                    self.assertEqual(operation["security"], [{"jwtAuth": []}])

    def test_vehicle_search_parameters_are_documented(self):
        operation = self.get_schema()["paths"]["/api/v1/vehicles/"]["get"]

        names = {parameter["name"] for parameter in operation["parameters"]}

        self.assertLessEqual(
            {
                "page",
                "office",
                "active",
                "make",
                "model",
                "maintained_from",
                "maintained_to",
                "mechanic_certification",
            },
            names,
        )

    def test_vehicle_search_make_and_model_are_integer_ids(self):
        operation = self.get_schema()["paths"]["/api/v1/vehicles/"]["get"]

        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

        for name in ("office", "make", "model"):
            with self.subTest(name=name):
                self.assertEqual(parameters[name]["schema"]["type"], "integer")
                self.assertFalse(parameters[name].get("required", False))

    def test_duplicate_check_parameters_are_required(self):
        operation = self.get_schema()["paths"]["/api/v1/vehicles/duplicate-check/"]["get"]

        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

        self.assertTrue(parameters["vin"]["required"])
        self.assertTrue(parameters["license_plate"]["required"])

    def test_validating_endpoints_document_400_and_keep_200_schema(self):
        paths = self.get_schema()["paths"]
        cases = (
            (paths["/api/v1/vehicles/"]["get"], "PaginatedVehicleList"),
            (paths["/api/v1/vehicles/duplicate-check/"]["get"], "VehicleConflicts"),
            (paths["/api/v1/vehicles/{id}/office/"]["put"], "Vehicle"),
        )

        for operation, component in cases:
            with self.subTest(operation=operation["operationId"]):
                responses = operation["responses"]
                self.assertIn("400", responses)
                self.assertEqual(
                    responses["200"]["content"]["application/json"]["schema"]["$ref"],
                    f"#/components/schemas/{component}",
                )

    def test_report_responses_are_plain_lists(self):
        paths = self.get_schema()["paths"]

        for url in ("/api/v1/offices/summary/", "/api/v1/mechanics/workload/"):
            with self.subTest(url=url):
                content = paths[url]["get"]["responses"]["200"]["content"]
                self.assertEqual(content["application/json"]["schema"]["type"], "array")

    def test_vehicle_retrieve_and_writes_use_different_components(self):
        schema = self.get_schema()
        operations = schema["paths"]["/api/v1/vehicles/{id}/"]

        def response_ref(method):
            content = operations[method]["responses"]["200"]["content"]
            return content["application/json"]["schema"]["$ref"]

        self.assertEqual(response_ref("get"), "#/components/schemas/VehicleDetail")
        self.assertEqual(response_ref("put"), "#/components/schemas/Vehicle")
        self.assertEqual(response_ref("patch"), "#/components/schemas/Vehicle")
        self.assertIn(
            "maintenance_records",
            schema["components"]["schemas"]["VehicleDetail"]["properties"],
        )
