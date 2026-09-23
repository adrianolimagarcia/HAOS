
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (
        new.id,
        COALESCE(CASE WHEN new.role = 'tool'
              AND new.id > COALESCE((SELECT CAST(value AS INTEGER)
                                         FROM state_meta
                                         WHERE key = 'fts_tool_full_content_high_water'), -1)
         THEN substr(COALESCE(new.content, ''), 1, 8192)
         ELSE new.content END, '')
        || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update
AFTER UPDATE OF content, tool_name, tool_calls, role ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
    INSERT INTO messages_fts(rowid, content) VALUES (
        new.id,
        COALESCE(CASE WHEN new.role = 'tool'
              AND new.id > COALESCE((SELECT CAST(value AS INTEGER)
                                         FROM state_meta
                                         WHERE key = 'fts_tool_full_content_high_water'), -1)
         THEN substr(COALESCE(new.content, ''), 1, 8192)
         ELSE new.content END, '')
        || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
    );
END;


CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(
    content,
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts_trigram(rowid, content) VALUES (
        new.id,
        COALESCE(CASE WHEN new.role = 'tool'
              AND new.id > COALESCE((SELECT CAST(value AS INTEGER)
                                         FROM state_meta
                                         WHERE key = 'fts_tool_full_content_high_water'), -1)
         THEN substr(COALESCE(new.content, ''), 1, 8192)
         ELSE new.content END, '')
        || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_update
AFTER UPDATE OF content, tool_name, tool_calls, role ON messages BEGIN
    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
    INSERT INTO messages_fts_trigram(rowid, content) VALUES (
        new.id,
        COALESCE(CASE WHEN new.role = 'tool'
              AND new.id > COALESCE((SELECT CAST(value AS INTEGER)
                                         FROM state_meta
                                         WHERE key = 'fts_tool_full_content_high_water'), -1)
         THEN substr(COALESCE(new.content, ''), 1, 8192)
         ELSE new.content END, '')
        || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
    );
END;
