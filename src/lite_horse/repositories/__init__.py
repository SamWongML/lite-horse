"""Tenant-scoped Postgres repositories.

Per the v0.4 Hard Contract this is the **only** package allowed to issue
raw SQL against a request-scoped `AsyncSession`. Every method honours the
`app.user_id` GUC set by `storage.db.db_session` either by RLS (defence
in depth) or explicit `WHERE user_id = ...` filters (the offence layer
RLS backs up).

`SearchHit` is re-exported here so callers can grab the FTS row shape
without reaching across packages.
"""

from lite_horse.effective import (
    EffectiveConfig,
    ResolvedCommand,
    ResolvedInstruction,
    ResolvedMcpServer,
    ResolvedSkill,
)
from lite_horse.repositories.audit_repo import AuditRepo
from lite_horse.repositories.base import BaseRepo, audited
from lite_horse.repositories.command_repo import CommandRepo
from lite_horse.repositories.cron_repo import CronRepo
from lite_horse.repositories.instruction_repo import InstructionRepo
from lite_horse.repositories.mcp_repo import McpRepo
from lite_horse.repositories.memory_repo import (
    MEMORY_MD_CHAR_LIMIT,
    USER_MD_CHAR_LIMIT,
    MemoryFull,
    MemoryRepo,
    UnsafeMemoryContent,
)
from lite_horse.repositories.message_repo import MessageRepo
from lite_horse.repositories.opt_out_repo import VALID_ENTITIES, OptOutRepo
from lite_horse.repositories.session_repo import SessionRepo
from lite_horse.repositories.skill_repo import SkillRepo
from lite_horse.repositories.user_repo import UserRepo
from lite_horse.repositories.user_settings_repo import (
    VALID_PERMISSION_MODES,
    UserSettings,
    UserSettingsRepo,
)
from lite_horse.sessions.types import SearchHit

__all__ = [
    "MEMORY_MD_CHAR_LIMIT",
    "USER_MD_CHAR_LIMIT",
    "VALID_ENTITIES",
    "VALID_PERMISSION_MODES",
    "AuditRepo",
    "BaseRepo",
    "CommandRepo",
    "CronRepo",
    "EffectiveConfig",
    "InstructionRepo",
    "McpRepo",
    "MemoryFull",
    "MemoryRepo",
    "MessageRepo",
    "OptOutRepo",
    "ResolvedCommand",
    "ResolvedInstruction",
    "ResolvedMcpServer",
    "ResolvedSkill",
    "SearchHit",
    "SessionRepo",
    "SkillRepo",
    "UnsafeMemoryContent",
    "UserRepo",
    "UserSettings",
    "UserSettingsRepo",
    "audited",
]
