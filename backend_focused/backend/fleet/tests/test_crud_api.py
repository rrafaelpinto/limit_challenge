from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from fleet.models import (
    MaintenanceRecord,
    MaintenanceType,
    Mechanic,
    Office,
    Vehicle,
    VehicleMake,
    VehicleModel,
)

RESOURCE_URLS = (
    "/api/v1/offices/",
    "/api/v1/vehicle-makes/",
    "/api/v1/vehicle-models/",
    "/api/v1/vehicles/",
    "/api/v1/mechanics/",
    "/api/v1/maintenance-types/",
    "/api/v1/maintenance-records/",
)


class FleetApiTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="fleet-admin",
            password="a-secure-test-password",
        )
        cls.office = Office.objects.create(name="Calgary", city="Calgary")
        cls.other_office = Office.objects.create(name="Edmonton", city="Edmonton")
        cls.mechanic = Mechanic.objects.create(
            name="Alex Rivera",
            certification_number="CERT-001",
        )
        cls.maintenance_type = MaintenanceType.objects.create(name="Oil Change")
        cls.ford = VehicleMake.objects.create(name="Ford")
        cls.transit = VehicleModel.objects.create(make=cls.ford, name="Transit")

    def setUp(self):
        self.client.force_authenticate(self.user)

    def create_vehicle(self, *, vin, license_plate, office=None, active=True, model=None):
        return Vehicle.objects.create(
            vin=vin,
            license_plate=license_plate,
            model=model or self.transit,
            year=2022,
            office=office or self.office,
            active=active,
        )

    def create_record(self, *, vehicle, mechanic=None, maintenance_type=None):
        return MaintenanceRecord.objects.create(
            vehicle=vehicle,
            mechanic=mechanic or self.mechanic,
            type=maintenance_type or self.maintenance_type,
            performed_on=date(2026, 1, 15),
            cost=Decimal("189.50"),
            notes="Routine service",
        )


class AuthenticationTests(APITestCase):
    username = "fleet-admin"
    password = "a-secure-test-password"

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_user(
            username=cls.username,
            password=cls.password,
        )

    def test_business_resources_require_authentication(self):
        for url in RESOURCE_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_jwt_access_token_grants_access_to_business_resources(self):
        token_response = self.client.post(
            "/api/v1/auth/token/",
            {"username": self.username, "password": self.password},
            format="json",
        )
        self.assertEqual(token_response.status_code, status.HTTP_200_OK)

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token_response.data['access']}"
        )
        response = self.client.post(
            "/api/v1/offices/",
            {"name": "Toronto", "city": "Toronto"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Office.objects.filter(name="Toronto").exists())


class CrudSchemaTests(APITestCase):
    def get_schema(self):
        response = self.client.get(
            "/api/v1/schema/",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.json()

    def test_schema_contains_crud_paths_protected_by_jwt(self):
        paths = self.get_schema()["paths"]

        for url in RESOURCE_URLS:
            with self.subTest(url=url):
                self.assertEqual(set(paths[url]), {"get", "post"})
                self.assertEqual(
                    set(paths[f"{url}{{id}}/"]),
                    {"get", "put", "patch", "delete"},
                )
                for operations in (paths[url], paths[f"{url}{{id}}/"]):
                    for operation in operations.values():
                        self.assertEqual(operation["security"], [{"jwtAuth": []}])

    def test_schema_documents_conflict_for_protected_deletes(self):
        paths = self.get_schema()["paths"]

        for url in (
            "/api/v1/offices/{id}/",
            "/api/v1/vehicle-makes/{id}/",
            "/api/v1/vehicle-models/{id}/",
            "/api/v1/mechanics/{id}/",
            "/api/v1/maintenance-types/{id}/",
        ):
            with self.subTest(url=url):
                self.assertIn("409", paths[url]["delete"]["responses"])

    def test_schema_documents_transfer_active_vehicles_operation(self):
        schema = self.get_schema()
        operation = schema["paths"][
            "/api/v1/operations/transfer-active-vehicles/"
        ]["post"]

        self.assertEqual(operation["security"], [{"jwtAuth": []}])
        self.assertIn("200", operation["responses"])
        self.assertIn("400", operation["responses"])

        def resolve_component(schema_fragment):
            while "$ref" in schema_fragment:
                component_name = schema_fragment["$ref"].rsplit("/", 1)[-1]
                schema_fragment = schema["components"]["schemas"][component_name]
            return schema_fragment

        request_schema = resolve_component(
            operation["requestBody"]["content"]["application/json"]["schema"]
        )
        response_schema = resolve_component(
            operation["responses"]["200"]["content"]["application/json"]["schema"]
        )

        self.assertTrue(
            {"source_office_id", "target_office_id"}.issubset(
                request_schema["properties"]
            )
        )
        self.assertTrue(
            {
                "source_office_id",
                "target_office_id",
                "moved_vehicle_count",
            }.issubset(response_schema["properties"])
        )

    def test_schema_separates_nested_reads_from_id_writes(self):
        schemas = self.get_schema()["components"]["schemas"]

        self.assertIn("office", schemas["Vehicle"]["properties"])
        self.assertNotIn("office_id", schemas["Vehicle"]["properties"])
        self.assertIn("office_id", schemas["VehicleRequest"]["properties"])
        self.assertNotIn("office", schemas["VehicleRequest"]["properties"])
        self.assertEqual(
            set(schemas["Vehicle"]["properties"]),
            {"id", "vin", "license_plate", "make", "model", "year", "active", "office"},
        )
        self.assertEqual(
            set(schemas["VehicleRequest"]["properties"]),
            {"vin", "license_plate", "model_id", "year", "active", "office_id"},
        )
        self.assertIn("model_id", schemas["VehicleRequest"]["required"])
        self.assertEqual(
            schemas["VehicleRequest"]["properties"]["model_id"]["type"],
            "integer",
        )
        self.assertEqual(set(schemas["VehicleMake"]["properties"]), {"id", "name"})
        self.assertEqual(
            set(schemas["VehicleModel"]["properties"]),
            {"id", "name", "make"},
        )
        self.assertEqual(
            set(schemas["VehicleModelRequest"]["properties"]),
            {"name", "make_id"},
        )
        self.assertEqual(
            set(schemas["MaintenanceRecordRequest"]["required"]),
            {"vehicle_id", "mechanic_id", "type_id", "performed_on", "cost"},
        )


class OfficeApiTests(FleetApiTestCase):
    def test_list_offices_is_ordered_by_name(self):
        response = self.client.get("/api/v1/offices/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            [office["name"] for office in response.data["results"]],
            ["Calgary", "Edmonton"],
        )

    def test_retrieve_office(self):
        response = self.client.get(f"/api/v1/offices/{self.office.pk}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {"id": self.office.pk, "name": "Calgary", "city": "Calgary"},
        )

    def test_create_office(self):
        response = self.client.post(
            "/api/v1/offices/",
            {"name": "Vancouver", "city": "Vancouver"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Vancouver")
        self.assertTrue(Office.objects.filter(pk=response.data["id"]).exists())

    def test_update_office(self):
        response = self.client.put(
            f"/api/v1/offices/{self.office.pk}/",
            {"name": "Calgary North", "city": "Calgary"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.office.refresh_from_db()
        self.assertEqual(self.office.name, "Calgary North")

    def test_delete_unreferenced_office(self):
        response = self.client.delete(f"/api/v1/offices/{self.other_office.pk}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Office.objects.filter(pk=self.other_office.pk).exists())

    def test_delete_office_referenced_by_vehicle_returns_conflict(self):
        self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        response = self.client.delete(f"/api/v1/offices/{self.office.pk}/")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data,
            {
                "detail": "This object cannot be deleted because other records reference it."
            },
        )
        self.assertTrue(Office.objects.filter(pk=self.office.pk).exists())


class VehicleApiTests(FleetApiTestCase):
    def vehicle_payload(self, **overrides):
        payload = {
            "vin": "1FTBR1C80NKA09999",
            "license_plate": "ZZ-9999",
            "model_id": self.transit.pk,
            "year": 2023,
            "active": True,
            "office_id": self.office.pk,
        }
        payload.update(overrides)
        return payload

    def test_list_vehicles_includes_nested_office(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        response = self.client.get("/api/v1/vehicles/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], vehicle.pk)
        self.assertEqual(
            response.data["results"][0]["office"],
            {"id": self.office.pk, "name": "Calgary", "city": "Calgary"},
        )

    def test_retrieve_vehicle(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        response = self.client.get(f"/api/v1/vehicles/{vehicle.pk}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "id": vehicle.pk,
                "vin": "1FTBR1C80NKA00001",
                "license_plate": "AB-1001",
                "make": {"id": self.ford.pk, "name": "Ford"},
                "model": {"id": self.transit.pk, "name": "Transit"},
                "year": 2022,
                "active": True,
                "office": {"id": self.office.pk, "name": "Calgary", "city": "Calgary"},
                "maintenance_records": [],
            },
        )

    def test_create_vehicle_with_office_id_and_model_id(self):
        response = self.client.post(
            "/api/v1/vehicles/",
            self.vehicle_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["office"]["id"], self.office.pk)
        self.assertEqual(response.data["make"], {"id": self.ford.pk, "name": "Ford"})
        self.assertEqual(response.data["model"], {"id": self.transit.pk, "name": "Transit"})
        self.assertNotIn("office_id", response.data)
        self.assertNotIn("model_id", response.data)
        vehicle = Vehicle.objects.get(pk=response.data["id"])
        self.assertEqual(vehicle.office, self.office)
        self.assertEqual(vehicle.model, self.transit)

    def test_create_vehicle_does_not_accept_make_or_model_names(self):
        payload = self.vehicle_payload(make="Ford", model="Transit")
        del payload["model_id"]

        response = self.client.post("/api/v1/vehicles/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {
                "model_id": ["This field is required."],
                "make": ["Make is read-only and is derived from model_id."],
                "model": ["Use model_id to set the vehicle model."],
            },
        )
        self.assertFalse(Vehicle.objects.exists())

    def test_make_id_is_rejected_because_the_model_decides_the_make(self):
        ram = VehicleMake.objects.create(name="Ram")

        response = self.client.post(
            "/api/v1/vehicles/",
            self.vehicle_payload(make_id=ram.pk),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {"make_id": ["Make is derived from model_id and cannot be set directly."]},
        )
        self.assertFalse(Vehicle.objects.exists())

    def test_put_with_make_or_model_input_is_rejected(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        for extra in ({"make": "Ford"}, {"model": "Transit"}, {"make_id": self.ford.pk}):
            with self.subTest(extra=extra):
                response = self.client.put(
                    f"/api/v1/vehicles/{vehicle.pk}/",
                    self.vehicle_payload(vin=vehicle.vin, license_plate="AB-2002", **extra),
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(set(response.data), set(extra))

        vehicle.refresh_from_db()
        self.assertEqual(vehicle.license_plate, "AB-1001")

    def test_invalid_model_id_is_rejected(self):
        for model_id in (999999, "abc", None):
            with self.subTest(model_id=model_id):
                response = self.client.post(
                    "/api/v1/vehicles/",
                    self.vehicle_payload(model_id=model_id),
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("model_id", response.data)

    def test_put_vehicle(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        response = self.client.put(
            f"/api/v1/vehicles/{vehicle.pk}/",
            self.vehicle_payload(
                vin=vehicle.vin,
                license_plate="AB-2002",
                office_id=self.other_office.pk,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.license_plate, "AB-2002")
        self.assertEqual(vehicle.office, self.other_office)

    def test_put_vehicle_changes_model_and_derived_make(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")
        ram = VehicleMake.objects.create(name="Ram")
        promaster = VehicleModel.objects.create(make=ram, name="ProMaster")

        response = self.client.put(
            f"/api/v1/vehicles/{vehicle.pk}/",
            self.vehicle_payload(vin=vehicle.vin, model_id=promaster.pk),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["make"], {"id": ram.pk, "name": "Ram"})
        self.assertEqual(response.data["model"], {"id": promaster.pk, "name": "ProMaster"})
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.model, promaster)
        self.assertEqual(vehicle.model.make, ram)

    def test_patch_vehicle_model_id(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")
        f150 = VehicleModel.objects.create(make=self.ford, name="F-150")

        response = self.client.patch(
            f"/api/v1/vehicles/{vehicle.pk}/",
            {"model_id": f150.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["model"], {"id": f150.pk, "name": "F-150"})
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.model, f150)

    def test_patch_with_make_or_model_names_is_rejected_and_changes_nothing(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")
        f150 = VehicleModel.objects.create(make=self.ford, name="F-150")

        response = self.client.patch(
            f"/api/v1/vehicles/{vehicle.pk}/",
            {
                "model": "E-Transit",
                "make": "Ford",
                "model_id": f150.pk,
                "license_plate": "AB-2002",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {
                "make": ["Make is read-only and is derived from model_id."],
                "model": ["Use model_id to set the vehicle model."],
            },
        )
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.model, self.transit)
        self.assertEqual(vehicle.license_plate, "AB-1001")
        self.assertFalse(VehicleModel.objects.filter(name="E-Transit").exists())

    def test_vehicle_can_keep_its_own_plate_on_update(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        put_response = self.client.put(
            f"/api/v1/vehicles/{vehicle.pk}/",
            self.vehicle_payload(vin=vehicle.vin, license_plate="AB-1001"),
            format="json",
        )
        patch_response = self.client.patch(
            f"/api/v1/vehicles/{vehicle.pk}/",
            {"license_plate": "AB-1001", "active": True},
            format="json",
        )

        self.assertEqual(put_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)

    def test_delete_vehicle_cascades_maintenance_records(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")
        record = self.create_record(vehicle=vehicle)

        response = self.client.delete(f"/api/v1/vehicles/{vehicle.pk}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Vehicle.objects.filter(pk=vehicle.pk).exists())
        self.assertFalse(MaintenanceRecord.objects.filter(pk=record.pk).exists())

    def test_duplicate_vin_is_rejected(self):
        self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        response = self.client.post(
            "/api/v1/vehicles/",
            self.vehicle_payload(vin="1FTBR1C80NKA00001"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("vin", response.data)

    def test_active_vehicle_cannot_reuse_active_plate(self):
        self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        response = self.client.post(
            "/api/v1/vehicles/",
            self.vehicle_payload(license_plate="AB-1001"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {
                "license_plate": [
                    "An active vehicle with this license plate already exists."
                ]
            },
        )

    def test_vehicle_without_active_flag_defaults_to_active_for_plate_check(self):
        self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")
        payload = self.vehicle_payload(license_plate="AB-1001")
        del payload["active"]

        response = self.client.post("/api/v1/vehicles/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("license_plate", response.data)

    def test_inactive_vehicle_can_reuse_active_plate(self):
        self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        response = self.client.post(
            "/api/v1/vehicles/",
            self.vehicle_payload(license_plate="AB-1001", active=False),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["active"])

    def test_patch_changing_plate_to_active_plate_is_rejected(self):
        self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00002", license_plate="AB-2002")

        response = self.client.patch(
            f"/api/v1/vehicles/{vehicle.pk}/",
            {"license_plate": "AB-1001"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("license_plate", response.data)

    def test_patch_activating_vehicle_with_conflicting_plate_is_rejected(self):
        self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")
        inactive_vehicle = self.create_vehicle(
            vin="1FTBR1C80NKA00002",
            license_plate="AB-1001",
            active=False,
        )

        response = self.client.patch(
            f"/api/v1/vehicles/{inactive_vehicle.pk}/",
            {"active": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("license_plate", response.data)
        inactive_vehicle.refresh_from_db()
        self.assertFalse(inactive_vehicle.active)

    def test_invalid_office_id_is_rejected(self):
        response = self.client.post(
            "/api/v1/vehicles/",
            self.vehicle_payload(office_id=999999),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("office_id", response.data)


class MechanicApiTests(FleetApiTestCase):
    def test_list_and_retrieve_mechanics(self):
        list_response = self.client.get("/api/v1/mechanics/")
        detail_response = self.client.get(f"/api/v1/mechanics/{self.mechanic.pk}/")

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data["count"], 1)
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            detail_response.data,
            {
                "id": self.mechanic.pk,
                "name": "Alex Rivera",
                "certification_number": "CERT-001",
                "active": True,
            },
        )

    def test_create_update_and_delete_mechanic(self):
        create_response = self.client.post(
            "/api/v1/mechanics/",
            {"name": "Sam Lee", "certification_number": "CERT-002"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        mechanic_id = create_response.data["id"]

        update_response = self.client.patch(
            f"/api/v1/mechanics/{mechanic_id}/",
            {"active": False},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertFalse(Mechanic.objects.get(pk=mechanic_id).active)

        delete_response = self.client.delete(f"/api/v1/mechanics/{mechanic_id}/")
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Mechanic.objects.filter(pk=mechanic_id).exists())

    def test_duplicate_certification_number_is_rejected(self):
        other = Mechanic.objects.create(name="Sam Lee", certification_number="CERT-002")
        requests = (
            (
                "post",
                "/api/v1/mechanics/",
                {"name": "Kim Park", "certification_number": "CERT-001"},
            ),
            (
                "patch",
                f"/api/v1/mechanics/{other.pk}/",
                {"certification_number": "CERT-001"},
            ),
        )

        for method, url, payload in requests:
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, payload, format="json")

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    response.data,
                    {
                        "certification_number": [
                            "mechanic with this certification number already exists."
                        ]
                    },
                )

        other.refresh_from_db()
        self.assertEqual(other.certification_number, "CERT-002")
        self.assertEqual(Mechanic.objects.count(), 2)

    def test_mechanic_can_keep_its_own_certification_number_on_update(self):
        response = self.client.put(
            f"/api/v1/mechanics/{self.mechanic.pk}/",
            {"name": "Alex R.", "certification_number": "CERT-001", "active": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_mechanic_referenced_by_record_returns_conflict(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")
        self.create_record(vehicle=vehicle)

        response = self.client.delete(f"/api/v1/mechanics/{self.mechanic.pk}/")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("detail", response.data)
        self.assertTrue(Mechanic.objects.filter(pk=self.mechanic.pk).exists())


class MaintenanceTypeApiTests(FleetApiTestCase):
    def test_list_and_retrieve_maintenance_types(self):
        list_response = self.client.get("/api/v1/maintenance-types/")
        detail_response = self.client.get(
            f"/api/v1/maintenance-types/{self.maintenance_type.pk}/"
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data["count"], 1)
        self.assertEqual(
            detail_response.data,
            {"id": self.maintenance_type.pk, "name": "Oil Change"},
        )

    def test_create_update_and_delete_maintenance_type(self):
        create_response = self.client.post(
            "/api/v1/maintenance-types/",
            {"name": "Brake Inspection"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        type_id = create_response.data["id"]

        update_response = self.client.put(
            f"/api/v1/maintenance-types/{type_id}/",
            {"name": "Brake Service"},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(MaintenanceType.objects.get(pk=type_id).name, "Brake Service")

        delete_response = self.client.delete(f"/api/v1/maintenance-types/{type_id}/")
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(MaintenanceType.objects.filter(pk=type_id).exists())

    def test_duplicate_maintenance_type_name_is_rejected(self):
        response = self.client.post(
            "/api/v1/maintenance-types/",
            {"name": "Oil Change"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)

    def test_delete_maintenance_type_referenced_by_record_returns_conflict(self):
        vehicle = self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")
        self.create_record(vehicle=vehicle)

        response = self.client.delete(
            f"/api/v1/maintenance-types/{self.maintenance_type.pk}/"
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("detail", response.data)
        self.assertTrue(
            MaintenanceType.objects.filter(pk=self.maintenance_type.pk).exists()
        )


class VehicleMakeApiTests(FleetApiTestCase):
    def test_list_and_retrieve_vehicle_makes_ordered_by_name(self):
        VehicleMake.objects.create(name="Chevrolet")

        list_response = self.client.get("/api/v1/vehicle-makes/")
        detail_response = self.client.get(f"/api/v1/vehicle-makes/{self.ford.pk}/")

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [make["name"] for make in list_response.data["results"]],
            ["Chevrolet", "Ford"],
        )
        self.assertEqual(detail_response.data, {"id": self.ford.pk, "name": "Ford"})

    def test_create_update_and_delete_vehicle_make(self):
        create_response = self.client.post(
            "/api/v1/vehicle-makes/",
            {"name": "Ram"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        make_id = create_response.data["id"]

        update_response = self.client.put(
            f"/api/v1/vehicle-makes/{make_id}/",
            {"name": "RAM"},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(VehicleMake.objects.get(pk=make_id).name, "RAM")

        delete_response = self.client.delete(f"/api/v1/vehicle-makes/{make_id}/")
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(VehicleMake.objects.filter(pk=make_id).exists())

    def test_duplicate_vehicle_make_name_is_rejected(self):
        response = self.client.post(
            "/api/v1/vehicle-makes/",
            {"name": "Ford"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)

    def test_delete_make_referenced_by_model_returns_conflict(self):
        response = self.client.delete(f"/api/v1/vehicle-makes/{self.ford.pk}/")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("detail", response.data)
        self.assertTrue(VehicleMake.objects.filter(pk=self.ford.pk).exists())


class VehicleModelApiTests(FleetApiTestCase):
    def test_list_and_retrieve_models_with_nested_make(self):
        chevrolet = VehicleMake.objects.create(name="Chevrolet")
        express = VehicleModel.objects.create(make=chevrolet, name="Express")
        f150 = VehicleModel.objects.create(make=self.ford, name="F-150")

        list_response = self.client.get("/api/v1/vehicle-models/")
        detail_response = self.client.get(f"/api/v1/vehicle-models/{self.transit.pk}/")

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [model["id"] for model in list_response.data["results"]],
            [express.pk, f150.pk, self.transit.pk],
        )
        self.assertEqual(
            detail_response.data,
            {
                "id": self.transit.pk,
                "name": "Transit",
                "make": {"id": self.ford.pk, "name": "Ford"},
            },
        )

    def test_create_update_and_delete_vehicle_model_with_make_id(self):
        ram = VehicleMake.objects.create(name="Ram")
        create_response = self.client.post(
            "/api/v1/vehicle-models/",
            {"name": "ProMaster", "make_id": ram.pk},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.data["make"], {"id": ram.pk, "name": "Ram"})
        self.assertNotIn("make_id", create_response.data)
        model_id = create_response.data["id"]

        update_response = self.client.patch(
            f"/api/v1/vehicle-models/{model_id}/",
            {"make_id": self.ford.pk},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(VehicleModel.objects.get(pk=model_id).make, self.ford)

        delete_response = self.client.delete(f"/api/v1/vehicle-models/{model_id}/")
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(VehicleModel.objects.filter(pk=model_id).exists())

    def test_model_name_must_be_unique_within_its_make(self):
        response = self.client.post(
            "/api/v1/vehicle-models/",
            {"name": "Transit", "make_id": self.ford.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(VehicleModel.objects.filter(name="Transit").count(), 1)

    def test_same_model_name_is_accepted_under_another_make(self):
        ram = VehicleMake.objects.create(name="Ram")

        response = self.client.post(
            "/api/v1/vehicle-models/",
            {"name": "Transit", "make_id": ram.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_invalid_make_id_is_rejected(self):
        for make_id in (999999, None):
            with self.subTest(make_id=make_id):
                response = self.client.post(
                    "/api/v1/vehicle-models/",
                    {"name": "Van", "make_id": make_id},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("make_id", response.data)

    def test_delete_model_referenced_by_vehicle_returns_conflict(self):
        self.create_vehicle(vin="1FTBR1C80NKA00001", license_plate="AB-1001")

        response = self.client.delete(f"/api/v1/vehicle-models/{self.transit.pk}/")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(VehicleModel.objects.filter(pk=self.transit.pk).exists())


class MaintenanceRecordApiTests(FleetApiTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.vehicle = Vehicle.objects.create(
            vin="1FTBR1C80NKA00001",
            license_plate="AB-1001",
            model=cls.transit,
            year=2022,
            office=cls.office,
        )
        cls.other_mechanic = Mechanic.objects.create(
            name="Sam Lee",
            certification_number="CERT-002",
        )
        cls.other_type = MaintenanceType.objects.create(name="Tire Rotation")

    def record_payload(self, **overrides):
        payload = {
            "vehicle_id": self.vehicle.pk,
            "mechanic_id": self.mechanic.pk,
            "type_id": self.maintenance_type.pk,
            "performed_on": "2026-02-10",
            "cost": "189.50",
            "notes": "Replaced oil filter",
        }
        payload.update(overrides)
        return payload

    def test_retrieve_record_represents_related_objects(self):
        record = self.create_record(vehicle=self.vehicle)

        response = self.client.get(f"/api/v1/maintenance-records/{record.pk}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "id": record.pk,
                "vehicle": {
                    "id": self.vehicle.pk,
                    "vin": "1FTBR1C80NKA00001",
                    "license_plate": "AB-1001",
                    "make": {"id": self.ford.pk, "name": "Ford"},
                    "model": {"id": self.transit.pk, "name": "Transit"},
                },
                "mechanic": {
                    "id": self.mechanic.pk,
                    "name": "Alex Rivera",
                    "certification_number": "CERT-001",
                    "active": True,
                },
                "type": {"id": self.maintenance_type.pk, "name": "Oil Change"},
                "performed_on": "2026-01-15",
                "cost": "189.50",
                "notes": "Routine service",
            },
        )

    def test_list_records(self):
        self.create_record(vehicle=self.vehicle)

        response = self.client.get("/api/v1/maintenance-records/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

    def test_create_record_with_id_fields(self):
        response = self.client.post(
            "/api/v1/maintenance-records/",
            self.record_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["vehicle"]["id"], self.vehicle.pk)
        self.assertEqual(response.data["mechanic"]["id"], self.mechanic.pk)
        self.assertEqual(response.data["type"]["id"], self.maintenance_type.pk)
        self.assertEqual(response.data["cost"], "189.50")
        for field in ("vehicle_id", "mechanic_id", "type_id"):
            self.assertNotIn(field, response.data)

    def test_update_record_relationships_with_id_fields(self):
        record = self.create_record(vehicle=self.vehicle)

        put_response = self.client.put(
            f"/api/v1/maintenance-records/{record.pk}/",
            self.record_payload(mechanic_id=self.other_mechanic.pk),
            format="json",
        )
        patch_response = self.client.patch(
            f"/api/v1/maintenance-records/{record.pk}/",
            {"type_id": self.other_type.pk},
            format="json",
        )

        self.assertEqual(put_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        record.refresh_from_db()
        self.assertEqual(record.mechanic, self.other_mechanic)
        self.assertEqual(record.type, self.other_type)
        self.assertEqual(record.performed_on, date(2026, 2, 10))

    def test_delete_record(self):
        record = self.create_record(vehicle=self.vehicle)

        response = self.client.delete(f"/api/v1/maintenance-records/{record.pk}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(MaintenanceRecord.objects.filter(pk=record.pk).exists())

    def test_invalid_foreign_keys_are_rejected(self):
        for field in ("vehicle_id", "mechanic_id", "type_id"):
            with self.subTest(field=field):
                response = self.client.post(
                    "/api/v1/maintenance-records/",
                    self.record_payload(**{field: 999999}),
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_negative_cost_is_rejected(self):
        record = self.create_record(vehicle=self.vehicle)
        requests = (
            ("post", "/api/v1/maintenance-records/", self.record_payload(cost="-0.01")),
            ("patch", f"/api/v1/maintenance-records/{record.pk}/", {"cost": "-10.00"}),
        )

        for method, url, payload in requests:
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, payload, format="json")

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["cost"][0].code, "min_value")
                self.assertEqual(
                    str(response.data["cost"][0]),
                    "Ensure this value is greater than or equal to 0.",
                )

        record.refresh_from_db()
        self.assertEqual(record.cost, Decimal("189.50"))

    def test_zero_cost_is_accepted(self):
        response = self.client.post(
            "/api/v1/maintenance-records/",
            self.record_payload(cost="0.00"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["cost"], "0.00")


class ListQueryCountTests(FleetApiTestCase):
    """List endpoints must not issue one extra query per row."""

    def create_varied_vehicles(self):
        ram = VehicleMake.objects.create(name="Ram")
        models = [
            self.transit,
            VehicleModel.objects.create(make=self.ford, name="F-150"),
            VehicleModel.objects.create(make=ram, name="ProMaster"),
        ]
        return [
            self.create_vehicle(
                vin=f"1FTBR1C80NKA0000{index}",
                license_plate=f"AB-100{index}",
                office=self.office if index % 2 else self.other_office,
                model=models[index % len(models)],
            )
            for index in range(6)
        ]

    def test_vehicle_list_query_count_does_not_grow_with_rows(self):
        self.create_varied_vehicles()

        # One COUNT for pagination plus one SELECT joined with offices, models and makes.
        with self.assertNumQueries(2):
            response = self.client.get("/api/v1/vehicles/")

        self.assertEqual(len(response.data["results"]), 6)
        self.assertEqual(
            {row["make"]["name"] for row in response.data["results"]},
            {"Ford", "Ram"},
        )

    def test_vehicle_writes_do_not_query_per_nested_relation(self):
        [vehicle, *_] = self.create_varied_vehicles()
        f150 = VehicleModel.objects.get(name="F-150")

        # Vehicle lookup (joined), model_id and office_id validation (model joined with make),
        # active-plate check, UPDATE. The response reuses the loaded objects.
        with self.assertNumQueries(5):
            response = self.client.patch(
                f"/api/v1/vehicles/{vehicle.pk}/",
                {"model_id": f150.pk, "office_id": self.office.pk},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["make"]["name"], "Ford")

    def test_vehicle_model_list_query_count_does_not_grow_with_rows(self):
        self.create_varied_vehicles()

        # One COUNT for pagination plus one SELECT joined with makes.
        with self.assertNumQueries(2):
            response = self.client.get("/api/v1/vehicle-models/")

        self.assertEqual(len(response.data["results"]), 3)

    def test_maintenance_record_list_query_count_does_not_grow_with_rows(self):
        other_mechanic = Mechanic.objects.create(
            name="Sam Lee",
            certification_number="CERT-002",
        )
        other_type = MaintenanceType.objects.create(name="Tire Rotation")
        for index, vehicle in enumerate(self.create_varied_vehicles()[:5]):
            self.create_record(
                vehicle=vehicle,
                mechanic=other_mechanic if index % 2 else self.mechanic,
                maintenance_type=other_type if index % 2 else self.maintenance_type,
            )

        # One COUNT for pagination plus one SELECT joined with vehicle, model, make, mechanic
        # and type.
        with self.assertNumQueries(2):
            response = self.client.get("/api/v1/maintenance-records/")

        self.assertEqual(len(response.data["results"]), 5)
        self.assertEqual(
            {row["vehicle"]["make"]["name"] for row in response.data["results"]},
            {"Ford", "Ram"},
        )
