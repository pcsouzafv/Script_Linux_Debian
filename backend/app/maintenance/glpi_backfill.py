from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import Settings
from app.services.glpi import GLPIClient
from app.services.glpi_backfill import GLPIHistoricalBackfillService
from app.services.llm import LLMClient
from app.services.operational_store import OperationalStateStore
from app.services.triage import TriageAgent


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill analítico de tickets históricos no GLPI usando auditoria operacional e triagem por regras.",
    )
    parser.add_argument("--ticket-id", action="append", dest="ticket_ids", default=[])
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--apply", action="store_true", help="Envia as atualizações ao GLPI.")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    if not GLPIClient(settings).configured:
        print("GLPI não configurado no backend; o backfill histórico exige acesso ao GLPI ao vivo.")
        return 1

    triage_settings = settings.model_copy(update={"llm_enabled": False})
    service = GLPIHistoricalBackfillService(
        glpi_client=GLPIClient(settings),
        operational_store=OperationalStateStore(settings),
        triage_agent=TriageAgent(LLMClient(triage_settings)),
    )
    summary = await service.backfill_missing_analytics(
        ticket_ids=args.ticket_ids,
        limit=args.limit,
        offset=args.offset,
        dry_run=not args.apply,
    )

    if args.json_output:
        payload = {
            "processed_count": summary.processed_count,
            "updated_count": summary.updated_count,
            "dry_run_count": summary.dry_run_count,
            "skipped_count": summary.skipped_count,
            "error_count": summary.error_count,
            "notes": summary.notes,
            "results": [
                {
                    "ticket_id": result.ticket_id,
                    "subject": result.subject,
                    "status": result.status,
                    "external_id": result.external_id,
                    "request_type_id": result.request_type_id,
                    "request_type_name": result.request_type_name,
                    "category_id": result.category_id,
                    "category_name": result.category_name,
                    "asset_name": result.asset_name,
                    "linked_item_type": result.linked_item_type,
                    "linked_item_id": result.linked_item_id,
                    "linked_item_name": result.linked_item_name,
                    "notes": result.notes,
                }
                for result in summary.results
            ],
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0 if summary.error_count == 0 else 2

    print(
        "Backfill GLPI: "
        f"processados={summary.processed_count} atualizados={summary.updated_count} "
        f"dry_run={summary.dry_run_count} ignorados={summary.skipped_count} erros={summary.error_count}"
    )
    for note in summary.notes:
        print(f"- {note}")
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