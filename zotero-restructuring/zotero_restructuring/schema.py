"""
schema.py — Zotero SQLite schema knowledge.

Documents the actual table/column structure found in Zotero 7 databases,
derived by inspecting /Users/cocconat/Documents/Research/zotero/zotero.sqlite.

This module contains no logic; it is pure data and documentation.
No imports from application modules; no external dependencies.
"""

# ── Core tables used for ingestion ───────────────────────────────────────────

# items
# ------
# itemID          INTEGER PRIMARY KEY
# itemTypeID      INT NOT NULL          -> itemTypes.itemTypeID
# dateAdded       TIMESTAMP
# dateModified    TIMESTAMP
# clientDateModified TIMESTAMP
# libraryID       INT NOT NULL          -> libraries.libraryID
# key             TEXT NOT NULL
# version         INT
# synced          INT

ITEMS_TABLE = "items"
ITEMS_COLUMNS = {
    "item_id": "itemID",
    "item_type_id": "itemTypeID",
    "date_added": "dateAdded",
    "date_modified": "dateModified",
    "library_id": "libraryID",
    "key": "key",
}

# itemTypes
# ---------
# itemTypeID      INTEGER PRIMARY KEY
# typeName        TEXT

ITEM_TYPES_TABLE = "itemTypes"
ITEM_TYPES_COLUMNS = {
    "item_type_id": "itemTypeID",
    "type_name": "typeName",
}

# itemData
# --------
# itemID          INT                   -> items.itemID
# fieldID         INT                   -> fields.fieldID (via fieldsCombined view)
# valueID         INT                   -> itemDataValues.valueID
# PRIMARY KEY (itemID, fieldID)

ITEM_DATA_TABLE = "itemData"
ITEM_DATA_COLUMNS = {
    "item_id": "itemID",
    "field_id": "fieldID",
    "value_id": "valueID",
}

# itemDataValues
# --------------
# valueID         INTEGER PRIMARY KEY
# value           TEXT UNIQUE

ITEM_DATA_VALUES_TABLE = "itemDataValues"
ITEM_DATA_VALUES_COLUMNS = {
    "value_id": "valueID",
    "value": "value",
}

# fields
# ------
# fieldID         INTEGER PRIMARY KEY
# fieldName       TEXT
# fieldFormatID   INT

FIELDS_TABLE = "fields"
FIELDS_COLUMNS = {
    "field_id": "fieldID",
    "field_name": "fieldName",
}

# fieldsCombined (view that unions fields + customFields)
# Available the same as fields for our purposes.
FIELDS_COMBINED_VIEW = "fieldsCombined"

# collections
# -----------
# collectionID           INTEGER PRIMARY KEY
# collectionName         TEXT NOT NULL
# parentCollectionID     INT DEFAULT NULL   -> collections.collectionID
# clientDateModified     TIMESTAMP
# libraryID              INT NOT NULL
# key                    TEXT NOT NULL
# version                INT
# synced                 INT

COLLECTIONS_TABLE = "collections"
COLLECTIONS_COLUMNS = {
    "collection_id": "collectionID",
    "name": "collectionName",
    "parent_id": "parentCollectionID",
    "library_id": "libraryID",
    "key": "key",
}

# collectionItems
# ---------------
# collectionID    INT NOT NULL   -> collections.collectionID
# itemID          INT NOT NULL   -> items.itemID
# orderIndex      INT
# PRIMARY KEY (collectionID, itemID)

COLLECTION_ITEMS_TABLE = "collectionItems"
COLLECTION_ITEMS_COLUMNS = {
    "collection_id": "collectionID",
    "item_id": "itemID",
    "order_index": "orderIndex",
}

# tags
# ----
# tagID           INTEGER PRIMARY KEY
# name            TEXT NOT NULL UNIQUE

TAGS_TABLE = "tags"
TAGS_COLUMNS = {
    "tag_id": "tagID",
    "name": "name",
}

# itemTags
# --------
# itemID          INT NOT NULL   -> items.itemID
# tagID           INT NOT NULL   -> tags.tagID
# type            INT NOT NULL   (0=automatic/user, 1=manual)
# PRIMARY KEY (itemID, tagID)

ITEM_TAGS_TABLE = "itemTags"
ITEM_TAGS_COLUMNS = {
    "item_id": "itemID",
    "tag_id": "tagID",
    "tag_type": "type",
}

# creators
# --------
# creatorID       INTEGER PRIMARY KEY
# firstName       TEXT
# lastName        TEXT
# fieldMode       INT   (0=two-field, 1=single-field/institution)

CREATORS_TABLE = "creators"
CREATORS_COLUMNS = {
    "creator_id": "creatorID",
    "first_name": "firstName",
    "last_name": "lastName",
    "field_mode": "fieldMode",
}

# itemCreators
# ------------
# itemID          INT NOT NULL   -> items.itemID
# creatorID       INT NOT NULL   -> creators.creatorID
# creatorTypeID   INT NOT NULL   -> creatorTypes.creatorTypeID
# orderIndex      INT
# PRIMARY KEY (itemID, creatorID, creatorTypeID, orderIndex)

ITEM_CREATORS_TABLE = "itemCreators"
ITEM_CREATORS_COLUMNS = {
    "item_id": "itemID",
    "creator_id": "creatorID",
    "creator_type_id": "creatorTypeID",
    "order_index": "orderIndex",
}

# itemAttachments
# ---------------
# itemID          INTEGER PRIMARY KEY -> items.itemID
# parentItemID    INT                 -> items.itemID (the parent reference item)
# linkMode        INT
# contentType     TEXT
# path            TEXT

ITEM_ATTACHMENTS_TABLE = "itemAttachments"
ITEM_ATTACHMENTS_COLUMNS = {
    "item_id": "itemID",
    "parent_item_id": "parentItemID",
    "link_mode": "linkMode",
    "content_type": "contentType",
    "path": "path",
}

# deletedItems
# ------------
# itemID          INTEGER PRIMARY KEY -> items.itemID
# dateDeleted     TIMESTAMP

DELETED_ITEMS_TABLE = "deletedItems"
DELETED_ITEMS_COLUMNS = {
    "item_id": "itemID",
    "date_deleted": "dateDeleted",
}

# libraries
# ---------
# libraryID       INTEGER PRIMARY KEY
# type            TEXT    ('user' or 'group')
# editable        INT
# filesEditable   INT

LIBRARIES_TABLE = "libraries"
LIBRARIES_COLUMNS = {
    "library_id": "libraryID",
    "type": "type",
}

# ── Tables present in the DB but not used for Phase 1-2 ingestion ─────────────
# feeds, feedItems, groups, groupItems, itemNotes, itemAnnotations,
# itemRelations, collectionRelations, savedSearches, savedSearchConditions,
# syncCache, syncDeleteLog, syncQueue, syncObjectTypes, syncedSettings,
# settings, translatorCache, fulltextItems, fulltextWords, fulltextItemWords,
# retractedItems, publicationsItems, storageDeleteLog, proxies, proxyHosts,
# users, version, dbDebug1

# ── Expected tables — used to detect schema mismatch ─────────────────────────
EXPECTED_TABLES: frozenset[str] = frozenset({
    "items",
    "itemTypes",
    "itemData",
    "itemDataValues",
    "fields",
    "collections",
    "collectionItems",
    "tags",
    "itemTags",
    "creators",
    "itemCreators",
    "itemAttachments",
    "deletedItems",
    "libraries",
})

# ── Primary library filtering ────────────────────────────────────────────────
# Zotero stores personal, group, and feed libraries in the same database.
# We only want the user's personal library (libraryID=1, type='user').
PRIMARY_LIBRARY_ID: int = 1

# Item types that are NOT real bibliographic items — exclude from ingestion.
# These are internal Zotero types that represent metadata, not references.
EXCLUDED_ITEM_TYPE_NAMES: frozenset[str] = frozenset({
    "annotation",
    "note",
    "attachment",
})

# ── Well-known field names used for fast lookups ──────────────────────────────
FIELD_TITLE = "title"
FIELD_DATE = "date"
FIELD_DOI = "DOI"
FIELD_URL = "url"
FIELD_ABSTRACT = "abstractNote"
FIELD_JOURNAL = "publicationTitle"
FIELD_VOLUME = "volume"
FIELD_ISSUE = "issue"
FIELD_PAGES = "pages"
FIELD_PUBLISHER = "publisher"
FIELD_PLACE = "place"
FIELD_ISBN = "ISBN"
FIELD_ISSN = "ISSN"
FIELD_EXTRA = "extra"
