"""Hypothesis registry — what was proposed, what was tested, what it returned.

The point is pre-registration. A rule written down *before* it is tested, with
its exact parameters and its dataset, cannot be quietly edited afterwards to
match whatever the data happened to show. Without that record, "we tried a few
variants and this one worked" is indistinguishable from a discovery, and the
difference is the whole of the scientific claim.

Two rules the schema enforces rather than merely encourages:

* **Versions are immutable in spirit.** A rule that changes after seeing
  results is a NEW version — `(hypothesis_id, version)` is the primary key, so
  H003 v2 cannot overwrite H003 v1 and the earlier verdict survives.
* **A result is meaningless without its dataset.** `dataset_id` points at a
  manifest in the same database, so any recorded outcome can be traced to the
  exact candles that produced it.

`ACCEPTED` means a hypothesis passed the research gates defined at the time. It
does not mean it is safe to trade, and nothing here should be read as saying so.

Lives in `research.db` beside the dataset manifests, not in `trading.db` — this
is research metadata and must not be able to affect the live application.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.research.store import DB_PATH, ResearchStore

STATUSES = (
    "PROPOSED",    # an idea, no exact rule yet
    "DEFINED",     # exact rule written down, not yet implemented or run
    "TESTING",     # generator implemented, evaluation running
    "REJECTED",    # failed its pre-registered gates
    "PROMISING",   # passed development gates, not yet validated
    "VALIDATING",  # under validation-period testing
    "ACCEPTED",    # passed all gates defined at the time — NOT "safe to trade"
    "RETIRED",     # previously accepted, no longer in use
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_hypotheses (
    hypothesis_id TEXT NOT NULL,
    version       TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    status        TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    rule_definition TEXT NOT NULL,   -- JSON: the exact, pre-registered rule
    dataset_id    TEXT,
    result_summary TEXT,             -- JSON: what the evaluation returned
    notes         TEXT,
    PRIMARY KEY (hypothesis_id, version)
);
"""


@dataclass
class Hypothesis:
    hypothesis_id: str
    version: str
    name: str
    description: str
    status: str = "PROPOSED"
    rule_definition: dict = field(default_factory=dict)
    dataset_id: str | None = None
    result_summary: dict | None = None
    notes: str = ""
    created_at: int | None = None
    updated_at: int | None = None

    def as_dict(self) -> dict:
        return {
            "hypothesis_id": self.hypothesis_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "rule_definition": self.rule_definition,
            "dataset_id": self.dataset_id,
            "result_summary": self.result_summary,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class HypothesisRegistry:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        # Reuse ResearchStore purely to create the file and its directory, so
        # both live in one database with one set of conventions.
        ResearchStore(self.path)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self):
        return ResearchStore(self.path)._conn()  # noqa: SLF001 — same package

    def register(self, h: Hypothesis) -> Hypothesis:
        """Insert or update a hypothesis VERSION.

        Updating an existing version is for recording a result or a status
        change against an unchanged rule. Changing the rule itself means
        registering a new version — the primary key makes overwriting v1 with a
        different v1 possible only by explicit intent, and `rule_changed`
        reports when that has happened.
        """
        if h.status not in STATUSES:
            raise ValueError(f"unknown status {h.status!r}; expected one of {STATUSES}")
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        existing = self.get(h.hypothesis_id, h.version)
        created = existing.created_at if existing else now
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO research_hypotheses "
                "(hypothesis_id, version, name, description, status, created_at, updated_at, "
                " rule_definition, dataset_id, result_summary, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    h.hypothesis_id, h.version, h.name, h.description, h.status, created, now,
                    json.dumps(h.rule_definition), h.dataset_id,
                    json.dumps(h.result_summary) if h.result_summary is not None else None,
                    h.notes,
                ),
            )
        h.created_at, h.updated_at = created, now
        return h

    def rule_changed(self, hypothesis_id: str, version: str, rule: dict) -> bool:
        """True if `rule` differs from what this version already has recorded.

        The guard against silently editing a pre-registered rule: a caller can
        check this and register a new version instead of overwriting.
        """
        existing = self.get(hypothesis_id, version)
        return existing is not None and existing.rule_definition != rule

    def get(self, hypothesis_id: str, version: str) -> Hypothesis | None:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM research_hypotheses WHERE hypothesis_id=? AND version=?",
                (hypothesis_id, version),
            )
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
        return self._row(dict(zip(cols, row))) if row else None

    def list(self, status: str | None = None) -> list[Hypothesis]:
        sql = "SELECT * FROM research_hypotheses"
        args: tuple = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY hypothesis_id, version"
        with self._conn() as conn:
            cur = conn.execute(sql, args)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [self._row(dict(zip(cols, r))) for r in rows]

    def set_status(self, hypothesis_id: str, version: str, status: str, notes: str = "") -> Hypothesis:
        h = self.get(hypothesis_id, version)
        if h is None:
            raise KeyError(f"{hypothesis_id} {version} is not registered")
        h.status = status
        if notes:
            h.notes = f"{h.notes}\n{notes}".strip()
        return self.register(h)

    def record_result(
        self, hypothesis_id: str, version: str, dataset_id: str, summary: dict, status: str
    ) -> Hypothesis:
        """Attach an evaluation outcome. Requires the dataset it ran on."""
        h = self.get(hypothesis_id, version)
        if h is None:
            raise KeyError(f"{hypothesis_id} {version} is not registered")
        h.dataset_id = dataset_id
        h.result_summary = summary
        h.status = status
        return self.register(h)

    @staticmethod
    def _row(d: dict) -> Hypothesis:
        return Hypothesis(
            hypothesis_id=d["hypothesis_id"],
            version=d["version"],
            name=d["name"],
            description=d["description"],
            status=d["status"],
            rule_definition=json.loads(d["rule_definition"] or "{}"),
            dataset_id=d["dataset_id"],
            result_summary=json.loads(d["result_summary"]) if d["result_summary"] else None,
            notes=d["notes"] or "",
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )


registry = HypothesisRegistry()
