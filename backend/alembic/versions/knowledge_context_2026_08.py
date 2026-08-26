"""org_configs.knowledge_context for admin-collected knowledge context

Adds a JSONB column to ``org_configs`` storing structured context collected
from the admin before knowledge ingestion:

    {
        "business_profile": {
            "business_name": ...,
            "business_description": ...,
            "products_services": ...,
            "target_customers": ...,
            "key_policies": ...,
            "answer_language_tone": ...
        },
        "sources": {
            "<doc_id | 'website'>": {
                "source_description": ...,
                "source_category": ...,
                "questions_to_answer": [...],
                ...
            }
        }
    }

The business profile is injected into the agent system prompt; per-source
context is stamped onto document_chunks.metadata at ingest time.

Also merges the two existing alembic heads (3b6628a68e6d and
tenant_org_notifications) so this revision becomes the single head.

Revision ID: knowledge_context_2026_08
Revises: 3b6628a68e6d, tenant_org_notifications
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "knowledge_context_2026_08"
down_revision: Union[str, Sequence[str], None] = (
    "3b6628a68e6d",
    "tenant_org_notifications",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE org_configs
        ADD COLUMN IF NOT EXISTS knowledge_context JSONB
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE org_configs
        DROP COLUMN IF EXISTS knowledge_context
        """
    )
