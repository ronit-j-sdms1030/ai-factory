"""MongoDB access.

Deliberately the *same* database and collection names the Express backend
uses, so both can run against one dataset during migration. Mongoose
pluralises and lowercases model names, so the ``User`` model lives in
``users`` and ``Artifact`` in ``artifacts``; those names are reproduced here
rather than chosen afresh.
"""

from __future__ import annotations

from functools import lru_cache

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from . import config


@lru_cache(maxsize=1)
def client() -> MongoClient:
    return MongoClient(config.mongodb_uri())


def db() -> Database:
    # The database name is carried in the URI, matching how the Express
    # backend connects.
    return client().get_database()


def users() -> Collection:
    return db()["users"]


def artifacts() -> Collection:
    return db()["artifacts"]


def generation_jobs() -> Collection:
    """Background BRD/UI/decomposition runs.

    Python-side only — the Express backend has no model for this — so the name
    does not need to match a Mongoose pluralisation.
    """
    return db()["generationJobs"]
