from fastapi import Depends

from app.core.config import Settings, get_settings
from app.orchestration.helpdesk import HelpdeskOrchestrator
from app.services.glpi import GLPIClient
from app.services.identity import IdentityService
from app.services.intake import UserIntakeService
from app.services.llm import LLMClient
from app.services.triage import TriageAgent
from app.services.whatsapp import WhatsAppClient
from app.services.zabbix import ZabbixClient


def get_glpi_client(settings: Settings = Depends(get_settings)) -> GLPIClient:
    return GLPIClient(settings)


def get_zabbix_client(settings: Settings = Depends(get_settings)) -> ZabbixClient:
    return ZabbixClient(settings)


def get_whatsapp_client(settings: Settings = Depends(get_settings)) -> WhatsAppClient:
    return WhatsAppClient(settings)


def get_llm_client(settings: Settings = Depends(get_settings)) -> LLMClient:
    return LLMClient(settings)


def get_triage_agent(llm_client: LLMClient = Depends(get_llm_client)) -> TriageAgent:
    return TriageAgent(llm_client)


def get_identity_service(
    settings: Settings = Depends(get_settings),
    glpi_client: GLPIClient = Depends(get_glpi_client),
) -> IdentityService:
    return IdentityService(settings, glpi_client)


def get_user_intake_service(
    llm_client: LLMClient = Depends(get_llm_client),
) -> UserIntakeService:
    return UserIntakeService(llm_client=llm_client)


def get_helpdesk_orchestrator(
    glpi_client: GLPIClient = Depends(get_glpi_client),
    zabbix_client: ZabbixClient = Depends(get_zabbix_client),
    whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client),
    identity_service: IdentityService = Depends(get_identity_service),
    triage_agent: TriageAgent = Depends(get_triage_agent),
    user_intake_service: UserIntakeService = Depends(get_user_intake_service),
) -> HelpdeskOrchestrator:
    return HelpdeskOrchestrator(
        glpi_client=glpi_client,
        zabbix_client=zabbix_client,
        whatsapp_client=whatsapp_client,
        identity_service=identity_service,
        triage_agent=triage_agent,
        user_intake_service=user_intake_service,
    )
