-- Tiramisu portable — SQLite schema

CREATE TABLE IF NOT EXISTS tasks_v2 (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    -- strict sequence: pending → planned → implementing → pr_open → addressing_feedback → merged → done
    agent        TEXT,
    parent_task_id INTEGER REFERENCES tasks_v2(id),
    depends_on   TEXT    DEFAULT '[]',  -- JSON array of task IDs (DAG edges)
    pr_metadata  TEXT    DEFAULT '[]',  -- JSON array even for single-PR tasks
    ticket_id    TEXT,
    activity_log TEXT    DEFAULT '',    -- append-only, never overwrite
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    category   TEXT    NOT NULL,  -- preference | fact | correction | decision | context
    content    TEXT    NOT NULL,
    source     TEXT,              -- conversation ID, file path, or agent name
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS triage_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_path TEXT    NOT NULL UNIQUE,
    status         TEXT    NOT NULL,  -- promoted | rejected | pending
    processed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kb_hits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file  TEXT,
    chunk_id     TEXT,
    agent        TEXT,
    query        TEXT,
    distance     REAL,
    rank         INTEGER,
    result_count INTEGER,
    is_test      INTEGER DEFAULT 0,
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_member TEXT    NOT NULL,
    to_member   TEXT    NOT NULL,
    job         TEXT,
    message     TEXT    NOT NULL,
    read        INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transcripts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL UNIQUE,
    agent       TEXT,
    cwd         TEXT,
    title       TEXT,
    message_count INTEGER,
    extracted   INTEGER DEFAULT 0,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS partners (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    role       TEXT,
    status     TEXT    DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Keep updated_at current on tasks
CREATE TRIGGER IF NOT EXISTS tasks_updated_at
    AFTER UPDATE ON tasks_v2
    FOR EACH ROW
BEGIN
    UPDATE tasks_v2 SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- Keep updated_at current on memories
CREATE TRIGGER IF NOT EXISTS memories_updated_at
    AFTER UPDATE ON memories
    FOR EACH ROW
BEGIN
    UPDATE memories SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
