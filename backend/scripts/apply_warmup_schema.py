#!/usr/bin/env python3
"""Apply database schema changes for debate warmup and cleanup."""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'legion_dashboard.db')

def apply_schema():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if columns already exist
    cursor.execute("PRAGMA table_info(debate_execution_config);")
    config_cols = {col[1] for col in cursor.fetchall()}
    
    cursor.execute("PRAGMA table_info(debate_runs);")
    runs_cols = {col[1] for col in cursor.fetchall()}
    
    # Add warmup fields to debate_execution_config if missing
    warmup_cols = ['warm_model_before_debate', 'warmup_timeout_seconds', 'keep_model_loaded_for', 'fail_debate_if_warmup_fails']
    for col in warmup_cols:
        if col not in config_cols:
            print(f"Adding {col} to debate_execution_config...")
            if col == 'warm_model_before_debate':
                cursor.execute(f"ALTER TABLE debate_execution_config ADD COLUMN {col} BOOLEAN DEFAULT 1")
            elif col == 'warmup_timeout_seconds':
                cursor.execute(f"ALTER TABLE debate_execution_config ADD COLUMN {col} INTEGER DEFAULT 300")
            elif col == 'keep_model_loaded_for':
                cursor.execute(f"ALTER TABLE debate_execution_config ADD COLUMN {col} VARCHAR(20) DEFAULT '1h'")
            elif col == 'fail_debate_if_warmup_fails':
                cursor.execute(f"ALTER TABLE debate_execution_config ADD COLUMN {col} BOOLEAN DEFAULT 1")
    
    # Add execution tracking fields to debate_runs if missing
    exec_cols = ['execution_stage', 'warmup_started_at', 'warmup_completed_at', 'warmup_duration_ms', 
                 'warmup_method', 'warmup_error', 'generation_started_at', 'generation_completed_at',
                 'generation_duration_ms', 'error_type']
    for col in exec_cols:
        if col not in runs_cols:
            print(f"Adding {col} to debate_runs...")
            cursor.execute(f"ALTER TABLE debate_runs ADD COLUMN {col} TEXT")
    
    # Add cleanup/visibility fields to debate_runs if missing
    cleanup_cols = ['hidden_at', 'hidden_by', 'hidden_reason', 'hidden_category', 
                    'is_test_run', 'superseded_by_run_id', 'cleanup_note']
    for col in cleanup_cols:
        if col not in runs_cols:
            print(f"Adding {col} to debate_runs...")
            if col == 'is_test_run':
                cursor.execute(f"ALTER TABLE debate_runs ADD COLUMN {col} BOOLEAN DEFAULT 0")
            else:
                cursor.execute(f"ALTER TABLE debate_runs ADD COLUMN {col} TEXT")
    
    # Update alembic version
    cursor.execute("UPDATE alembic_version SET version_num = '0012'")
    
    conn.commit()
    conn.close()
    print("Database schema updated successfully!")

if __name__ == '__main__':
    apply_schema()
