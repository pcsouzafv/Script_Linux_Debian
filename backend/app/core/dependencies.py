from fastapi import Depends

from app.agent_runtime import AgentRuntimeService
from app.core.config import Settings, get_settings
from app.orchestration.helpdesk import HelpdeskOrchestrator
from app.services.automation import AutomationService
from app.services.ansible_runner import AnsibleRunnerClient
from app.services.docker_runtime import DockerRuntimeClient
from app.services.glpi import GLPIClient
from app.services.identity import IdentityService
from app.services.intake import UserIntakeService
from app.services.job_queue import JobQueueService
from app.services.llm import LLMClient
from app.services.operational_store import OperationalStateStore
from app.services.ticket_analytics_store import TicketAnalyticsStore
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


def get_docker_runtime_client() -> DockerRuntimeClient:
    return DockerRuntimeClient()


def get_operational_store(
    settings: Settings = Depends(get_settings),
) -> OperationalStateStore:
    return OperationalStateStore(settings)


def get_ticket_analytics_store(
    settings: Settings = Depends(get_settings),
) -> TicketAnalyticsStore:
    return TicketAnalyticsStore(settings)


def get_job_queue_service(
    settings: Settings = Depends(get_settings),
) -> JobQueueService:
    return JobQueueService(settings)


def get_ansible_runner_client(
    settings: Settings = Depends(get_settings),
) -> AnsibleRunnerClient:
    return AnsibleRunnerClient(settings)


def get_triage_agent(
    llm_client: LLMClient = Depends(get_llm_client),
    analytics_store: TicketAnalyticsStore = Depends(get_ticket_analytics_store),
) -> TriageAgent:
    return TriageAgent(llm_client, analytics_store=analytics_store)


def get_identity_service(
    settings: Settings = Depends(get_settings),
    glpi_client: GLPIClient = Depends(get_glpi_client),
) -> IdentityService:
    return IdentityService(settings, glpi_client)


def get_automation_service(
    glpi_client: GLPIClient = Depends(get_glpi_client),
    ansible_runner_client: AnsibleRunnerClient = Depends(get_ansible_runner_client),
) -> AutomationService:
    return AutomationService(glpi_client, ansible_runner_client=ansible_runner_client)


def get_user_intake_service(
    llm_client: LLMClient = Depends(get_llm_client),
    operational_store: OperationalStateStore = Depends(get_operational_store),
) -> UserIntakeService:
    return UserIntakeService(llm_client=llm_client, operational_store=operational_store)


def get_helpdesk_orchestrator(
    glpi_client: GLPIClient = Depends(get_glpi_client),
    zabbix_client: ZabbixClient = Depends(get_zabbix_client),
    whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client),
    llm_client: LLMClient = Depends(get_llm_client),
    identity_service: IdentityService = Depends(get_identity_service),
    automation_service: AutomationService = Depends(get_automation_service),
    triage_agent: TriageAgent = Depends(get_triage_agent),
    user_intake_service: UserIntakeService = Depends(get_user_intake_service),
    operational_store: OperationalStateStore = Depends(get_operational_store),
    analytics_store: TicketAnalyticsStore = Depends(get_ticket_analytics_store),
    job_queue: JobQueueService = Depends(get_job_queue_service),
) -> HelpdeskOrchestrator:
    return HelpdeskOrchestrator(
        glpi_client=glpi_client,
        zabbix_client=zabbix_client,
        whatsapp_client=whatsapp_client,
        llm_client=llm_client,
        identity_service=identity_service,
        automation_service=automation_service,
        triage_agent=triage_agent,
        user_intake_service=user_intake_service,
        operational_store=operational_store,
        analytics_store=analytics_store,
        job_queue=job_queue,
    )


def get_agent_runtime_service(
    settings: Settings = Depends(get_settings),
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> AgentRuntimeService:
    return AgentRuntimeService(
        settings=settings,
        orchestrator=orchestrator,
    )
