#!/usr/bin/env python3
"""SysPulse - Live Process & Resource Watchdog.

Polls system metrics with psutil, flags processes that stay above a CPU or
memory threshold for N consecutive ticks, logs spikes to SQLite, and renders
a live terminal dashboard with rich.

Usage:
    python syspulse.py                      # run the live dashboard
    python syspulse.py --cpu 60 --ticks 5   # custom threshold / streak length
    python syspulse.py --history 20         # query logged spikes and exit
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    interval: float = 1.0        # seconds between polls
    cpu_threshold: float = 80.0  # % CPU considered a spike
    mem_threshold: float = 50.0  # % RAM considered a spike
    ticks: int = 3               # consecutive ticks above threshold to flag
    top_n: int = 10              # processes shown in the dashboard
    normalize: bool = False      # divide per-process CPU by core count
    db_path: str = "syspulse.db"
    retention_days: int = 30     # prune spike rows older than this


def parse_args() -> tuple[Config, int | None]:
    p = argparse.ArgumentParser(description="SysPulse - Live Process & Resource Watchdog")
    p.add_argument("--interval", type=float, default=1.0, help="poll interval in seconds")
    p.add_argument("--cpu", type=float, default=80.0, help="CPU spike threshold (%%)")
    p.add_argument("--mem", type=float, default=50.0, help="memory spike threshold (%%)")
    p.add_argument("--ticks", type=int, default=3, help="consecutive ticks required to flag")
    p.add_argument("--top", type=int, default=10, help="number of top processes to show")
    p.add_argument("--normalize", action="store_true",
                   help="express process CPU as share of all cores (max 100%%)")
    p.add_argument("--db", default="syspulse.db", help="SQLite database path")
    p.add_argument("--retention-days", type=int, default=30, help="days of history to keep")
    p.add_argument("--history", type=int, metavar="N",
                   help="print the N most recent logged spikes plus top offenders, then exit")
    a = p.parse_args()
    cfg = Config(a.interval, a.cpu, a.mem, a.ticks, a.top, a.normalize, a.db, a.retention_days)
    return cfg, a.history


# --------------------------------------------------------------------------- #
# Storage (SQLite)
# --------------------------------------------------------------------------- #
class SpikeStore:
    def __init__(self, path: str, retention_days: int):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS spikes (
                   id        INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts        TEXT NOT NULL,
                   pid       INTEGER NOT NULL,
                   name      TEXT,
                   username  TEXT,
                   metric    TEXT NOT NULL,
                   value     REAL NOT NULL,
                   threshold REAL NOT NULL,
                   streak    INTEGER NOT NULL
               )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_spikes_ts ON spikes(ts)")
        self.conn.commit()
        self.prune(retention_days)

    def log(self, pid: int, name: str, username: str | None, metric: str,
            value: float, threshold: float, streak: int) -> str:
        ts = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO spikes (ts, pid, name, username, metric, value, threshold, streak) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ts, pid, name, username, metric, round(value, 2), threshold, streak),
        )
        self.conn.commit()
        return ts

    def prune(self, days: int) -> None:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        self.conn.execute("DELETE FROM spikes WHERE ts < ?", (cutoff,))
        self.conn.commit()

    def recent(self, limit: int) -> list[tuple]:
        cur = self.conn.execute(
            "SELECT ts, pid, name, metric, value, streak FROM spikes ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()

    def top_offenders(self, limit: int) -> list[tuple]:
        cur = self.conn.execute(
            "SELECT name, metric, COUNT(*) AS events, ROUND(MAX(value),1) AS peak "
            "FROM spikes GROUP BY name, metric ORDER BY events DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()

    def close(self) -> None:
        self.conn.close()


# --------------------------------------------------------------------------- #
# Spike detection
# --------------------------------------------------------------------------- #
class SpikeTracker:
    """Counts consecutive ticks each PID has been above a threshold."""

    def __init__(self, threshold: float, ticks: int):
        self.threshold = threshold
        self.ticks = ticks
        self.streaks: dict[int, int] = {}

    def update(self, values: dict[int, float]) -> list[int]:
        """Feed this tick's {pid: value}. Returns PIDs that just crossed the streak limit."""
        newly_flagged: list[int] = []
        for pid, value in values.items():
            if value > self.threshold:
                self.streaks[pid] = self.streaks.get(pid, 0) + 1
                if self.streaks[pid] == self.ticks:  # fire once per spike episode
                    newly_flagged.append(pid)
            else:
                self.streaks.pop(pid, None)
        for pid in set(self.streaks) - set(values):  # process disappeared
            del self.streaks[pid]
        return newly_flagged

    def streak(self, pid: int) -> int:
        return self.streaks.get(pid, 0)

    def is_flagged(self, pid: int) -> bool:
        return self.streak(pid) >= self.ticks


# --------------------------------------------------------------------------- #
# Metric collection
# --------------------------------------------------------------------------- #
class SystemSampler:
    """Collects system-wide metrics; disk/net are converted to per-second rates."""

    def __init__(self) -> None:
        self._last_time = time.monotonic()
        self._last_disk = self._disk()
        self._last_net = psutil.net_io_counters()
        psutil.cpu_percent(None)  # prime the counter (first call returns 0.0)

    @staticmethod
    def _disk():
        try:
            return psutil.disk_io_counters()
        except Exception:  # some platforms/containers have no disk counters
            return None

    @staticmethod
    def _connections() -> int | None:
        try:
            conns = psutil.net_connections(kind="inet")
            return sum(1 for c in conns if c.status == psutil.CONN_ESTABLISHED)
        except (psutil.AccessDenied, PermissionError, OSError):
            return None  # e.g. macOS without root

    def sample(self) -> dict:
        now = time.monotonic()
        dt = max(now - self._last_time, 1e-6)
        disk, net = self._disk(), psutil.net_io_counters()

        disk_r = disk_w = 0.0
        if disk and self._last_disk:
            disk_r = (disk.read_bytes - self._last_disk.read_bytes) / dt
            disk_w = (disk.write_bytes - self._last_disk.write_bytes) / dt
        net_up = (net.bytes_sent - self._last_net.bytes_sent) / dt
        net_down = (net.bytes_recv - self._last_net.bytes_recv) / dt

        self._last_time, self._last_disk, self._last_net = now, disk, net
        mem = psutil.virtual_memory()
        return {
            "cpu": psutil.cpu_percent(None),
            "mem_percent": mem.percent,
            "mem_used": mem.used,
            "mem_total": mem.total,
            "disk_r": disk_r,
            "disk_w": disk_w,
            "net_up": net_up,
            "net_down": net_down,
            "conns": self._connections(),
        }


def scan_processes(normalize: bool) -> list[dict]:
    """Return one dict per readable process, skipping ones that vanish or deny access."""
    cores = psutil.cpu_count() or 1
    procs: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = proc.info
            if info["pid"] == 0:  # Windows "System Idle Process" skews the numbers
                continue
            cpu = info["cpu_percent"] or 0.0
            if normalize:
                cpu /= cores
            procs.append({
                "pid": info["pid"],
                "name": info["name"] or "?",
                "cpu": cpu,
                "mem": info["memory_percent"] or 0.0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return procs


def username_of(pid: int) -> str | None:
    try:
        return psutil.Process(pid).username()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


# --------------------------------------------------------------------------- #
# Dashboard rendering
# --------------------------------------------------------------------------- #
def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:6.1f} {unit}"
        n /= 1024
    return f"{n:6.1f} PB"


def bar(pct: float, width: int = 30) -> Text:
    pct = max(0.0, min(100.0, pct))
    filled = int(width * pct / 100)
    color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
    t = Text()
    t.append("█" * filled, style=color)
    t.append("░" * (width - filled), style="grey37")
    t.append(f" {pct:5.1f}%")
    return t


def render_system(m: dict, cfg: Config) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()
    grid.add_row("CPU", bar(m["cpu"]))
    grid.add_row("RAM", Text.assemble(
        bar(m["mem_percent"]),
        f"  {human_bytes(m['mem_used']).strip()} / {human_bytes(m['mem_total']).strip()}"))
    grid.add_row("Disk", f"read {human_bytes(m['disk_r'])}/s   write {human_bytes(m['disk_w'])}/s")
    grid.add_row("Net", f"up {human_bytes(m['net_up'])}/s   down {human_bytes(m['net_down'])}/s")
    conns = "n/a (needs elevated privileges)" if m["conns"] is None else str(m["conns"])
    grid.add_row("Conns", f"{conns} established")
    return Panel(grid, title="[bold]System[/bold]", border_style="cyan")


def render_processes(procs: list[dict], cpu_t: SpikeTracker, mem_t: SpikeTracker,
                     cfg: Config) -> Panel:
    table = Table(expand=True, header_style="bold magenta")
    table.add_column("PID", justify="right")
    table.add_column("Name")
    table.add_column("CPU %", justify="right")
    table.add_column("MEM %", justify="right")
    table.add_column("Streak", justify="right")
    table.add_column("Status")

    top = sorted(procs, key=lambda p: (p["cpu"], p["mem"]), reverse=True)[: cfg.top_n]
    for p in top:
        pid = p["pid"]
        flagged = cpu_t.is_flagged(pid) or mem_t.is_flagged(pid)
        streak = max(cpu_t.streak(pid), mem_t.streak(pid))
        if flagged:
            status, style = "[bold red]⚠ SPIKE[/bold red]", "red"
        elif streak:
            status, style = f"[yellow]watching {streak}/{cfg.ticks}[/yellow]", None
        else:
            status, style = "[green]ok[/green]", None
        table.add_row(str(pid), p["name"], f"{p['cpu']:.1f}", f"{p['mem']:.1f}",
                      str(streak) if streak else "-", status, style=style)
    return Panel(table, title=f"[bold]Top {cfg.top_n} processes[/bold]", border_style="magenta")


def render_alerts(alerts: deque) -> Panel:
    table = Table(expand=True, header_style="bold yellow")
    for col in ("Time", "PID", "Process", "Metric", "Value", "Streak"):
        table.add_column(col)
    if not alerts:
        table.add_row("-", "-", "no spikes recorded yet", "-", "-", "-")
    for ts, pid, name, metric, value, streak in alerts:
        table.add_row(ts, str(pid), name, metric, f"{value:.1f}%", str(streak))
    return Panel(table, title="[bold]Recent spike events[/bold]", border_style="yellow")


def render(m, procs, cpu_t, mem_t, alerts, cfg) -> Group:
    footer = Text(
        f" CPU > {cfg.cpu_threshold:g}% | MEM > {cfg.mem_threshold:g}% for {cfg.ticks} ticks "
        f"| interval {cfg.interval:g}s | db: {cfg.db_path} | Ctrl+C to quit",
        style="dim")
    return Group(render_system(m, cfg), render_processes(procs, cpu_t, mem_t, cfg),
                 render_alerts(alerts), footer)


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def run_monitor(cfg: Config) -> None:
    store = SpikeStore(cfg.db_path, cfg.retention_days)
    sampler = SystemSampler()
    cpu_tracker = SpikeTracker(cfg.cpu_threshold, cfg.ticks)
    mem_tracker = SpikeTracker(cfg.mem_threshold, cfg.ticks)
    alerts: deque = deque(reversed(store.recent(6)), maxlen=6)
    alerts = deque(sorted(alerts, key=lambda r: r[0]), maxlen=6)  # oldest -> newest

    scan_processes(cfg.normalize)  # prime per-process cpu_percent counters
    time.sleep(cfg.interval)

    next_tick = time.monotonic()
    last_prune = time.monotonic()
    try:
        with Live(console=console, screen=True, refresh_per_second=4) as live:
            while True:
                metrics = sampler.sample()
                procs = scan_processes(cfg.normalize)
                by_pid = {p["pid"]: p for p in procs}

                for tracker, key, label, thr in (
                    (cpu_tracker, "cpu", "cpu", cfg.cpu_threshold),
                    (mem_tracker, "mem", "memory", cfg.mem_threshold),
                ):
                    newly = tracker.update({p["pid"]: p[key] for p in procs})
                    for pid in newly:
                        p = by_pid[pid]
                        ts = store.log(pid, p["name"], username_of(pid), label,
                                       p[key], thr, tracker.streak(pid))
                        alerts.append((ts, pid, p["name"], label, p[key], tracker.streak(pid)))

                live.update(render(metrics, procs, cpu_tracker, mem_tracker, alerts, cfg))

                if time.monotonic() - last_prune > 3600:  # hourly retention cleanup
                    store.prune(cfg.retention_days)
                    last_prune = time.monotonic()

                # drift-free fixed-interval sleep
                next_tick += cfg.interval
                time.sleep(max(0.0, next_tick - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        store.close()
        console.print("[bold]SysPulse stopped.[/bold]")


def show_history(cfg: Config, limit: int) -> None:
    store = SpikeStore(cfg.db_path, cfg.retention_days)
    recent = Table(title=f"Last {limit} spike events", header_style="bold yellow")
    for col in ("Time", "PID", "Process", "Metric", "Value %", "Streak"):
        recent.add_column(col)
    for ts, pid, name, metric, value, streak in store.recent(limit):
        recent.add_row(ts, str(pid), name or "?", metric, f"{value:.1f}", str(streak))
    console.print(recent)

    top = Table(title="Top offenders", header_style="bold magenta")
    for col in ("Process", "Metric", "Events", "Peak %"):
        top.add_column(col)
    for name, metric, events, peak in store.top_offenders(10):
        top.add_row(name or "?", metric, str(events), str(peak))
    console.print(top)
    store.close()


def main() -> None:
    cfg, history = parse_args()
    if history is not None:
        show_history(cfg, history)
    else:
        run_monitor(cfg)


if __name__ == "__main__":
    main()
