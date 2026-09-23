
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    tool_name,
    tool_calls,
    content='messages',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages
WHEN (new.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                         WHERE key = 'fts_rebuild_high_water'), -1)
   OR new.id <= COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                          WHERE key = 'fts_rebuild_progress'), -1))
BEGIN
    INSERT INTO messages_fts(rowid, content, tool_name, tool_calls)
    VALUES (
        new.id,
        CASE WHEN new.role = 'tool'
              AND new.id > COALESCE((SELECT CAST(value AS INTEGER)
                                         FROM state_meta
                                         WHERE key = 'fts_tool_full_content_high_water'), -1)
         THEN substr(COALESCE(new.content, ''), 1, 8192)
         ELSE new.content END,
        new.tool_name,
        new.tool_calls
    );
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages
WHEN (old.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                         WHERE key = 'fts_rebuild_high_water'), -1)
   OR old.id <= COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                          WHERE key = 'fts_rebuild_progress'), -1))
BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, tool_name, tool_calls)
    VALUES (
        'delete',
        old.id,
        CASE WHEN old.role = 'tool'
              AND old.id > COALESCE((SELECT CAST(value AS INTEGER)
                                         FROM state_meta
                                         WHERE key = 'fts_tool_full_content_high_water'), -1)
         THEN substr(COALESCE(old.content, ''), 1, 8192)
         ELSE old.content END,
        old.tool_name,
        old.tool_calls
    );
END;

-- UPDATE OF skips the trigger entirely for non-content column writes
-- (status/compacted/observed/etc.), which is stronger than the WHEN gate
-- alone and avoids FTS I/O saturation on large state.db (#68858 / #73639).
CREATE TRIGGER IF NOT EXISTS messages_fts_update
AFTER UPDATE OF content, tool_name, tool_calls, role ON messages
WHEN (old.content IS NOT new.content
    OR old.tool_name IS NOT new.tool_name
    OR old.tool_calls IS NOT new.tool_calls
    OR old.role IS NOT new.role)
   AND (old.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                           WHERE key = 'fts_rebuild_high_water'), -1)
     OR old.id <= COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                            WHERE key = 'fts_rebuild_progress'), -1))
BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, tool_name, tool_calls)
    VALUES (
        'delete',
        old.id,
        CASE WHEN old.role = 'tool'
              AND old.id > COALESCE((SELECT CAST(value AS INTEGER)
                                         FROM state_meta
                                         WHERE key = 'fts_tool_full_content_high_water'), -1)
         THEN substr(COALESCE(old.content, ''), 1, 8192)
         ELSE old.content END,
        old.tool_name,
        old.tool_calls
    );
    INSERT INTO messages_fts(rowid, content, tool_name, tool_calls)
    VALUES (
        new.id,
        CASE WHEN new.role = 'tool'
              AND new.id > COALESCE((SELECT CAST(value AS INTEGER)
                                         FROM state_meta
                                         WHERE key = 'fts_tool_full_content_high_water'), -1)
         THEN substr(COALESCE(new.content, ''), 1, 8192)
         ELSE new.content END,
        new.tool_name,
        new.tool_calls
    );
END;


CREATE VIEW IF NOT EXISTS messages_fts_trigram_src AS
    SELECT m.id, m.role, m.content, m.tool_name
    FROM messages AS m
    JOIN sessions AS s ON s.id = m.session_id
    WHERE m.role <> 'tool' AND s.source NOT IN ('cron', 'subagent') AND json_extract((CASE WHEN json_valid(s.model_config) THEN s.model_config ELSE json_object() END), '$._delegate_from') IS NULL;

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(
    content,
    tool_name,
    content='messages_fts_trigram_src',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert AFTER INSERT ON messages
WHEN new.role <> 'tool'
   AND EXISTS (SELECT 1 FROM sessions
               WHERE id = new.session_id AND source NOT IN ('cron', 'subagent') AND json_extract((CASE WHEN json_valid(model_config) THEN model_config ELSE json_object() END), '$._delegate_from') IS NULL)
   AND (new.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                           WHERE key = 'fts_rebuild_high_water'), -1)
     OR new.id <= COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                            WHERE key = 'fts_rebuild_progress'), -1))
BEGIN
    INSERT INTO messages_fts_trigram(rowid, content, tool_name)
    VALUES (new.id, new.content, new.tool_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete AFTER DELETE ON messages
WHEN old.role <> 'tool'
   AND EXISTS (SELECT 1 FROM sessions
               WHERE id = old.session_id AND source NOT IN ('cron', 'subagent') AND json_extract((CASE WHEN json_valid(model_config) THEN model_config ELSE json_object() END), '$._delegate_from') IS NULL)
   AND (old.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                           WHERE key = 'fts_rebuild_high_water'), -1)
     OR old.id <= COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                            WHERE key = 'fts_rebuild_progress'), -1))
BEGIN
    INSERT INTO messages_fts_trigram(messages_fts_trigram, rowid, content, tool_name)
    VALUES ('delete', old.id, old.content, old.tool_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_update
AFTER UPDATE OF content, tool_name, role ON messages
WHEN (old.content IS NOT new.content
    OR old.tool_name IS NOT new.tool_name
    OR old.role IS NOT new.role)
   AND (old.id > COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                           WHERE key = 'fts_rebuild_high_water'), -1)
     OR old.id <= COALESCE((SELECT CAST(value AS INTEGER) FROM state_meta
                            WHERE key = 'fts_rebuild_progress'), -1))
BEGIN
    INSERT INTO messages_fts_trigram(messages_fts_trigram, rowid, content, tool_name)
    SELECT 'delete', old.id, old.content, old.tool_name
    WHERE old.role <> 'tool'
      AND EXISTS (SELECT 1 FROM sessions
                  WHERE id = old.session_id AND source NOT IN ('cron', 'subagent') AND json_extract((CASE WHEN json_valid(model_config) THEN model_config ELSE json_object() END), '$._delegate_from') IS NULL);
    INSERT INTO messages_fts_trigram(rowid, content, tool_name)
    SELECT new.id, new.content, new.tool_name
    WHERE new.role <> 'tool'
      AND EXISTS (SELECT 1 FROM sessions
                  WHERE id = new.session_id AND source NOT IN ('cron', 'subagent') AND json_extract((CASE WHEN json_valid(model_config) THEN model_config ELSE json_object() END), '$._delegate_from') IS NULL);
END;
