from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import Settings
from app.services.glpi import GLPIClient
from app.services.glpi_analytics import GLPIAnalyticsSyncService
from app.services.operational_store import OperationalStateStore
from app.services.ticket_analytics_store import TicketAnalyticsStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sincroniza snapshots analíticos de tickets do GLPI para o PostgreSQL operacional.",
    )
    parser.add_argument("--ticket-id", action="append", dest="ticket_ids", default=[])
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    if not settings.operational_postgres_dsn:
        print("HELPDESK_OPERATIONAL_POSTGRES_DSN nao configurado; a sincronizacao analitica exige PostgreSQL operacional.")
        return 1
    if not GLPIClient(settings).configured:
        print("GLPI não configurado; a sincronizacao analitica exige acesso ao GLPI ao vivo.")
        return 1

    service = GLPIAnalyticsSyncService(
        glpi_client=GLPIClient(settings),
        operational_store=OperationalStateStore(settings),
        analytics_store=TicketAnalyticsStore(settings),
    )
    summary = await service.sync_ticket_snapshots(
        ticket_ids=args.ticket_ids,
        limit=args.limit,
        offset=args.offset,
    )

    if args.json_output:
        payload = {
            "processed_count": summary.processed_count,
            "synced_count": summary.synced_count,
            "error_count": summary.error_count,
            "notes": summary.notes,
            "results": [
                {
                    "ticket_id": item.ticket_id,
                    "subject": item.subject,
                    "status": item.status,
                    "notes": item.notes,
                }
                for item in summary.results
            ],
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0 if summary.error_count == 0 else 2

    print(
        "Sync analitico GLPI: "
        f"processados={summary.processed_count} sincronizados={summary.synced_count} erros={summary.error_count}"
    )
    for result in summary.results:
        print(f"[{result.status}] ticket={result.ticket_id} assunto={result.subject}")
        for note in result.notes:
            print(f"  - {note}")
    return 0 if summary.error_count == 0 else 2


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())