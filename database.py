import sqlite3
import os
import json
from datetime import datetime
from contextlib import contextmanager

DATABASE_PATH = os.environ.get("SQLITE_DB_PATH", "flowai.db")

def get_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_configurations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT,
                integration TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS automation_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                flow_data TEXT NOT NULL,
                intent_data TEXT,
                interval_minutes INTEGER DEFAULT 60,
                is_active INTEGER DEFAULT 0,
                last_run TEXT,
                run_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS saved_flows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                prompt TEXT NOT NULL,
                flow_data TEXT NOT NULL,
                intent_data TEXT,
                validation_score INTEGER DEFAULT 0,
                last_executed TEXT,
                execution_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS workflow_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                canvas_zoom REAL DEFAULT 1.0,
                canvas_offset_x REAL DEFAULT 0,
                canvas_offset_y REAL DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                last_executed TEXT,
                execution_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS workflow_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                node_id TEXT NOT NULL,
                name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                node_category TEXT NOT NULL,
                position_x REAL DEFAULT 0,
                position_y REAL DEFAULT 0,
                config TEXT,
                is_enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES workflow_projects(id) ON DELETE CASCADE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS workflow_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                edge_id TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                source_port TEXT DEFAULT 'output',
                target_port TEXT DEFAULT 'input',
                label TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES workflow_projects(id) ON DELETE CASCADE
            )
        ''')

def row_to_dict(row):
    if row is None:
        return None
    return dict(row)

def rows_to_list(rows):
    return [dict(row) for row in rows]


class UserConfiguration:
    @staticmethod
    def get_all():
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_configurations ORDER BY id")
            return rows_to_list(cursor.fetchall())
    
    @staticmethod
    def get_by_key(key):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_configurations WHERE key = ?", (key,))
            return row_to_dict(cursor.fetchone())
    
    @staticmethod
    def get_by_integration(integration):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_configurations WHERE integration = ?", (integration,))
            return rows_to_list(cursor.fetchall())
    
    @staticmethod
    def create(key, value, integration):
        with get_db() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute(
                "INSERT INTO user_configurations (key, value, integration, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (key, value, integration, now, now)
            )
            return cursor.lastrowid
    
    @staticmethod
    def update(key, value, integration=None):
        with get_db() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            if integration is not None:
                cursor.execute(
                    "UPDATE user_configurations SET value = ?, integration = ?, updated_at = ? WHERE key = ?",
                    (value, integration, now, key)
                )
            else:
                cursor.execute(
                    "UPDATE user_configurations SET value = ?, updated_at = ? WHERE key = ?",
                    (value, now, key)
                )
            return cursor.rowcount > 0
    
    @staticmethod
    def delete(key):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_configurations WHERE key = ?", (key,))
            return cursor.rowcount > 0


class AutomationSchedule:
    @staticmethod
    def get_all():
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM automation_schedules ORDER BY id")
            return rows_to_list(cursor.fetchall())
    
    @staticmethod
    def get_by_id(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM automation_schedules WHERE id = ?", (id,))
            return row_to_dict(cursor.fetchone())
    
    @staticmethod
    def get_active():
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM automation_schedules WHERE is_active = 1")
            return rows_to_list(cursor.fetchall())
    
    @staticmethod
    def create(name, description, flow_data, intent_data, interval_minutes=60):
        with get_db() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute(
                """INSERT INTO automation_schedules 
                   (name, description, flow_data, intent_data, interval_minutes, created_at, updated_at) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, description, flow_data, intent_data, interval_minutes, now, now)
            )
            return cursor.lastrowid
    
    @staticmethod
    def update(id, **kwargs):
        with get_db() as conn:
            cursor = conn.cursor()
            kwargs['updated_at'] = datetime.utcnow().isoformat()
            set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
            values = list(kwargs.values()) + [id]
            cursor.execute(f"UPDATE automation_schedules SET {set_clause} WHERE id = ?", values)
            return cursor.rowcount > 0
    
    @staticmethod
    def delete(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM automation_schedules WHERE id = ?", (id,))
            return cursor.rowcount > 0


class SavedFlow:
    @staticmethod
    def get_all():
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM saved_flows ORDER BY created_at DESC")
            return rows_to_list(cursor.fetchall())
    
    @staticmethod
    def get_by_id(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM saved_flows WHERE id = ?", (id,))
            return row_to_dict(cursor.fetchone())
    
    @staticmethod
    def create(name, description, prompt, flow_data, intent_data, validation_score=0):
        with get_db() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute(
                """INSERT INTO saved_flows 
                   (name, description, prompt, flow_data, intent_data, validation_score, created_at, updated_at) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, description, prompt, flow_data, intent_data, validation_score, now, now)
            )
            return cursor.lastrowid
    
    @staticmethod
    def update(id, **kwargs):
        with get_db() as conn:
            cursor = conn.cursor()
            kwargs['updated_at'] = datetime.utcnow().isoformat()
            set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
            values = list(kwargs.values()) + [id]
            cursor.execute(f"UPDATE saved_flows SET {set_clause} WHERE id = ?", values)
            return cursor.rowcount > 0
    
    @staticmethod
    def delete(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM saved_flows WHERE id = ?", (id,))
            return cursor.rowcount > 0
    
    @staticmethod
    def to_dict(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "prompt": row["prompt"],
            "flow": json.loads(row["flow_data"]) if row["flow_data"] else {},
            "intent": json.loads(row["intent_data"]) if row["intent_data"] else {},
            "validation_score": row["validation_score"],
            "last_executed": row["last_executed"],
            "execution_count": row["execution_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }


class WorkflowProject:
    @staticmethod
    def get_all():
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workflow_projects ORDER BY updated_at DESC")
            return rows_to_list(cursor.fetchall())
    
    @staticmethod
    def get_by_id(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workflow_projects WHERE id = ?", (id,))
            return row_to_dict(cursor.fetchone())
    
    @staticmethod
    def create(name, description=""):
        with get_db() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute(
                """INSERT INTO workflow_projects 
                   (name, description, created_at, updated_at) 
                   VALUES (?, ?, ?, ?)""",
                (name, description, now, now)
            )
            return cursor.lastrowid
    
    @staticmethod
    def update(id, **kwargs):
        with get_db() as conn:
            cursor = conn.cursor()
            kwargs['updated_at'] = datetime.utcnow().isoformat()
            set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
            values = list(kwargs.values()) + [id]
            cursor.execute(f"UPDATE workflow_projects SET {set_clause} WHERE id = ?", values)
            return cursor.rowcount > 0
    
    @staticmethod
    def delete(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM workflow_nodes WHERE project_id = ?", (id,))
            cursor.execute("DELETE FROM workflow_edges WHERE project_id = ?", (id,))
            cursor.execute("DELETE FROM workflow_projects WHERE id = ?", (id,))
            return cursor.rowcount > 0
    
    @staticmethod
    def get_nodes(project_id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workflow_nodes WHERE project_id = ? ORDER BY position_x", (project_id,))
            return rows_to_list(cursor.fetchall())
    
    @staticmethod
    def get_edges(project_id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workflow_edges WHERE project_id = ?", (project_id,))
            return rows_to_list(cursor.fetchall())
    
    @staticmethod
    def to_dict(row, include_children=True):
        if row is None:
            return None
        result = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "canvas_zoom": row["canvas_zoom"],
            "canvas_offset_x": row["canvas_offset_x"],
            "canvas_offset_y": row["canvas_offset_y"],
            "is_active": bool(row["is_active"]),
            "last_executed": row["last_executed"],
            "execution_count": row["execution_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }
        if include_children:
            result["nodes"] = [WorkflowNode.to_dict(n) for n in WorkflowProject.get_nodes(row["id"])]
            result["edges"] = [WorkflowEdge.to_dict(e) for e in WorkflowProject.get_edges(row["id"])]
        return result
    
    @staticmethod
    def to_flow_json(project_id):
        project = WorkflowProject.get_by_id(project_id)
        if not project:
            return None
        nodes = WorkflowProject.get_nodes(project_id)
        edges = WorkflowProject.get_edges(project_id)
        
        nodes_list = []
        for node in nodes:
            node_data = {
                "id": node["node_id"],
                "name": node["name"],
                "type": node["node_type"],
                "config": json.loads(node["config"]) if node["config"] else {}
            }
            nodes_list.append(node_data)
        
        connections = []
        for edge in edges:
            connections.append({
                "from": edge["source_node_id"],
                "to": edge["target_node_id"],
                "label": edge["label"] or ""
            })
        
        return {
            "name": project["name"],
            "description": project["description"] or "",
            "nodes": nodes_list,
            "connections": connections
        }


class WorkflowNode:
    @staticmethod
    def get_by_id(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workflow_nodes WHERE id = ?", (id,))
            return row_to_dict(cursor.fetchone())
    
    @staticmethod
    def get_by_node_id(project_id, node_id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workflow_nodes WHERE project_id = ? AND node_id = ?", (project_id, node_id))
            return row_to_dict(cursor.fetchone())
    
    @staticmethod
    def create(project_id, node_id, name, node_type, node_category, position_x=0, position_y=0, config=None):
        with get_db() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            config_str = json.dumps(config) if config else None
            cursor.execute(
                """INSERT INTO workflow_nodes 
                   (project_id, node_id, name, node_type, node_category, position_x, position_y, config, created_at, updated_at) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (project_id, node_id, name, node_type, node_category, position_x, position_y, config_str, now, now)
            )
            return cursor.lastrowid
    
    @staticmethod
    def update(id, **kwargs):
        with get_db() as conn:
            cursor = conn.cursor()
            kwargs['updated_at'] = datetime.utcnow().isoformat()
            if 'config' in kwargs and isinstance(kwargs['config'], dict):
                kwargs['config'] = json.dumps(kwargs['config'])
            set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
            values = list(kwargs.values()) + [id]
            cursor.execute(f"UPDATE workflow_nodes SET {set_clause} WHERE id = ?", values)
            return cursor.rowcount > 0
    
    @staticmethod
    def delete(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM workflow_nodes WHERE id = ?", (id,))
            return cursor.rowcount > 0
    
    @staticmethod
    def to_dict(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "node_id": row["node_id"],
            "name": row["name"],
            "node_type": row["node_type"],
            "node_category": row["node_category"],
            "position_x": row["position_x"],
            "position_y": row["position_y"],
            "config": json.loads(row["config"]) if row["config"] else {},
            "is_enabled": bool(row["is_enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }


class WorkflowEdge:
    @staticmethod
    def get_by_id(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workflow_edges WHERE id = ?", (id,))
            return row_to_dict(cursor.fetchone())
    
    @staticmethod
    def create(project_id, edge_id, source_node_id, target_node_id, source_port="output", target_port="input", label=None):
        with get_db() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute(
                """INSERT INTO workflow_edges 
                   (project_id, edge_id, source_node_id, target_node_id, source_port, target_port, label, created_at) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (project_id, edge_id, source_node_id, target_node_id, source_port, target_port, label, now)
            )
            return cursor.lastrowid
    
    @staticmethod
    def delete(id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM workflow_edges WHERE id = ?", (id,))
            return cursor.rowcount > 0
    
    @staticmethod
    def to_dict(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "edge_id": row["edge_id"],
            "source_node_id": row["source_node_id"],
            "target_node_id": row["target_node_id"],
            "source_port": row["source_port"],
            "target_port": row["target_port"],
            "label": row["label"],
            "created_at": row["created_at"]
        }
