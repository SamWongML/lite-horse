"""SQLAlchemy 2.0 declarative models matching the v0.4 locked DDL.

One file per entity. `Base` is the single declarative metadata that
Alembic uses for autogeneration, but the initial migration is
hand-written to preserve precise DDL (partial unique indexes, generated
tsvector column, RLS policies).
"""

from lite_horse.models.audit_log import AuditLog
from lite_horse.models.base import Base
from lite_horse.models.command import Command
from lite_horse.models.cron_job import CronJob
from lite_horse.models.instruction import Instruction
from lite_horse.models.mcp_server import McpServer
from lite_horse.models.message import Message
from lite_horse.models.opt_out import UserOfficialOptOut
from lite_horse.models.session import Session
from lite_horse.models.skill import Skill
from lite_horse.models.skill_proposal import SkillProposal
from lite_horse.models.usage_event import UsageEvent
from lite_horse.models.user import User
from lite_horse.models.user_document import UserDocument

__all__ = [
    "AuditLog",
    "Base",
    "Command",
    "CronJob",
    "Instruction",
    "McpServer",
    "Message",
    "Session",
    "Skill",
    "SkillProposal",
    "UsageEvent",
    "User",
    "UserDocument",
    "UserOfficialOptOut",
]
