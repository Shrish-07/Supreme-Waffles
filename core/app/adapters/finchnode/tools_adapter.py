"""FinchNodeAdapter backed by the ``finchnode`` library (any client: fixtures or HTTP)."""

from claimshield_contracts.models import Claim, HealthRecord, PatientRef
from finchnode import tools
from finchnode.client import FinchNodeClient

from .base import FinchNodeAdapter


class FinchNodeToolsAdapter(FinchNodeAdapter):
    def __init__(self, client: FinchNodeClient, mode: str) -> None:
        self._client = client
        self.mode = mode

    def get_patient_profile(self, patient_id: str) -> PatientRef | None:
        return tools.get_patient_profile(self._client, patient_id)

    def get_patient_claims(self, patient_id: str) -> list[Claim]:
        return tools.get_patient_claims(self._client, patient_id)

    def get_patient_encounters(self, patient_id: str) -> list[HealthRecord]:
        return tools.get_patient_encounters(self._client, patient_id)

    def get_patient_procedures(self, patient_id: str) -> list[HealthRecord]:
        return tools.get_patient_procedures(self._client, patient_id)

    def get_patient_medications(self, patient_id: str) -> list[HealthRecord]:
        return tools.get_patient_medications(self._client, patient_id)
