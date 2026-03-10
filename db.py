"""SQLite storage for drafts and post history."""

import json
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "posts.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            image_prompt TEXT,
            image_path TEXT,
            trends_used TEXT,
            status TEXT DEFAULT 'draft',
            linkedin_post_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            posted_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS post_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            linkedin_post_id TEXT,
            impressions INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            checked_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (post_id) REFERENCES posts(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            title TEXT,
            messages TEXT NOT NULL DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


# --- Conversations ---

def create_conversation(chat_id, title=None):
    """Create a new conversation. Returns the conversation ID."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO conversations (chat_id, title, messages) VALUES (?, ?, '[]')",
        (chat_id, title or "New conversation"),
    )
    conn.commit()
    conv_id = cur.lastrowid
    conn.close()
    return conv_id


def save_conversation(conv_id, messages, title=None):
    """Save messages to an existing conversation."""
    conn = get_conn()
    if title:
        conn.execute(
            "UPDATE conversations SET messages = ?, title = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(messages), title, conv_id),
        )
    else:
        conn.execute(
            "UPDATE conversations SET messages = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(messages), conv_id),
        )
    conn.commit()
    conn.close()


def get_conversation(conv_id):
    """Get a conversation by ID."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["messages"] = json.loads(d["messages"])
        return d
    return None


def get_conversations(chat_id, limit=10):
    """Get recent conversations for a chat."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, title, updated_at FROM conversations WHERE chat_id = ? ORDER BY updated_at DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_conversation(conv_id):
    """Delete a conversation."""
    conn = get_conn()
    conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()


# --- Memory / Preferences ---

def remember(key, value):
    """Store a preference. Overwrites if key exists."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO memory (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    conn.commit()
    conn.close()


def forget(key):
    """Remove a preference."""
    conn = get_conn()
    conn.execute("DELETE FROM memory WHERE key = ?", (key,))
    conn.commit()
    conn.close()


def get_all_memories():
    """Get all stored preferences as a dict."""
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM memory").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def get_memory(key):
    """Get a single preference."""
    conn = get_conn()
    row = conn.execute("SELECT value FROM memory WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def save_draft(content, image_prompt, image_path, trends_used):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO posts (content, image_prompt, image_path, trends_used, status) VALUES (?, ?, ?, ?, 'draft')",
        (content, image_prompt, image_path, json.dumps(trends_used)),
    )
    conn.commit()
    draft_id = cur.lastrowid
    conn.close()
    return draft_id


def get_draft(draft_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (draft_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_latest_draft():
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM posts WHERE status = 'draft' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_draft_content(draft_id, new_content):
    conn = get_conn()
    conn.execute("UPDATE posts SET content = ? WHERE id = ?", (new_content, draft_id))
    conn.commit()
    conn.close()


def mark_posted(draft_id, linkedin_post_id=None):
    conn = get_conn()
    conn.execute(
        "UPDATE posts SET status = 'posted', posted_at = ?, linkedin_post_id = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), linkedin_post_id, draft_id),
    )
    conn.commit()
    conn.close()


def mark_skipped(draft_id):
    conn = get_conn()
    conn.execute("UPDATE posts SET status = 'skipped' WHERE id = ?", (draft_id,))
    conn.commit()
    conn.close()


def set_scheduled_time(draft_id, scheduled_at):
    """Set a scheduled posting time for a draft (ISO format string)."""
    conn = get_conn()
    conn.execute(
        "UPDATE posts SET status = 'scheduled', posted_at = ? WHERE id = ?",
        (scheduled_at, draft_id),
    )
    conn.commit()
    conn.close()


def get_scheduled_posts():
    """Get all posts scheduled for the future."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM posts WHERE status = 'scheduled' ORDER BY posted_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_posts_today():
    """Count how many posts were made/scheduled today."""
    conn = get_conn()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM posts WHERE status IN ('posted', 'scheduled') AND created_at > datetime('now', 'start of day')"
    ).fetchone()["c"]
    conn.close()
    return count


def save_metrics(post_id, linkedin_post_id, impressions, likes, comments, shares):
    """Save post performance metrics."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO post_metrics (post_id, linkedin_post_id, impressions, likes, comments, shares) VALUES (?, ?, ?, ?, ?, ?)",
        (post_id, linkedin_post_id, impressions, likes, comments, shares),
    )
    conn.commit()
    conn.close()


def get_top_posts(limit=5):
    """Get top performing posts by engagement for learning."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.content, p.trends_used, pm.impressions, pm.likes, pm.comments, pm.shares,
               (pm.likes + pm.comments * 3 + pm.shares * 5) as engagement_score
        FROM post_metrics pm
        JOIN posts p ON p.id = pm.post_id
        ORDER BY engagement_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_topics(days=7):
    """Get topics posted in the last N days to avoid repetition."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT trends_used FROM posts WHERE status = 'posted' AND created_at > datetime('now', ?)",
        (f"-{days} days",),
    ).fetchall()
    conn.close()
    topics = []
    for row in rows:
        try:
            topics.extend(json.loads(row["trends_used"]))
        except (json.JSONDecodeError, TypeError):
            pass
    return topics
