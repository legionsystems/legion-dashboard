"""LEGION Debate Worker - Durable background execution service.

This worker polls the database for queued debate runs, claims them with leases,
warms models, executes turns one at a time, and persists progress after each turn.

Usage:
    python -m app.debate_worker

Environment:
    DATABASE_URL: Database connection (inherited from app)
    WORKER_ID: Stable worker identifier (auto-generated if not set)
"""
import asyncio
import json
import os
import signal
import socket
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .database import SessionLocal, engine
from .models import DebateExecutionConfig, DebateRun, ModelHost
from .debate_executor import (
    execute_debate_run,
    is_local_endpoint,
    warm_model_ollama_native,
    warm_model_openai_compatible,
)
from .debate_warmup import warm_model_ollama_native as warmup_ollama, warm_model_openai_compatible as warmup_openai


class DebateWorker:
    """Durable debate execution worker with lease-based job claiming."""
    
    def __init__(self):
        self.worker_id = os.environ.get(
            "WORKER_ID",
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self.running = True
        self.current_run_id: Optional[int] = None
        self.lease_extension_seconds = 60
        
        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        
        print(f"[WORKER] Starting {self.worker_id}")
    
    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown."""
        print(f"[WORKER] Received signal {signum}, shutting down...")
        self.running = False
        if self.current_run_id:
            print(f"[WORKER] Will release run {self.current_run_id} on shutdown")
    
    def _get_db(self) -> Session:
        """Get database session."""
        return SessionLocal()
    
    def _get_config(self, db: Session) -> DebateExecutionConfig:
        """Get debate execution config."""
        config = db.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()
        if not config:
            # Create default config if missing
            config = DebateExecutionConfig(id=1, enabled=False)
            db.add(config)
            db.commit()
            db.refresh(config)
        return config
    
    def _claim_run(self, db: Session, config: DebateExecutionConfig) -> Optional[int]:
        """Atomically claim a queued debate run with lease.
        
        Returns the run ID or None if no runs available.
        """
        # Find queued runs (not cancelled, not claimed by another worker)
        now = datetime.now(timezone.utc)
        
        # Claim new queued runs or reclaim stale runs
        run = (
            db.query(DebateRun)
            .filter(
                and_(
                    or_(
                        and_(
                            DebateRun.worker_status == "queued",
                            DebateRun.cancel_requested == False,
                        ),
                        and_(
                            DebateRun.worker_status.in_(["claimed", "warming", "running"]),
                            DebateRun.lease_until < now,  # Stale lease
                        ),
                    )
                )
            )
            .order_by(DebateRun.queued_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        
        if not run:
            return None
        
        # Claim the run
        lease_until = now + timedelta(seconds=int(config.worker_lease_seconds))
        run.worker_status = "claimed"
        run.worker_id = self.worker_id
        run.lease_until = lease_until
        run.claimed_at = now
        run.heartbeat_at = now
        run.started_at = now
        db.commit()
        
        run_id = run.id
        print(f"[WORKER] Claimed run {run_id} (lease until {lease_until})")
        return run_id
    
    def _heartbeat(self, db: Session, run: DebateRun, config: DebateExecutionConfig):
        """Extend lease and update heartbeat."""
        now = datetime.now(timezone.utc)
        run.heartbeat_at = now
        run.lease_until = now + timedelta(seconds=config.worker_lease_seconds)
        db.commit()
        print(f"[WORKER] Heartbeat for run {run.id} (lease extended to {run.lease_until})")
    
    def _check_cancel(self, db: Session, run: DebateRun) -> bool:
        """Check if cancel was requested. Returns True if cancelled."""
        db.refresh(run)
        if run.cancel_requested:
            run.worker_status = "cancelled"
            run.status = "cancelled"
            run.cancelled_at = datetime.now(timezone.utc)
            run.cancelled_by = "operator"
            run.completed_at = run.cancelled_at
            db.commit()
            print(f"[WORKER] Run {run.id} cancelled by operator")
            return True
        return False
    
    def _warm_model(self, db: Session, run: DebateRun, config: DebateExecutionConfig) -> bool:
        """Warm model before debate execution. Returns True if successful."""
        if not config.warm_model_before_debate:
            return True
        
        print(f"[WORKER] Warming model for run {run.id}...")
        run.worker_status = "warming"
        run.execution_stage = "warming"
        run.warmup_started_at = datetime.now(timezone.utc)
        run.last_progress_at = run.warmup_started_at
        run.progress_message = "Warming model before debate"
        db.commit()
        
        # Resolve model/host
        base_url = config.base_url
        model = config.default_model
        api_key = config.api_key
        
        if config.default_host_id:
            host = db.query(ModelHost).filter(ModelHost.id == config.default_host_id).first()
            if host:
                base_url = host.base_url
                api_key = host.api_key or api_key
                # Check if host is enabled
                if not host.enabled:
                    run.worker_status = "failed"
                    run.status = "failed"
                    run.error_type = "model_provider_disabled"
                    run.error_message = "Selected model provider is disabled in Settings > Model Providers."
                    run.completed_at = datetime.now(timezone.utc)
                    db.commit()
                    print(f"[WORKER] Run {run.id} failed: provider disabled")
                    return False
        
        # Check cloud guard
        is_cloud = not is_local_endpoint(base_url)
        if is_cloud and not config.allow_cloud_endpoints:
            run.worker_status = "failed"
            run.status = "failed"
            run.error_type = "cloud_endpoint_blocked"
            run.error_message = "Cloud endpoints not allowed. Enable allow_cloud_endpoints in settings."
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return False
        
        # Perform warmup
        is_ollama = base_url.rstrip("/").endswith("/v1") or "ollama" in base_url.lower()
        run.warmup_method = "ollama_native" if is_ollama else "openai_compatible_ping"
        
        try:
            if is_ollama:
                success, error, latency_ms = warmup_ollama(
                    base_url=base_url,
                    model=model,
                    keep_alive=config.keep_model_loaded_for,
                    timeout_seconds=config.warmup_timeout_seconds,
                )
            else:
                success, error, latency_ms = warmup_openai(
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                    timeout_seconds=config.warmup_timeout_seconds,
                )
            
            if success:
                run.warmup_completed_at = datetime.now(timezone.utc)
                run.warmup_duration_ms = latency_ms
                print(f"[WORKER] Model warmed successfully in {latency_ms}ms")
                return True
            else:
                run.warmup_error = error
                if config.fail_debate_if_warmup_fails:
                    run.worker_status = "failed"
                    run.status = "failed"
                    run.error_type = "model_warmup_failed"
                    run.error_message = f"Model warmup failed: {error}"
                    run.completed_at = datetime.now(timezone.utc)
                    db.commit()
                    return False
                else:
                    # Continue but record failure
                    print(f"[WORKER] Warmup failed but continuing: {error}")
                    return True
        except Exception as e:
            run.warmup_error = str(e)
            if config.fail_debate_if_warmup_fails:
                run.worker_status = "failed"
                run.status = "failed"
                run.error_type = "model_warmup_error"
                run.error_message = f"Model warmup error: {type(e).__name__}"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
                return False
            return True
    
    def _execute_turns(self, db: Session, run: DebateRun, config: DebateExecutionConfig):
        """Execute debate turns one at a time with persistence."""
        print(f"[WORKER] Executing debate turns for run {run.id}...")
        run.worker_status = "running"
        run.execution_stage = "generating"
        run.generation_started_at = datetime.now(timezone.utc)
        db.commit()
        
        # Fetch work item for this run
        from .models import WorkItem
        work_item = db.query(WorkItem).filter(WorkItem.id == run.work_item_id).first()
        if not work_item:
            print(f"[WORKER] Work item {run.work_item_id} not found for run {run.id}")
            run.worker_status = "failed"
            run.status = "failed"
            run.error_type = "work_item_not_found"
            run.error_message = f"Work item {run.work_item_id} not found"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        
        # Convert DebateExecutionConfig to ExecutionConfig for execute_debate_run
        from .debate_executor import ExecutionConfig, get_execution_config
        exec_config = get_execution_config(db)
        
        # Use existing execute_debate_run which persists progress
        try:
            execute_debate_run(db, run, work_item, exec_config)
            print(f"[WORKER] execute_debate_run completed for run {run.id}")
            # Refresh run state after execution
            db.refresh(run)
            print(f"[WORKER] Run {run.id} state after execution: status={run.status}, error_type={run.error_type}")
        except Exception as e:
            print(f"[WORKER] Error executing run {run.id}: {e}")
            run.worker_status = "failed"
            run.status = "failed"
            run.error_type = "execution_error"
            run.error_message = f"Execution error: {type(e).__name__}"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
    
    def _complete_run(self, db: Session, run: DebateRun):
        """Mark run as completed."""
        print(f"[WORKER] Completing run {run.id}...")
        now = datetime.now(timezone.utc)
        run.worker_status = "completed"
        run.status = "completed"
        run.completed_at = now
        run.execution_stage = "completed"
        if run.generation_started_at:
            run.generation_completed_at = now
            # Handle both timezone-aware and naive datetimes
            gen_start = run.generation_started_at
            if gen_start.tzinfo is None:
                # Make it timezone-aware assuming UTC
                gen_start = gen_start.replace(tzinfo=timezone.utc)
            run.generation_duration_ms = int((now - gen_start).total_seconds() * 1000)
        db.commit()
        print(f"[WORKER] Run {run.id} completed")
    
    def process_run(self, run_id: int):
        """Process a single debate run through completion."""
        self.current_run_id = run_id
        db = self._get_db()
        
        try:
            # Fetch fresh run from this session
            run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
            if not run:
                print(f"[WORKER] Run {run_id} not found")
                return
            
            config = self._get_config(db)
            
            # Check cancel before starting
            if self._check_cancel(db, run):
                return
            
            # Warm model if configured
            if not self._warm_model(db, run, config):
                return
            
            # Check cancel after warmup
            if self._check_cancel(db, run):
                return
            
            # Execute turns
            self._execute_turns(db, run, config)
            
            # Check final state - refresh to get latest state from execute_debate_run
            db.refresh(run)
            print(f"[WORKER] After execution: run {run.id} status={run.status}, worker_status={run.worker_status}")
            
            # Only complete if not already failed/cancelled by execute_debate_run
            if run.status not in ["failed", "cancelled"] and run.worker_status not in ["failed", "cancelled"]:
                self._complete_run(db, run)
                
        except Exception as e:
            print(f"[WORKER] Fatal error processing run {run_id}: {e}")
            # Try to mark as failed
            try:
                run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
                if run:
                    run.worker_status = "failed"
                    run.status = "failed"
                    run.error_type = "worker_fatal"
                    run.error_message = f"Worker fatal error: {type(e).__name__}"
                    run.completed_at = datetime.now(timezone.utc)
                    db.commit()
            except:
                pass
        finally:
            self.current_run_id = None
            db.close()
    
    def run(self):
        """Main worker loop."""
        print(f"[WORKER] Starting main loop...")
        
        while self.running:
            try:
                db = self._get_db()
                config = self._get_config(db)
                
                if not config.worker_enabled:
                    # Worker disabled, sleep and check again
                    db.close()
                    asyncio.run(asyncio.sleep(5))
                    continue
                
                # Try to claim a run
                run_id = self._claim_run(db, config)
                
                if run_id:
                    db.close()  # Close claiming session, process in new session
                    self.process_run(run_id)
                else:
                    # No runs available, sleep
                    db.close()
                    asyncio.run(asyncio.sleep(config.worker_poll_interval_seconds))
                    
            except Exception as e:
                print(f"[WORKER] Error in main loop: {e}")
                asyncio.run(asyncio.sleep(5))
        
        print("[WORKER] Worker loop ended")


def main():
    """Entry point."""
    worker = DebateWorker()
    worker.run()


if __name__ == "__main__":
    main()
